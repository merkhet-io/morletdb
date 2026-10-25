-- These tables are caller-owned. All functions use invoker privileges.
CREATE FUNCTION public.morlet_create_stream_collection(schema_name text,collection_name text)
RETURNS regclass LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  IF octet_length(collection_name)>46 OR collection_name='' THEN
    RAISE EXCEPTION 'stream collection name must contain 1..46 bytes';
  END IF;
  EXECUTE format('CREATE TABLE %I.%I (
    recording_id text PRIMARY KEY, sample_rate_hz float8 NOT NULL,
    channel text NOT NULL, unit text NOT NULL,
    metadata jsonb NOT NULL CHECK(jsonb_typeof(metadata)=''object''),
    checkpoint jsonb NOT NULL)',schema_name,collection_name);
  EXECUTE format('CREATE TABLE %I.%I (
    recording_id text NOT NULL REFERENCES %I.%I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    samples float8[] NOT NULL CHECK(array_ndims(samples)=1 AND array_lower(samples,1)=1 AND public.morlet_valid_chunk(samples)),
    PRIMARY KEY(recording_id,start_sample))',schema_name,collection_name||'_chunks',schema_name,collection_name);
  EXECUTE format('CREATE TABLE %I.%I (
    recording_id text NOT NULL REFERENCES %I.%I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),features float8[] NOT NULL,
    PRIMARY KEY(recording_id,start_sample))',schema_name,collection_name||'_segments',schema_name,collection_name);
  RETURN format('%I.%I',schema_name,collection_name)::regclass;
END $$;

CREATE FUNCTION public.morlet_stream_relations(collection regclass)
RETURNS TABLE(chunks regclass,segments regclass)
LANGUAGE sql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT format('%I.%I',n.nspname,c.relname||'_chunks')::regclass,
    format('%I.%I',n.nspname,c.relname||'_segments')::regclass
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection
$$;

CREATE FUNCTION public.morlet_start_recording(collection regclass,recording_id text,
  sample_rate_hz float8,channel text,unit text,metadata jsonb DEFAULT '{}'::jsonb,
  window_size integer DEFAULT 512,stride integer DEFAULT 256,normalization text DEFAULT 'zscore',
  extractor text DEFAULT 'morlet-energy-v1')
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE initial jsonb;
BEGIN
  -- The checkpoint records the explicitly selected representation version.
  initial:=public.morlet_chunk_checkpoint(sample_rate_hz,window_size,stride,normalization,extractor);
  EXECUTE format('INSERT INTO %s(recording_id,sample_rate_hz,channel,unit,metadata,checkpoint) VALUES ($1,$2,$3,$4,$5,$6)',collection)
    USING recording_id,sample_rate_hz,channel,unit,metadata,initial;
END $$;

