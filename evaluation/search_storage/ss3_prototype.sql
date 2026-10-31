-- SS3 prototype (scratch databases only): filter-aware probe search.
CREATE OR REPLACE FUNCTION public.morlet_search_envelope_probes_pi(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,metadata_filter jsonb DEFAULT '{}'::jsonb,top_k integer DEFAULT 10,
  max_lag_fraction float8 DEFAULT 0.075,exclude_source boolean DEFAULT true,suppress_overlaps boolean DEFAULT true,
  per_probe integer DEFAULT 50,probe_step integer DEFAULT 5)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; env regclass; probes regclass; vschema text; q float8[]; width integer; lag integer;
  l integer; lags integer[]; ids text[]:=ARRAY[]::text[]; starts bigint[]:=ARRAY[]::bigint[]; got_ids text[];
  got_starts bigint[]; ef text; its text; eligible text[]; hit record; picked_ids text[]:=ARRAY[]::text[]; picked_starts bigint[]:=ARRAY[]::bigint[];
  picked_ends bigint[]:=ARRAY[]::bigint[]; picked integer:=0; idx integer; blocked boolean;
BEGIN
  IF top_k NOT BETWEEN 1 AND 100 OR jsonb_typeof(metadata_filter)<>'object' OR NOT (max_lag_fraction BETWEEN 0 AND 0.5)
    OR per_probe NOT BETWEEN 1 AND 1000 OR probe_step<1 THEN
    RAISE EXCEPTION 'invalid top_k (1..100), metadata filter, max_lag_fraction (0..0.5), per_probe (1..1000) or probe_step (>= 1)';
  END IF;
  SELECT * INTO source FROM public.morlet_read_interval(collection,source_recording_id,interval_start,interval_end);
  env:=public.morlet_envelope_relation(collection);
  width:=cardinality(source.samples);
  probes:=public.morlet_envelope_probe_relation(collection,width);
  IF probes IS NULL THEN
    RAISE EXCEPTION 'probe search is not enabled for window_size % (morlet_enable_envelope_probes)',width;
  END IF;
  SELECT e.extnamespace::regnamespace::text INTO vschema FROM pg_extension e WHERE e.extname='vector';
  q:=public.morlet_envelope(source.samples);
  lag:=floor(max_lag_fraction*cardinality(q));
  lags:=ARRAY(SELECT generate_series(-lag,lag,probe_step));
  IF lags[cardinality(lags)]<>lag THEN lags:=lags||lag; END IF;
  -- The index returns at most hnsw.ef_search rows per scan. Use max(per_probe, 100), as in
  -- the evaluated configuration, for this call only.
  ef:=current_setting('hnsw.ef_search',true);
  PERFORM set_config('hnsw.ef_search',greatest(per_probe,100)::text,true);
  -- SS3 prototype: restrict each probe scan to eligible recordings; iterative scan finds per_probe of them.
  EXECUTE format('SELECT array_agg(r.recording_id) FROM %s r WHERE r.sample_rate_hz=$1 AND r.channel=$2 AND r.unit=$3
    AND r.metadata @> $4',collection) INTO eligible USING source.sample_rate_hz,source.channel,source.unit,metadata_filter;
  its:=current_setting('hnsw.iterative_scan',true);
  PERFORM set_config('hnsw.iterative_scan','relaxed_order',true);
  FOREACH l IN ARRAY lags LOOP
    EXECUTE format('SELECT array_agg(p.recording_id),array_agg(p.start_sample) FROM (SELECT recording_id,start_sample
      FROM %s WHERE recording_id = ANY($3) ORDER BY v OPERATOR(%I.<#>) $1::%I.halfvec LIMIT $2) p',probes,vschema,vschema)
      INTO got_ids,got_starts USING public.morlet_envelope_probe(q,l),per_probe,coalesce(eligible,ARRAY[]::text[]);
    ids:=ids||coalesce(got_ids,ARRAY[]::text[]);
    starts:=starts||coalesce(got_starts,ARRAY[]::bigint[]);
  END LOOP;
  PERFORM set_config('hnsw.ef_search',coalesce(nullif(ef,''),'40'),true);
  PERFORM set_config('hnsw.iterative_scan',coalesce(nullif(its,''),'off'),true);
  FOR hit IN EXECUTE format('SELECT e.recording_id,e.start_sample,e.start_sample+e.window_size AS end_sample,
      public.morlet_envelope_distance($1,e.envelope,$2) AS distance,r.metadata
    FROM (SELECT DISTINCT * FROM unnest($3::text[],$4::bigint[]) c(recording_id,start_sample)) c
    JOIN %s e ON e.recording_id=c.recording_id AND e.start_sample=c.start_sample AND e.window_size=$5
    JOIN %s r ON r.recording_id=e.recording_id
    WHERE r.sample_rate_hz=$6 AND r.channel=$7 AND r.unit=$8 AND r.metadata @> $9
      AND (NOT $10 OR e.recording_id<>$11 OR e.start_sample+e.window_size<=$12 OR e.start_sample>=$13)
    ORDER BY distance,e.recording_id,e.start_sample',env,collection)
    USING q,lag,ids,starts,width,source.sample_rate_hz,source.channel,source.unit,metadata_filter,exclude_source,
      source_recording_id,interval_start,interval_end
  LOOP
    blocked:=false;
    IF suppress_overlaps AND picked>0 THEN
      FOR idx IN 1..picked LOOP
        IF picked_ids[idx]=hit.recording_id AND picked_starts[idx]<hit.end_sample AND picked_ends[idx]>hit.start_sample THEN
          blocked:=true; EXIT;
        END IF;
      END LOOP;
    END IF;
    IF blocked THEN CONTINUE; END IF;
    RETURN QUERY SELECT hit.recording_id,hit.start_sample,hit.end_sample,hit.distance,hit.metadata;
    picked:=picked+1;
    picked_ids:=array_append(picked_ids,hit.recording_id);
    picked_starts:=array_append(picked_starts,hit.start_sample);
    picked_ends:=array_append(picked_ends,hit.end_sample);
    EXIT WHEN picked=top_k;
  END LOOP;
END $$;
