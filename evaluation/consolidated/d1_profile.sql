-- D1 (DIAGNOSTICS_PROTOCOL.md): an instrumented copy of morlet_search_envelope_probes (pg_morlet 0.3.1,
-- main 68594d7, sql/streams.sql) for the unfiltered probe path. Same statements and settings as the
-- product function; adds clock_timestamp() stamps and candidate counts. Lives in schema diag only.
CREATE SCHEMA IF NOT EXISTS diag;

CREATE OR REPLACE FUNCTION diag.probe_profile(collection regclass, source_recording_id text, interval_start bigint,
  interval_end bigint, metadata_filter jsonb DEFAULT '{}'::jsonb, top_k integer DEFAULT 10, max_lag_fraction float8 DEFAULT 0.075,
  per_probe integer DEFAULT 50, probe_step integer DEFAULT 5, exact_threshold integer DEFAULT 5000)
RETURNS jsonb LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; env regclass; probes regclass; vschema text; q float8[]; width integer; lag integer;
  l integer; lags integer[]; ids text[]:=ARRAY[]::text[]; starts bigint[]:=ARRAY[]::bigint[]; got_ids text[];
  got_starts bigint[]; eligible text[]; eligible_windows bigint; hit record; picked_ids text[]:=ARRAY[]::text[];
  picked_starts bigint[]:=ARRAY[]::bigint[]; picked_ends bigint[]:=ARRAY[]::bigint[]; picked integer:=0; idx integer;
  blocked boolean; t0 timestamptz; t timestamptz; ms jsonb:='{}'; probe_ms float8[]:=ARRAY[]::float8[];
  probe_rows integer[]:=ARRAY[]::integer[]; reranked integer:=0; considered integer:=0; excluded_source integer:=0;
  suppressed integer:=0; hits jsonb:='[]';