CREATE FUNCTION public.morlet_append_chunk(collection regclass,recording_id text,start_sample bigint,samples float8[])
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE state jsonb; previous float8[]; result jsonb; relations record; updated_rows bigint;
BEGIN
  IF array_ndims(samples) IS DISTINCT FROM 1 OR array_lower(samples,1) IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'chunk must be a one-dimensional array starting at 1';
  END IF;
  PERFORM public.morlet_valid_chunk(samples);
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  -- Serializes concurrent appenders of the same recording, under READ COMMITTED.
  EXECUTE format('SELECT checkpoint FROM %s WHERE recording_id=$1 FOR UPDATE',collection) INTO state USING recording_id;
  IF state IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  EXECUTE format('SELECT samples FROM %s WHERE recording_id=$1 AND start_sample=$2',relations.chunks)
    INTO previous USING recording_id,start_sample;
  IF previous IS NOT NULL THEN
    IF previous IS DISTINCT FROM samples THEN RAISE EXCEPTION 'conflicting replay at chunk offset'; END IF;
    RETURN (state->>'end')::bigint;
  END IF;
  result:=public.morlet_chunk_step(state,start_sample,samples);
  EXECUTE format('INSERT INTO %s(recording_id,start_sample,samples) VALUES ($1,$2,$3)',relations.chunks)
    USING recording_id,start_sample,samples;
  EXECUTE format('INSERT INTO %s(recording_id,start_sample,features)
    SELECT $1,(s->>''start_sample'')::bigint,ARRAY(SELECT jsonb_array_elements_text(s->''features'')::float8)
    FROM jsonb_array_elements($2->''segments'') s',relations.segments) USING recording_id,result;
  EXECUTE format('UPDATE %s SET checkpoint=$2 WHERE recording_id=$1',collection)
    USING recording_id,result->'checkpoint';
  GET DIAGNOSTICS updated_rows = ROW_COUNT;
  IF updated_rows<>1 THEN RAISE EXCEPTION 'checkpoint update was not permitted'; END IF;
  RETURN (result->'checkpoint'->>'end')::bigint;
END $$;

CREATE FUNCTION public.morlet_stream_status(collection regclass,recording_id text)
RETURNS TABLE(received_samples bigint,next_window_start bigint,indexed_segments bigint,extractor_version text)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE relations record;
BEGIN
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  RETURN QUERY EXECUTE format('SELECT (r.checkpoint->>''end'')::bigint,(r.checkpoint->>''next_start'')::bigint,
    (SELECT count(*) FROM %s s WHERE s.recording_id=r.recording_id),r.checkpoint->>''extractor_version''
    FROM %s r WHERE r.recording_id=$1',relations.segments,collection) USING recording_id;
END $$;

CREATE FUNCTION public.morlet_stream_candidates(collection regclass,query_samples float8[],query_rate_hz float8,
  query_channel text,query_unit text,metadata_filter jsonb DEFAULT '{}'::jsonb,
  normalization text DEFAULT 'zscore',max_candidates integer DEFAULT 4096,
  extractor text DEFAULT 'morlet-energy-v1')
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,extractor_version text,metadata jsonb)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE relations record; query_features float8[]; width integer; candidates bigint; incompatible boolean;
BEGIN
  IF array_ndims(query_samples) IS DISTINCT FROM 1 OR array_lower(query_samples,1) IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'query must be a one-dimensional array starting at 1';
  END IF;
  IF max_candidates NOT BETWEEN 1 AND 1000000 THEN
    RAISE EXCEPTION 'candidate budget must be 1..1000000';
  END IF;
  width:=cardinality(query_samples);
  IF width NOT BETWEEN 512 AND 4096 OR jsonb_typeof(metadata_filter)<>'object' THEN
    RAISE EXCEPTION 'invalid query size or metadata filter';
  END IF;
  query_features:=public.morlet_features(public.morlet_normalize(query_samples,normalization),query_rate_hz,extractor);
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  -- Materialize compatibility once per visible recording. The checkpoint tail
  -- may be TOASTed; decoding it for every joined segment dominates small scans.
  EXECUTE format('WITH eligible AS MATERIALIZED (
    SELECT r.recording_id,((r.checkpoint->>''window'')::integer IS DISTINCT FROM $1
      OR r.checkpoint->>''normalization'' IS DISTINCT FROM $2
      OR r.checkpoint->>''extractor_version'' IS DISTINCT FROM $7) AS invalid
    FROM %2$s r WHERE r.sample_rate_hz=$3 AND r.channel=$4 AND r.unit=$5 AND r.metadata @> $6
      AND EXISTS(SELECT 1 FROM %1$s s WHERE s.recording_id=r.recording_id))
    SELECT coalesce(sum((SELECT count(*) FROM %1$s s WHERE s.recording_id=e.recording_id)),0)::bigint,
      coalesce(bool_or(e.invalid),false) FROM eligible e',relations.segments,collection)
    INTO candidates,incompatible USING width,normalization,query_rate_hz,query_channel,query_unit,metadata_filter,extractor;
  IF incompatible THEN RAISE EXCEPTION 'incompatible stream representation'; END IF;
  IF candidates>max_candidates THEN
    RAISE EXCEPTION 'stream search exceeds % segments; narrow the metadata filter',max_candidates;
  END IF;
  RETURN QUERY EXECUTE format('SELECT s.recording_id,s.start_sample,s.start_sample+$1,
    public.morlet_feature_vector_distance(s.features,$2) AS distance,$7,r.metadata
    FROM %s s JOIN %s r USING(recording_id)
    WHERE r.sample_rate_hz=$3 AND r.channel=$4 AND r.unit=$5 AND r.metadata @> $6
    ORDER BY distance,s.recording_id,s.start_sample',relations.segments,collection)
    USING width,query_features,query_rate_hz,query_channel,query_unit,metadata_filter,extractor;
END $$;

-- Compatibility entry point delegates all eligibility/scoring to one path.
CREATE FUNCTION public.morlet_search_streams(collection regclass,query_samples float8[],query_rate_hz float8,
  query_channel text,query_unit text,metadata_filter jsonb DEFAULT '{}'::jsonb,
  normalization text DEFAULT 'zscore',top_k integer DEFAULT 10,max_candidates integer DEFAULT 4096,
  extractor text DEFAULT 'morlet-energy-v1')
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,extractor_version text)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  IF top_k NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'invalid top_k'; END IF;
  RETURN QUERY SELECT h.recording_id,h.start_sample,h.end_sample,h.distance,h.extractor_version
    FROM public.morlet_stream_candidates(collection,query_samples,query_rate_hz,query_channel,
      query_unit,metadata_filter,normalization,max_candidates,extractor) h
    ORDER BY h.distance,h.recording_id,h.start_sample LIMIT top_k;
END $$;

-- Idempotent recording creation for resumable importers. Existing acquisition
-- metadata/specification must match; checkpoint progress is deliberately ignored.
CREATE FUNCTION public.morlet_ensure_recording(collection regclass,recording_id text,
  sample_rate_hz float8,channel text,unit text,metadata jsonb DEFAULT '{}'::jsonb,
  window_size integer DEFAULT 512,stride integer DEFAULT 256,normalization text DEFAULT 'zscore',
  extractor text DEFAULT 'morlet-energy-v1')
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE initial jsonb; existing record;
BEGIN
  initial:=public.morlet_chunk_checkpoint(sample_rate_hz,window_size,stride,normalization,extractor);
  EXECUTE format('INSERT INTO %s(recording_id,sample_rate_hz,channel,unit,metadata,checkpoint)
    VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT(recording_id) DO NOTHING',collection)
    USING recording_id,sample_rate_hz,channel,unit,metadata,initial;
  EXECUTE format('SELECT sample_rate_hz,channel,unit,metadata,checkpoint FROM %s WHERE recording_id=$1 FOR UPDATE',collection)
    INTO existing USING recording_id;
  IF existing.checkpoint IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  IF existing.sample_rate_hz IS DISTINCT FROM sample_rate_hz OR existing.channel IS DISTINCT FROM channel
    OR existing.unit IS DISTINCT FROM unit OR existing.metadata IS DISTINCT FROM metadata
    OR existing.checkpoint->>'extractor_version' IS DISTINCT FROM initial->>'extractor_version'
    OR (existing.checkpoint->>'window')::integer IS DISTINCT FROM window_size
    OR (existing.checkpoint->>'stride')::integer IS DISTINCT FROM stride
    OR existing.checkpoint->>'normalization' IS DISTINCT FROM normalization THEN
    RAISE EXCEPTION 'recording metadata or extraction specification conflicts with import';
  END IF;
END $$;

CREATE TYPE public.morlet_input_chunk AS (start_sample bigint,samples float8[]);

-- One recording per batch keeps lock ordering explicit. Every element reuses the
-- validated durable append path; a later error rolls back the entire statement.
CREATE FUNCTION public.morlet_append_chunks(collection regclass,recording_id text,inputs public.morlet_input_chunk[])
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE item public.morlet_input_chunk; sample_count bigint:=0; received bigint;
BEGIN
  IF array_ndims(inputs) IS DISTINCT FROM 1 OR array_lower(inputs,1) IS DISTINCT FROM 1
    OR cardinality(inputs) NOT BETWEEN 1 AND 64 THEN
    RAISE EXCEPTION 'batch must contain 1..64 input chunks in a one-dimensional array';
  END IF;
  FOREACH item IN ARRAY inputs LOOP
    IF item.start_sample IS NULL OR item.samples IS NULL
      OR array_ndims(item.samples) IS DISTINCT FROM 1 OR array_lower(item.samples,1) IS DISTINCT FROM 1
      OR cardinality(item.samples) NOT BETWEEN 1 AND 32768 THEN
      RAISE EXCEPTION 'invalid batch chunk offset or sample array';
    END IF;
    sample_count:=sample_count+cardinality(item.samples);
  END LOOP;
  IF sample_count>262144 THEN RAISE EXCEPTION 'batch exceeds 262144 samples'; END IF;
  FOREACH item IN ARRAY inputs LOOP
    received:=public.morlet_append_chunk(collection,recording_id,item.start_sample,item.samples);
  END LOOP;
  RETURN received;
END $$;


-- Read a bounded half-open interval without exposing table internals. Both
-- parent and chunk privileges/RLS apply; missing or hidden chunks fail closed.
CREATE FUNCTION public.morlet_read_interval(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,samples float8[],
  sample_rate_hz float8,channel text,unit text,metadata jsonb,extractor_version text)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; relations record; recovered float8[]; covered bigint;
BEGIN
  IF interval_start<0 OR interval_end<=interval_start OR interval_end-interval_start>32768 THEN
    RAISE EXCEPTION 'read interval must contain 1..32768 samples at nonnegative coordinates';
  END IF;
  EXECUTE format('SELECT r.sample_rate_hz,r.channel,r.unit,r.metadata,r.checkpoint FROM %s r WHERE r.recording_id=$1',collection)
    INTO source USING source_recording_id;
  IF source.checkpoint IS NULL THEN RAISE EXCEPTION 'source recording does not exist or is not visible'; END IF;
  IF interval_end>(source.checkpoint->>'end')::bigint THEN
    RAISE EXCEPTION 'source interval exceeds recording length';
  END IF;
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  EXECUTE format('SELECT array_agg(v.value ORDER BY c.start_sample,v.ordinality),count(*)
    FROM %s c CROSS JOIN LATERAL unnest(c.samples[
      greatest(1,$2-c.start_sample+1)::integer:
      least(cardinality(c.samples)::bigint,$3-c.start_sample)::integer
    ]) WITH ORDINALITY AS v(value,ordinality)
    WHERE c.recording_id=$1 AND c.start_sample<$3
      AND c.start_sample+cardinality(c.samples)>$2',relations.chunks)
    INTO recovered,covered USING source_recording_id,interval_start,interval_end;
  IF covered<>interval_end-interval_start THEN
    RAISE EXCEPTION 'source interval is incomplete or not fully visible';
  END IF;
  RETURN QUERY SELECT source_recording_id,interval_start,interval_end,recovered,
    source.sample_rate_hz,source.channel,source.unit,source.metadata,source.checkpoint->>'extractor_version';
END $$;

-- Stored-interval search uses the shared exact candidate scorer, then
-- applies source exclusion and greedy, rank-ordered non-overlap selection BEFORE
-- top_k. Fetching only top_k first could discard valid independent matches.
CREATE FUNCTION public.morlet_search_interval(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,metadata_filter jsonb DEFAULT '{}'::jsonb,
  top_k integer DEFAULT 10,exclude_source boolean DEFAULT true,suppress_overlaps boolean DEFAULT true,
  max_candidates integer DEFAULT 4096)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,
  extractor_version text,sample_rate_hz float8,channel text,unit text,metadata jsonb)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; source_state jsonb; normalization text; extractor text; hit record;
  picked_ids text[]:=ARRAY[]::text[]; picked_starts bigint[]:=ARRAY[]::bigint[];
  picked_ends bigint[]:=ARRAY[]::bigint[]; picked integer:=0; idx integer; blocked boolean;
BEGIN
  IF interval_start<0 OR interval_end<=interval_start OR interval_end-interval_start NOT BETWEEN 512 AND 4096
    OR top_k NOT BETWEEN 1 AND 100 OR jsonb_typeof(metadata_filter)<>'object' THEN
    RAISE EXCEPTION 'invalid query interval, top_k or metadata filter';
  END IF;
  SELECT * INTO source FROM public.morlet_read_interval(collection,source_recording_id,interval_start,interval_end);
  EXECUTE format('SELECT checkpoint FROM %s WHERE recording_id=$1',collection)
    INTO source_state USING source_recording_id;
  IF (source_state->>'window')::bigint<>interval_end-interval_start THEN
    RAISE EXCEPTION 'query interval must match the stored representation window';
  END IF;
  normalization:=source_state->>'normalization';
  extractor:=source_state->>'extractor_version';
  FOR hit IN SELECT h.* FROM public.morlet_stream_candidates(collection,source.samples,
    source.sample_rate_hz,source.channel,source.unit,metadata_filter,normalization,max_candidates,extractor) h
    WHERE NOT exclude_source OR h.recording_id<>source_recording_id
      OR h.end_sample<=interval_start OR h.start_sample>=interval_end
    ORDER BY h.distance,h.recording_id,h.start_sample
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
    RETURN QUERY SELECT hit.recording_id,hit.start_sample,hit.end_sample,hit.distance,
      hit.extractor_version,source.sample_rate_hz,source.channel,source.unit,hit.metadata;
    picked:=picked+1;
    picked_ids:=array_append(picked_ids,hit.recording_id);
    picked_starts:=array_append(picked_starts,hit.start_sample);
    picked_ends:=array_append(picked_ends,hit.end_sample);
    EXIT WHEN picked=top_k;
  END LOOP;
END $$;