BEGIN
  t0:=clock_timestamp(); t:=t0;
  SELECT * INTO source FROM public.morlet_read_interval(collection,source_recording_id,interval_start,interval_end);
  env:=public.morlet_envelope_relation(collection);
  width:=cardinality(source.samples);
  probes:=public.morlet_envelope_probe_relation(collection,width);
  SELECT e.extnamespace::regnamespace::text INTO vschema FROM pg_extension e WHERE e.extname='vector';
  ms:=ms||jsonb_build_object('read_interval',extract(epoch FROM clock_timestamp()-t)*1000); t:=clock_timestamp();
  EXECUTE format('SELECT array_agg(r.recording_id) FROM %s r WHERE r.sample_rate_hz=$1 AND r.channel=$2 AND r.unit=$3
    AND r.metadata @> $4',collection) INTO eligible USING source.sample_rate_hz,source.channel,source.unit,metadata_filter;
  eligible:=coalesce(eligible,ARRAY[]::text[]);
  EXECUTE format('SELECT count(*) FROM (SELECT 1 FROM %s e WHERE e.recording_id=ANY($1) AND e.window_size=$2 LIMIT $3) x',env)
    INTO eligible_windows USING eligible,width,exact_threshold::bigint+1;
  ms:=ms||jsonb_build_object('routing',extract(epoch FROM clock_timestamp()-t)*1000); t:=clock_timestamp();
  IF eligible_windows<=exact_threshold THEN
    RETURN jsonb_build_object('path','exact');
  END IF;
  q:=public.morlet_envelope(source.samples);
  lag:=floor(max_lag_fraction*cardinality(q));
  lags:=ARRAY(SELECT generate_series(-lag,lag,probe_step));
  IF lags[cardinality(lags)]<>lag THEN lags:=lags||lag; END IF;
  ms:=ms||jsonb_build_object('query_envelope',extract(epoch FROM clock_timestamp()-t)*1000); t:=clock_timestamp();
  PERFORM set_config('hnsw.ef_search',greatest(per_probe,100)::text,true);
  PERFORM set_config('hnsw.iterative_scan','relaxed_order',true);
  FOREACH l IN ARRAY lags LOOP
    EXECUTE format('SELECT array_agg(p.recording_id),array_agg(p.start_sample) FROM (SELECT recording_id,start_sample
      FROM %s WHERE recording_id=ANY($3) ORDER BY v OPERATOR(%I.<#>) $1::%I.halfvec LIMIT $2) p',probes,vschema,vschema)
      INTO got_ids,got_starts USING public.morlet_envelope_probe(q,l),per_probe,eligible;
    probe_ms:=probe_ms||(extract(epoch FROM clock_timestamp()-t)*1000); t:=clock_timestamp();
    probe_rows:=probe_rows||coalesce(cardinality(got_ids),0);
    ids:=ids||coalesce(got_ids,ARRAY[]::text[]);
    starts:=starts||coalesce(got_starts,ARRAY[]::bigint[]);
  END LOOP;
  PERFORM set_config('hnsw.ef_search','40',true);
  PERFORM set_config('hnsw.iterative_scan','off',true);
  ms:=ms||jsonb_build_object('probes_total',(SELECT sum(x) FROM unnest(probe_ms) x)); t:=clock_timestamp();
  -- re-rank: the product statement, materialized so that its time is measured separately from suppression
  CREATE TEMP TABLE IF NOT EXISTS diag_ranked(recording_id text, start_sample bigint, end_sample bigint, distance float8,
    metadata jsonb, o bigint) ON COMMIT DROP;
  TRUNCATE diag_ranked;
  EXECUTE format('INSERT INTO diag_ranked SELECT x.*,row_number() OVER (ORDER BY x.distance,x.recording_id,x.start_sample)
    FROM (SELECT e.recording_id,e.start_sample,e.start_sample+e.window_size AS end_sample,
      public.morlet_envelope_distance($1,e.envelope,$2) AS distance,r.metadata
    FROM (SELECT DISTINCT * FROM unnest($3::text[],$4::bigint[]) c(recording_id,start_sample)) c
    JOIN %s e ON e.recording_id=c.recording_id AND e.start_sample=c.start_sample AND e.window_size=$5
    JOIN %s r ON r.recording_id=e.recording_id
    WHERE r.sample_rate_hz=$6 AND r.channel=$7 AND r.unit=$8 AND r.metadata @> $9) x',env,collection)
    USING q,lag,ids,starts,width,source.sample_rate_hz,source.channel,source.unit,metadata_filter;
  GET DIAGNOSTICS reranked = ROW_COUNT;
  ms:=ms||jsonb_build_object('rerank',extract(epoch FROM clock_timestamp()-t)*1000); t:=clock_timestamp();
  FOR hit IN SELECT * FROM diag_ranked ORDER BY o LOOP
    considered:=considered+1;
    IF hit.recording_id=source_recording_id AND hit.start_sample<interval_end AND hit.end_sample>interval_start THEN
      excluded_source:=excluded_source+1; CONTINUE;
    END IF;
    blocked:=false;
    IF picked>0 THEN
      FOR idx IN 1..picked LOOP
        IF picked_ids[idx]=hit.recording_id AND picked_starts[idx]<hit.end_sample AND picked_ends[idx]>hit.start_sample THEN
          blocked:=true; EXIT;
        END IF;
      END LOOP;
    END IF;
    IF blocked THEN suppressed:=suppressed+1; CONTINUE; END IF;
    picked:=picked+1;
    picked_ids:=array_append(picked_ids,hit.recording_id);
    picked_starts:=array_append(picked_starts,hit.start_sample);
    picked_ends:=array_append(picked_ends,hit.end_sample);
    hits:=hits||jsonb_build_array(jsonb_build_array(hit.recording_id,hit.start_sample));
    EXIT WHEN picked=top_k;
  END LOOP;
  ms:=ms||jsonb_build_object('suppression',extract(epoch FROM clock_timestamp()-t)*1000,
                             'total',extract(epoch FROM clock_timestamp()-t0)*1000);
  RETURN jsonb_build_object('path','probe','ms',ms,'probe_ms',to_jsonb(probe_ms),'probe_rows',to_jsonb(probe_rows),
    'union_rows',cardinality(ids),'unique_candidates',(SELECT count(*) FROM (SELECT DISTINCT * FROM unnest(ids,starts)) u),
    'reranked',reranked,'considered',considered,'excluded_source',excluded_source,'suppressed',suppressed,
    'returned',picked,'remaining_unread',reranked-considered,'eligible_windows_counted',eligible_windows,'hits',hits);
END $$;
