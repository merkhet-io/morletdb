-- Exact envelope search screening (morlet_core::envelope_screen): a parallel aggregate that bounds each
-- stored window's distance to a query envelope in float32 and keeps every window that could be among the
-- m nearest, as a JSON array of [recording_id, start_sample]. Used by morlet_envelope_ranked.
CREATE AGGREGATE public.morlet_envelope_screen(bytea,text,bigint,bytea,integer,integer,bigint) (
  SFUNC = public.morlet_envelope_screen_step, STYPE = internal,
  COMBINEFUNC = public.morlet_envelope_screen_combine,
  SERIALFUNC = public.morlet_envelope_screen_serial, DESERIALFUNC = public.morlet_envelope_screen_deserial,
  FINALFUNC = public.morlet_envelope_screen_final, FINALFUNC_MODIFY = READ_WRITE, PARALLEL = SAFE);

-- Interval statistics over encoded chunks, per bucket of the interval (morlet_core::intervalstats): each
-- chunk's part is accumulated alone, or taken from its stored summary when the chunk lies inside the
-- interval, and merged in start order (call with ORDER BY start_sample). Used by morlet_interval_stats
-- and morlet_interval_overview.
CREATE AGGREGATE public.morlet_interval_stats_agg(bytea,bytea,bigint,integer,bigint,bigint,integer) (
  SFUNC = public.morlet_interval_stats_step, STYPE = internal,
  FINALFUNC = public.morlet_interval_stats_final, FINALFUNC_MODIFY = READ_WRITE);

-- Exact merge of chunk summaries (version 1) and roll-ups (version 2) into one roll-up (morlet_core::exactstats,
-- #219). The result does not depend on the order of rows. Used by morlet_build_rollups.
CREATE AGGREGATE public.morlet_rollup_agg(bytea) (
  SFUNC = public.morlet_rollup_step, STYPE = bytea, PARALLEL = SAFE);

-- These tables are caller-owned. All functions use invoker privileges. New collections store a summary of every
-- chunk (SQL 0.3.5; morlet_enable_chunk_summaries adds them to older collections): interval statistics and
-- overviews then read summaries instead of decoding, for about 2% of ingestion speed and 1% of storage.
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
    checkpoint jsonb NOT NULL,
    sample_quantum float8 NOT NULL DEFAULT 1 CHECK(sample_quantum>0 AND sample_quantum<''Infinity''))',schema_name,collection_name);
  EXECUTE format('CREATE TABLE %I.%I (
    recording_id text NOT NULL REFERENCES %I.%I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    sample_count integer NOT NULL CHECK(sample_count BETWEEN 1 AND 32768),
    encoded bytea COMPRESSION pglz NOT NULL CHECK(public.morlet_valid_encoded_chunk(encoded,sample_count)),
    summary bytea COMPRESSION pglz CHECK(summary IS NULL OR (octet_length(summary)=66 AND get_byte(summary,0)=1)),
    PRIMARY KEY(recording_id,start_sample))',schema_name,collection_name||'_chunks',schema_name,collection_name);
  EXECUTE format('CREATE TABLE %I.%I (
    recording_id text NOT NULL REFERENCES %I.%I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),features float8[] COMPRESSION pglz NOT NULL,
    PRIMARY KEY(recording_id,start_sample))',schema_name,collection_name||'_segments',schema_name,collection_name);
  RETURN format('%I.%I',schema_name,collection_name)::regclass;
END $$;

-- TOAST compression of the stored samples and everything derived from them is pinned to pglz (#280).
-- PostgreSQL 19 changes default_toast_compression to lz4; without a pin, storage on 19 would differ from
-- 17 and 18 and from every published figure, which were measured with pglz. Tables created by this
-- extension's functions name pglz in their column definitions; this function pins the columns of a
-- collection created before that (the extension update calls it for every existing collection) and of
-- legacy collections (morlet_create_collection). Catalog only: stored values are not rewritten and
-- read back unchanged. Returns the number of columns it changed; 0 when everything was already pinned.
CREATE FUNCTION public.morlet_pin_toast_compression(collection regclass)
RETURNS integer LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s text; c text; col record; changed integer:=0;
BEGIN
  SELECT n.nspname,cl.relname INTO s,c FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace
    WHERE cl.oid=collection;
  FOR col IN SELECT a.attrelid::regclass AS rel,a.attname
    FROM (VALUES ('','samples'),('_chunks','encoded'),('_chunks','samples'),('_chunks','summary'),
                 ('_segments','features'),('_envelopes','envelope'),('_rollups','summary'),
                 ('_ctx_values','vals'),('_kept_summaries','summary')) v(suffix,attname)
    JOIN pg_class t ON t.relname=c||v.suffix AND t.relnamespace=(SELECT oid FROM pg_namespace WHERE nspname=s)
    JOIN pg_attribute a ON a.attrelid=t.oid AND a.attname=v.attname AND NOT a.attisdropped
    WHERE a.attcompression<>'p' AND a.atttypid IN ('bytea'::regtype,'float8[]'::regtype)
    ORDER BY 1,2
  LOOP
    EXECUTE format('ALTER TABLE %s ALTER COLUMN %I SET COMPRESSION pglz',col.rel,col.attname);
    changed:=changed+1;
  END LOOP;
  RETURN changed;
END $$;

-- Collections created before encoded storage keep float8[] chunks; the
-- expressions let every reader handle both layouts with one query text.
CREATE FUNCTION public.morlet_stream_relations(collection regclass)
RETURNS TABLE(chunks regclass,segments regclass,encoded boolean,samples_expr text,count_expr text,
  summarized boolean)
-- PL/pgSQL so the catalog query plan is cached per session; a SQL function with a
-- SET clause cannot be inlined and was re-planned on every call (~0.8 ms).
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  RETURN QUERY SELECT r.chunks,r.segments,e.encoded,
    CASE WHEN e.encoded THEN 'public.morlet_decode_chunk(c.encoded)' ELSE 'c.samples' END,
    CASE WHEN e.encoded THEN 'c.sample_count' ELSE 'cardinality(c.samples)' END,e.summarized
  FROM (SELECT format('%I.%I',n.nspname,c.relname||'_chunks')::regclass AS chunks,
      format('%I.%I',n.nspname,c.relname||'_segments')::regclass AS segments
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection) r
  CROSS JOIN LATERAL (SELECT coalesce(bool_or(a.attname='encoded'),false) AS encoded,
      coalesce(bool_or(a.attname='summary'),false) AS summarized
    FROM pg_attribute a WHERE a.attrelid=r.chunks AND a.attname IN ('encoded','summary') AND NOT a.attisdropped) e;
END $$;

CREATE FUNCTION public.morlet_start_recording(collection regclass,recording_id text,
  sample_rate_hz float8,channel text,unit text,metadata jsonb DEFAULT '{}'::jsonb,
  window_size integer DEFAULT 512,stride integer DEFAULT 256,normalization text DEFAULT 'zscore',
  extractor text DEFAULT 'morlet-energy-v1',sample_quantum float8 DEFAULT 1)
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE initial jsonb; relations record;
BEGIN
  -- The checkpoint records the explicitly selected representation version.
  initial:=public.morlet_chunk_checkpoint(sample_rate_hz,window_size,stride,normalization,extractor);
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF relations.encoded THEN
    EXECUTE format('INSERT INTO %s(recording_id,sample_rate_hz,channel,unit,metadata,checkpoint,sample_quantum) VALUES ($1,$2,$3,$4,$5,$6,$7)',collection)
      USING recording_id,sample_rate_hz,channel,unit,metadata,initial,sample_quantum;
  ELSIF sample_quantum<>1 THEN
    RAISE EXCEPTION 'sample_quantum requires a collection with encoded chunk storage';
  ELSE
    EXECUTE format('INSERT INTO %s(recording_id,sample_rate_hz,channel,unit,metadata,checkpoint) VALUES ($1,$2,$3,$4,$5,$6)',collection)
      USING recording_id,sample_rate_hz,channel,unit,metadata,initial;
  END IF;
END $$;

CREATE FUNCTION public.morlet_append_chunk(collection regclass,recording_id text,start_sample bigint,samples float8[])
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE state jsonb; quantum float8:=1; previous float8[]; result jsonb; relations record; updated_rows bigint;
BEGIN
  IF array_ndims(samples) IS DISTINCT FROM 1 OR array_lower(samples,1) IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'chunk must be a one-dimensional array starting at 1';
  END IF;
  PERFORM public.morlet_valid_chunk(samples);
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  -- Serializes concurrent appenders of the same recording, under READ COMMITTED.
  IF relations.encoded THEN
    EXECUTE format('SELECT checkpoint,sample_quantum FROM %s WHERE recording_id=$1 FOR UPDATE',collection)
      INTO state,quantum USING recording_id;
  ELSE
    EXECUTE format('SELECT checkpoint FROM %s WHERE recording_id=$1 FOR UPDATE',collection) INTO state USING recording_id;
  END IF;
  IF state IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  EXECUTE format('SELECT %s FROM %s c WHERE c.recording_id=$1 AND c.start_sample=$2',relations.samples_expr,relations.chunks)
    INTO previous USING recording_id,start_sample;
  IF previous IS NOT NULL THEN
    -- Bit for bit: array equality treats +0 and -0 as equal.
    IF public.morlet_float8_le_bytes(previous) IS DISTINCT FROM public.morlet_float8_le_bytes(samples) THEN
      RAISE EXCEPTION 'conflicting replay at chunk offset';
    END IF;
    RETURN (state->>'end')::bigint;
  END IF;
  result:=public.morlet_chunk_step(state,start_sample,samples);
  IF relations.summarized THEN
    -- With chunk summaries enabled, each chunk is stored with its summary.
    EXECUTE format('INSERT INTO %s(recording_id,start_sample,sample_count,encoded,summary)
      VALUES ($1,$2,$3,public.morlet_encode_chunk($4,$5),public.morlet_chunk_summary($4))',relations.chunks)
      USING recording_id,start_sample,cardinality(samples),samples,quantum;
  ELSIF relations.encoded THEN
    EXECUTE format('INSERT INTO %s(recording_id,start_sample,sample_count,encoded) VALUES ($1,$2,$3,public.morlet_encode_chunk($4,$5))',relations.chunks)
      USING recording_id,start_sample,cardinality(samples),samples,quantum;
  ELSE
    EXECUTE format('INSERT INTO %s(recording_id,start_sample,samples) VALUES ($1,$2,$3)',relations.chunks)
      USING recording_id,start_sample,samples;
  END IF;
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
  extractor text DEFAULT 'morlet-energy-v1',sample_quantum float8 DEFAULT 1)
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE initial jsonb; existing record; relations record;
BEGIN
  initial:=public.morlet_chunk_checkpoint(sample_rate_hz,window_size,stride,normalization,extractor);
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF relations.encoded THEN
    EXECUTE format('INSERT INTO %s(recording_id,sample_rate_hz,channel,unit,metadata,checkpoint,sample_quantum)
      VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(recording_id) DO NOTHING',collection)
      USING recording_id,sample_rate_hz,channel,unit,metadata,initial,sample_quantum;
    EXECUTE format('SELECT sample_rate_hz,channel,unit,metadata,checkpoint,sample_quantum FROM %s WHERE recording_id=$1 FOR UPDATE',collection)
      INTO existing USING recording_id;
  ELSIF sample_quantum<>1 THEN
    RAISE EXCEPTION 'sample_quantum requires a collection with encoded chunk storage';
  ELSE
    EXECUTE format('INSERT INTO %s(recording_id,sample_rate_hz,channel,unit,metadata,checkpoint)
      VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT(recording_id) DO NOTHING',collection)
      USING recording_id,sample_rate_hz,channel,unit,metadata,initial;
    EXECUTE format('SELECT sample_rate_hz,channel,unit,metadata,checkpoint,1::float8 AS sample_quantum FROM %s WHERE recording_id=$1 FOR UPDATE',collection)
      INTO existing USING recording_id;
  END IF;
  IF existing.checkpoint IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  IF existing.sample_rate_hz IS DISTINCT FROM sample_rate_hz OR existing.channel IS DISTINCT FROM channel
    OR existing.unit IS DISTINCT FROM unit OR existing.metadata IS DISTINCT FROM metadata
    OR existing.checkpoint->>'extractor_version' IS DISTINCT FROM initial->>'extractor_version'
    OR (existing.checkpoint->>'window')::integer IS DISTINCT FROM window_size
    OR (existing.checkpoint->>'stride')::integer IS DISTINCT FROM stride
    OR existing.checkpoint->>'normalization' IS DISTINCT FROM normalization
    OR existing.sample_quantum IS DISTINCT FROM sample_quantum THEN
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
  -- Chunks hold at most 32768 samples, so the lower start bound is implied; it
  -- keeps reads late in long recordings to an index range instead of a prefix scan.
  -- Encoded chunks are decoded and sliced in one call; float8[] chunks go through SQL.
  IF relations.encoded THEN
    EXECUTE format('SELECT public.morlet_decode_interval(coalesce(array_agg(c.encoded ORDER BY c.start_sample),''{}''),
        coalesce(array_agg(c.start_sample ORDER BY c.start_sample),''{}''),$2,$3)
      FROM %1$s c WHERE c.recording_id=$1 AND c.start_sample<$3 AND c.start_sample>$2-32768
        AND c.start_sample+c.sample_count>$2',relations.chunks)
      INTO recovered USING source_recording_id,interval_start,interval_end;
    covered:=coalesce(cardinality(recovered),0);
    IF covered<>interval_end-interval_start THEN
      PERFORM public.morlet_raise_incomplete(collection,source_recording_id,interval_start,interval_end);
    END IF;
    RETURN QUERY SELECT source_recording_id,interval_start,interval_end,recovered,
      source.sample_rate_hz,source.channel,source.unit,source.metadata,source.checkpoint->>'extractor_version';
    RETURN;
  END IF;
  EXECUTE format('SELECT array_agg(v.value ORDER BY c.start_sample,v.ordinality),count(*)
    FROM %1$s c CROSS JOIN LATERAL unnest((%2$s)[
      greatest(1,$2-c.start_sample+1)::integer:
      least((%3$s)::bigint,$3-c.start_sample)::integer
    ]) WITH ORDINALITY AS v(value,ordinality)
    WHERE c.recording_id=$1 AND c.start_sample<$3 AND c.start_sample>$2-32768
      AND c.start_sample+(%3$s)>$2',relations.chunks,relations.samples_expr,relations.count_expr)
    INTO recovered,covered USING source_recording_id,interval_start,interval_end;
  IF covered<>interval_end-interval_start THEN
    PERFORM public.morlet_raise_incomplete(collection,source_recording_id,interval_start,interval_end);
  END IF;
  RETURN QUERY SELECT source_recording_id,interval_start,interval_end,recovered,
    source.sample_rate_hz,source.channel,source.unit,source.metadata,source.checkpoint->>'extractor_version';
END $$;

-- As morlet_append_chunk, with the samples as raw IEEE 754 float64 values, little-endian, 8 bytes each,
-- instead of a float8[]: a client sends the bytes of an array and the server skips per-element parsing.
-- The samples go through morlet_append_chunk unchanged: same checks, gaps, retries and conflicts.
CREATE FUNCTION public.morlet_append_chunk_raw(collection regclass,recording_id text,start_sample bigint,samples bytea)
RETURNS bigint LANGUAGE sql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT public.morlet_append_chunk(collection,recording_id,start_sample,public.morlet_float8_from_le_bytes(samples))
$$;

-- A run of consecutive chunks of one recording in one call (SQL 0.3.4, issue #233): raw little-endian
-- float64 samples cut into chunks of chunk_size samples, the last possibly shorter. Equivalent to calling
-- morlet_append_chunk_raw for each chunk in order: the same chunks, summaries, segments and checkpoint,
-- privileges and row security. A batch that starts at the recording's next sample offset is stored with
-- one statement per table; any other batch (a retry, an overlap, a gap, a float8[] collection or a
-- recording that is not visible) goes through morlet_append_chunk_raw chunk by chunk, which verifies
-- replays bit for bit and raises the same errors.
CREATE FUNCTION public.morlet_append_chunks_raw(collection regclass,recording_id text,start_sample bigint,
  samples bytea,chunk_size integer)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE state jsonb; quantum float8; relations record; b record; updated_rows bigint; total bigint; s integer:=0;
  received bigint;
BEGIN
  IF chunk_size NOT BETWEEN 1 AND 32768 THEN RAISE EXCEPTION 'chunk_size must be 1..32768'; END IF;
  total:=octet_length(samples);
  IF total=0 OR total%8<>0 THEN
    RAISE EXCEPTION 'raw samples must be a nonempty multiple of 8 bytes (little-endian float64)';
  END IF;
  total:=total/8;
  IF total>262144 THEN RAISE EXCEPTION 'batch exceeds 262144 samples'; END IF;
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF relations.encoded THEN
    -- Serializes concurrent appenders of the same recording, under READ COMMITTED.
    EXECUTE format('SELECT checkpoint,sample_quantum FROM %s WHERE recording_id=$1 FOR UPDATE',collection)
      INTO state,quantum USING recording_id;
  END IF;
  IF state IS NULL OR start_sample IS DISTINCT FROM (state->>'end')::bigint THEN
    WHILE s<total LOOP
      received:=public.morlet_append_chunk_raw(collection,recording_id,start_sample+s,
        substring(samples FROM 8*s+1 FOR 8*chunk_size));
      s:=s+chunk_size;
    END LOOP;
    RETURN received;
  END IF;
  SELECT * INTO b FROM public.morlet_chunk_batch(state,start_sample,samples,chunk_size,quantum,relations.summarized);
  IF relations.summarized THEN
    EXECUTE format('INSERT INTO %s(recording_id,start_sample,sample_count,encoded,summary)
      SELECT $1,u.s,u.c,u.e,u.m FROM unnest($2::bigint[],$3::integer[],$4::bytea[],$5::bytea[]) u(s,c,e,m)',
      relations.chunks) USING recording_id,b.starts,b.counts,b.encoded,b.summaries;
  ELSE
    EXECUTE format('INSERT INTO %s(recording_id,start_sample,sample_count,encoded)
      SELECT $1,u.s,u.c,u.e FROM unnest($2::bigint[],$3::integer[],$4::bytea[]) u(s,c,e)',
      relations.chunks) USING recording_id,b.starts,b.counts,b.encoded;
  END IF;
  EXECUTE format('INSERT INTO %s(recording_id,start_sample,features)
    SELECT $1,(s->>''start_sample'')::bigint,ARRAY(SELECT jsonb_array_elements_text(s->''features'')::float8)
    FROM jsonb_array_elements($2->''segments'') s',relations.segments) USING recording_id,b.result;
  EXECUTE format('UPDATE %s SET checkpoint=$2 WHERE recording_id=$1',collection)
    USING recording_id,b.result->'checkpoint';
  GET DIAGNOSTICS updated_rows = ROW_COUNT;
  IF updated_rows<>1 THEN RAISE EXCEPTION 'checkpoint update was not permitted'; END IF;
  RETURN (b.result->'checkpoint'->>'end')::bigint;
END $$;

-- The samples of [interval_start, interval_end) as raw IEEE 754 float64 values, little-endian, 8 bytes
-- each, for clients that map bytes to arrays without parsing every element. Same visibility, coverage
-- and errors as morlet_read_interval; up to 16,777,216 samples per call; encoded collections only.
CREATE FUNCTION public.morlet_read_interval_raw(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint)
RETURNS bytea
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; relations record; recovered bytea;
BEGIN
  IF interval_start<0 OR interval_end<=interval_start OR interval_end-interval_start>16777216 THEN
    RAISE EXCEPTION 'raw read interval must contain 1..16777216 samples at nonnegative coordinates';
  END IF;
  EXECUTE format('SELECT r.checkpoint FROM %s r WHERE r.recording_id=$1',collection)
    INTO source USING source_recording_id;
  IF source.checkpoint IS NULL THEN RAISE EXCEPTION 'source recording does not exist or is not visible'; END IF;
  IF interval_end>(source.checkpoint->>'end')::bigint THEN
    RAISE EXCEPTION 'source interval exceeds recording length';
  END IF;
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF NOT relations.encoded THEN
    RAISE EXCEPTION 'raw reads need a collection with encoded chunks; use morlet_read_interval';
  END IF;
  EXECUTE format('SELECT public.morlet_decode_interval_raw(coalesce(array_agg(c.encoded ORDER BY c.start_sample),''{}''),
      coalesce(array_agg(c.start_sample ORDER BY c.start_sample),''{}''),$2,$3)
    FROM %1$s c WHERE c.recording_id=$1 AND c.start_sample<$3 AND c.start_sample>$2-32768
      AND c.start_sample+c.sample_count>$2',relations.chunks)
    INTO recovered USING source_recording_id,interval_start,interval_end;
  IF coalesce(length(recovered),0)<>8*(interval_end-interval_start) THEN
    PERFORM public.morlet_raise_incomplete(collection,source_recording_id,interval_start,interval_end);
  END IF;
  RETURN recovered;
END $$;

-- Compressed transfer (docs/compressed-transfer.md): the encoded chunks of [interval_start, interval_end) in
-- one transfer frame, for a client that decodes them (the Python SDK's native decoder). Same limits, visibility,
-- coverage and errors as morlet_read_interval_raw, but nothing is decoded here: coverage is checked from the
-- stored sample counts, and the client verifies every chunk's checksum. The first and last chunks are sent
-- whole, so the frame can hold up to 32,767 samples of the same recording on either side of the interval; the
-- client cuts them off. If a chunk's format version is above max_chunk_version, the samples are decoded here and
-- sent in a raw frame instead, so a client never receives a chunk it cannot decode.
CREATE FUNCTION public.morlet_read_interval_encoded(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,max_chunk_version integer DEFAULT 3)
RETURNS bytea
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; relations record; frame bytea;
BEGIN
  IF interval_start<0 OR interval_end<=interval_start OR interval_end-interval_start>16777216 THEN
    RAISE EXCEPTION 'raw read interval must contain 1..16777216 samples at nonnegative coordinates';
  END IF;
  EXECUTE format('SELECT r.checkpoint FROM %s r WHERE r.recording_id=$1',collection)
    INTO source USING source_recording_id;
  IF source.checkpoint IS NULL THEN RAISE EXCEPTION 'source recording does not exist or is not visible'; END IF;
  IF interval_end>(source.checkpoint->>'end')::bigint THEN
    RAISE EXCEPTION 'source interval exceeds recording length';
  END IF;
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF NOT relations.encoded THEN
    RAISE EXCEPTION 'encoded reads need a collection with encoded chunks; use morlet_read_interval';
  END IF;
  EXECUTE format('SELECT public.morlet_frame_chunks(coalesce(array_agg(c.encoded ORDER BY c.start_sample),''{}''),
      coalesce(array_agg(c.start_sample ORDER BY c.start_sample),''{}''),
      coalesce(array_agg(c.sample_count ORDER BY c.start_sample),''{}''),$2,$3,$4)
    FROM %1$s c WHERE c.recording_id=$1 AND c.start_sample<$3 AND c.start_sample>$2-32768
      AND c.start_sample+c.sample_count>$2',relations.chunks)
    INTO frame USING source_recording_id,interval_start,interval_end,max_chunk_version;
  IF frame IS NULL THEN
    PERFORM public.morlet_raise_incomplete(collection,source_recording_id,interval_start,interval_end);
  END IF;
  RETURN frame;
END $$;

-- Count, min, max, mean and RMS of the samples of [interval_start, interval_end), computed in one pass
-- while the chunks are decoded (no sample array is built). Same visibility, coverage and errors as
-- morlet_read_interval; up to 16,777,216 samples; encoded collections only. min and max are exact
-- sample values; mean is a compensated sum divided by the count; rms is scaled against overflow; any
-- NaN sample makes them NaN. Agreement with other summation orders is to a few units in the last place.
CREATE FUNCTION public.morlet_interval_stats(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint)
RETURNS TABLE(sample_count bigint,min float8,max float8,mean float8,rms float8)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE stats float8[];
BEGIN
  stats:=public.morlet_interval_buckets(collection,source_recording_id,interval_start,interval_end,1,'statistics');
  RETURN QUERY SELECT stats[3]::bigint,stats[4],stats[5],stats[6],stats[7];
END $$;

-- The newest samples of every recording (SQL 0.3.6, #206): "what is the current value of each sensor?". One row
-- per recording with samples whose metadata contains metadata_filter, in recording_id order:
--   n 1..32768  the last n samples (fewer when the recording is shorter), read as morlet_read_interval reads
--               them, with their statistics;
--   n = 0       no samples: statistics of the newest stored chunk (from its summary when summaries are enabled).
-- end_sample is the checkpoint end; end_time the time just after the last sample when the recording has a start
-- time (morlet_recording_start_ns). Cost per recording is a fixed number of index lookups, independent of how many
-- chunks it has. When the newest samples are not readable (compacted by a memory level, or a gap), samples and
-- statistics are NULL and note gives the reason; other recordings are unaffected. More than max_recordings
-- matching recordings is an error. Privileges and row security apply as for every reader.
CREATE FUNCTION public.morlet_latest(collection regclass,n integer DEFAULT 1,metadata_filter jsonb DEFAULT '{}'::jsonb,
  max_recordings integer DEFAULT 1000)
RETURNS TABLE(recording_id text,sample_rate_hz float8,channel text,unit text,start_sample bigint,end_sample bigint,
  end_time timestamptz,samples float8[],sample_count bigint,min float8,max float8,mean float8,rms float8,note text)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; r record; matching bigint; lo bigint; chunk_end bigint; st record; t numeric;
BEGIN
  IF collection IS NULL OR n IS NULL OR metadata_filter IS NULL OR max_recordings IS NULL THEN
    RAISE EXCEPTION 'collection, n, metadata_filter and max_recordings must not be NULL';
  END IF;
  IF n NOT BETWEEN 0 AND 32768 THEN RAISE EXCEPTION 'n must be 0..32768'; END IF;
  IF jsonb_typeof(metadata_filter)<>'object' THEN RAISE EXCEPTION 'metadata_filter must be a JSON object'; END IF;
  IF max_recordings NOT BETWEEN 1 AND 100000 THEN RAISE EXCEPTION 'max_recordings must be 1..100000'; END IF;
  SELECT * INTO rel FROM public.morlet_stream_relations(collection);
  IF n=0 AND NOT rel.encoded THEN RAISE EXCEPTION 'n = 0 (chunk statistics) needs a collection with encoded chunks'; END IF;
  EXECUTE format('SELECT count(*) FROM %s t WHERE t.metadata @> $1 AND coalesce((t.checkpoint->>''end'')::bigint,0)>0',
    collection) INTO matching USING metadata_filter;
  IF matching>max_recordings THEN
    RAISE EXCEPTION '% recordings match, more than max_recordings (%)',matching,max_recordings
      USING HINT='Narrow metadata_filter or pass a larger max_recordings.';
  END IF;
  FOR r IN EXECUTE format('SELECT t.recording_id,t.sample_rate_hz,t.channel,t.unit,t.metadata,
      (t.checkpoint->>''end'')::bigint AS e FROM %s t
      WHERE t.metadata @> $1 AND coalesce((t.checkpoint->>''end'')::bigint,0)>0 ORDER BY t.recording_id',collection)
      USING metadata_filter LOOP
    recording_id:=r.recording_id; sample_rate_hz:=r.sample_rate_hz; channel:=r.channel; unit:=r.unit;
    end_sample:=r.e; samples:=NULL; sample_count:=NULL; min:=NULL; max:=NULL; mean:=NULL; rms:=NULL; note:=NULL;
    t:=public.morlet_recording_start_ns(r.metadata);
    end_time:=CASE WHEN t IS NULL THEN NULL ELSE to_timestamp(((t+r.e*1e9/r.sample_rate_hz)/1e9)::float8) END;
    IF n>0 THEN
      start_sample:=greatest(0,r.e-n);
    ELSE
      EXECUTE format('SELECT c.start_sample,c.start_sample+%s FROM %s c WHERE c.recording_id=$1
          ORDER BY c.start_sample DESC LIMIT 1',rel.count_expr,rel.chunks)
        INTO lo,chunk_end USING r.recording_id;
      start_sample:=coalesce(lo,r.e);
      IF chunk_end IS DISTINCT FROM r.e THEN
        note:='the newest samples are not stored (compacted by a memory level, or a gap); no chunk ends at the checkpoint';
        RETURN NEXT; CONTINUE;
      END IF;
    END IF;
    BEGIN
      IF n>0 THEN
        SELECT i.samples INTO samples FROM public.morlet_read_interval(collection,r.recording_id,start_sample,r.e) i;
      END IF;
      IF rel.encoded THEN
        SELECT * INTO st FROM public.morlet_interval_stats(collection,r.recording_id,start_sample,r.e);
        sample_count:=st.sample_count; min:=st.min; max:=st.max; mean:=st.mean; rms:=st.rms;
      ELSE
        SELECT count(*),pg_catalog.min(v),pg_catalog.max(v),avg(v),sqrt(avg(v*v))
          INTO sample_count,min,max,mean,rms FROM unnest(samples) v;
      END IF;
    EXCEPTION WHEN raise_exception THEN
      samples:=NULL; sample_count:=NULL; min:=NULL; max:=NULL; mean:=NULL; rms:=NULL; note:=SQLERRM;
    END;
    RETURN NEXT;
  END LOOP;
END $$;

-- Shared body of morlet_interval_stats and morlet_interval_overview: per-bucket statistics as
-- [bucket_start, bucket_end, count, min, max, mean, rms] per non-empty bucket. With chunk summaries
-- enabled, chunks inside the interval are not decoded (morlet_enable_chunk_summaries), and the
-- interval may be of any length; otherwise it is limited to 16,777,216 samples. With roll-ups
-- (morlet_enable_rollups, #219), each part of the interval takes the coarsest stored roll-up that lies
-- inside it and inside one bucket, then finer ones at the edges, then chunks: pieces are merged exactly,
-- so the result is bitwise the same as from the chunks alone.
CREATE FUNCTION public.morlet_interval_buckets(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,buckets integer,what text)
RETURNS float8[] LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; relations record; stats float8[]; covered float8:=0; i integer; mem record; src text;
  cut bigint; roll regclass; gaps int8multirange; lvl integer; r_start bigint[]:='{}'; r_count bigint[]:='{}';
  r_sum bytea[]:='{}'; picked record;
BEGIN
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  IF NOT relations.summarized AND (interval_start<0 OR interval_end<=interval_start OR interval_end-interval_start>16777216) THEN
    RAISE EXCEPTION '% interval must contain 1..16777216 samples at nonnegative coordinates',what
      USING HINT='With chunk summaries enabled (morlet_enable_chunk_summaries) the interval may be of any length.';
  ELSIF interval_start<0 OR interval_end<=interval_start THEN
    RAISE EXCEPTION '% interval must contain at least one sample at nonnegative coordinates',what;
  END IF;
  IF buckets NOT BETWEEN 1 AND 100000 THEN RAISE EXCEPTION 'buckets must be 1..100000'; END IF;
  EXECUTE format('SELECT r.checkpoint FROM %s r WHERE r.recording_id=$1',collection)
    INTO source USING source_recording_id;
  IF source.checkpoint IS NULL THEN RAISE EXCEPTION 'source recording does not exist or is not visible'; END IF;
  IF interval_end>(source.checkpoint->>'end')::bigint THEN
    RAISE EXCEPTION 'source interval exceeds recording length';
  END IF;
  IF NOT relations.encoded THEN
    RAISE EXCEPTION 'interval % need a collection with encoded chunks',what;
  END IF;
  -- With episodic memory, chunks compacted to levels 2 and 3 contribute their kept summaries; those are exact
  -- only for whole chunks, so an interval that cuts one is refused.
  IF mem.kept IS NOT NULL THEN
    EXECUTE format('SELECT k.start_sample FROM %s k WHERE k.recording_id=$1 AND k.start_sample<$3
        AND k.start_sample+k.sample_count>$2 AND (k.start_sample<$2 OR k.start_sample+k.sample_count>$3) LIMIT 1',mem.kept)
      INTO cut USING source_recording_id,interval_start,interval_end;
    IF cut IS NOT NULL THEN
      RAISE EXCEPTION 'the % interval cuts the compacted chunk at sample % of recording %',what,cut,source_recording_id
        USING HINT='Compacted chunks keep exact statistics for whole chunks only: widen or narrow the interval to chunk boundaries.';
    END IF;
    src:=format('(SELECT c.recording_id,c.start_sample,c.sample_count,c.encoded,%s AS summary FROM %s c
      UNION ALL SELECT k.recording_id,k.start_sample,k.sample_count,NULL::bytea,k.summary FROM %s k)',
      CASE WHEN relations.summarized THEN 'c.summary' ELSE 'NULL::bytea' END,relations.chunks,mem.kept);
  END IF;
  -- Roll-ups, coarsest first: a block is taken when it lies inside a part of the interval not yet covered and
  -- inside one bucket; what it covers is removed from the parts left for finer levels and for chunks.
  gaps:=int8multirange(int8range(interval_start,interval_end));
  roll:=public.morlet_rollup_relation(collection);
  -- A caller who may not read the roll-ups gets the same result from the chunks.
  IF roll IS NOT NULL AND has_table_privilege(roll,'SELECT') THEN
    -- Each gap is looked up on its own (OFFSET 0 keeps the subquery per gap), so the index on block is used and
    -- the cost follows the number of gaps, not the size of the table; a level's picks are collected in one go.
    FOR lvl IN REVERSE 3..1 LOOP
      EXIT WHEN isempty(gaps);
      EXECUTE format('SELECT array_agg(r.start_sample ORDER BY r.start_sample) AS s,
            array_agg(r.end_sample-r.start_sample ORDER BY r.start_sample) AS n,
            array_agg(r.summary ORDER BY r.start_sample) AS sm,range_agg(int8range(r.start_sample,r.end_sample)) AS covered
          FROM unnest($5::int8multirange) g, LATERAL (SELECT x.start_sample,x.end_sample,x.summary FROM %s x
            WHERE x.recording_id=$1 AND x.level=$6 AND x.block BETWEEN lower(g)>>(15+5*$6) AND (upper(g)-1)>>(15+5*$6)
              AND x.start_sample>=lower(g) AND x.end_sample<=upper(g)
              AND public.morlet_interval_bucket(x.start_sample,$2,$3,$4)=public.morlet_interval_bucket(x.end_sample-1,$2,$3,$4)
            OFFSET 0) r',roll)
        INTO picked USING source_recording_id,interval_start,interval_end,buckets,gaps,lvl;
      CONTINUE WHEN picked.covered IS NULL;
      r_start:=r_start||picked.s; r_count:=r_count||picked.n; r_sum:=r_sum||picked.sm;
      gaps:=gaps-picked.covered;
    END LOOP;
  END IF;
  -- A chunk's encoded bytes are read only when it is cut by the interval or has no summary.
  EXECUTE format('SELECT public.morlet_interval_stats_agg(
      CASE WHEN %2$s IS NULL OR c.start_sample<$2 OR c.start_sample+c.sample_count>$3 THEN c.encoded END,
      %2$s,c.start_sample,c.sample_count,$2,$3,$4 ORDER BY c.start_sample)
    FROM (SELECT c.* FROM unnest($5::int8multirange) g, LATERAL (SELECT c.* FROM %1$s c WHERE c.recording_id=$1
        AND c.start_sample<upper(g) AND c.start_sample>lower(g)-32768 AND c.start_sample+c.sample_count>lower(g) OFFSET 0) c
      UNION ALL SELECT $1,u.s,u.n::integer,NULL::bytea,u.sm FROM unnest($6,$7,$8) u(s,n,sm)) c',
    coalesce(src,format('(SELECT c.recording_id,c.start_sample,c.sample_count,c.encoded,%s AS summary FROM %s c)',
      CASE WHEN relations.summarized THEN 'c.summary' ELSE 'NULL::bytea' END,relations.chunks)),
    'c.summary')
    INTO stats USING source_recording_id,interval_start,interval_end,buckets,gaps,r_start,r_count,r_sum;
  FOR i IN 0..coalesce(cardinality(stats),0)/7-1 LOOP covered:=covered+stats[7*i+3]; END LOOP;
  IF covered<>interval_end-interval_start THEN
    PERFORM public.morlet_raise_incomplete(collection,source_recording_id,interval_start,interval_end);
  END IF;
  RETURN stats;
END $$;

-- An overview of an interval of any length: count, min, max, mean and RMS in up to `buckets` consecutive
-- parts, for example a waveform thumbnail of an hour of data. Each chunk's part falls into one bucket
-- (the one holding its middle), so bucket edges follow chunk edges. Fast with chunk summaries enabled.
CREATE FUNCTION public.morlet_interval_overview(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,buckets integer DEFAULT 100)
RETURNS TABLE(bucket_start bigint,bucket_end bigint,sample_count bigint,min float8,max float8,mean float8,rms float8)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE stats float8[]; i integer;
BEGIN
  stats:=public.morlet_interval_buckets(collection,source_recording_id,interval_start,interval_end,buckets,'overview');
  FOR i IN 0..cardinality(stats)/7-1 LOOP
    bucket_start:=stats[7*i+1]::bigint; bucket_end:=stats[7*i+2]::bigint; sample_count:=stats[7*i+3]::bigint;
    min:=stats[7*i+4]; max:=stats[7*i+5]; mean:=stats[7*i+6]; rms:=stats[7*i+7];
    RETURN NEXT;
  END LOOP;
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
  IF source_state->>'extractor_version'='none' THEN
    RAISE EXCEPTION 'recording % stores raw samples only (extractor none); use morlet_search_envelope_interval or the context comparison',
      source_recording_id;
  END IF;
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

-- Per-recording value sizes. encoded_bytes is the logical encoded payload
-- (NULL for float8[] collections); stored_value_bytes is each value's datum size
-- after any TOAST compression. Neither includes tuple, page, TOAST-chunk or index
-- overhead, which is shared across recordings: see morlet_collection_storage.
CREATE FUNCTION public.morlet_recording_storage(collection regclass,recording_id text)
RETURNS TABLE(chunks bigint,samples bigint,grid_chunks bigint,float_chunks bigint,raw_chunks bigint,
  original_float64_bytes bigint,encoded_bytes bigint,stored_value_bytes bigint,stored_value_bits_per_sample float8)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE relations record;
BEGIN
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  RETURN QUERY EXECUTE format('SELECT count(*),coalesce(sum(%2$s),0)::bigint,%3$s,%4$s,%5$s,
      8*coalesce(sum(%2$s),0)::bigint,%6$s,coalesce(sum(pg_column_size(%7$s)),0)::bigint,
      (8.0*sum(pg_column_size(%7$s))/nullif(sum(%2$s),0))::float8
    FROM %1$s c WHERE c.recording_id=$1',relations.chunks,relations.count_expr,
    CASE WHEN relations.encoded THEN 'count(*) FILTER (WHERE public.morlet_encoded_chunk_kind(c.encoded)=''grid'')' ELSE 'NULL::bigint' END,
    CASE WHEN relations.encoded THEN 'count(*) FILTER (WHERE public.morlet_encoded_chunk_kind(c.encoded)=''float'')' ELSE 'NULL::bigint' END,
    CASE WHEN relations.encoded THEN 'count(*) FILTER (WHERE public.morlet_encoded_chunk_kind(c.encoded)=''raw'')' ELSE 'NULL::bigint' END,
    CASE WHEN relations.encoded THEN 'coalesce(sum(octet_length(c.encoded)),0)::bigint' ELSE 'NULL::bigint' END,
    CASE WHEN relations.encoded THEN 'c.encoded' ELSE 'c.samples' END) USING recording_id;
END $$;

-- Physical collection footprint: pg_total_relation_size (heap, TOAST, indexes)
-- of each sibling table. Samples are the received counts of visible recordings.
-- Operational status of a collection: progress of raw data, envelopes and probe rows, and
-- bytes per structure. One row per stored envelope window size (one row with NULL window_size
-- when envelopes are not enabled or not built). The envelope stride is not stored, so the
-- backlog is given in raw samples not yet covered by an envelope window; it includes each
-- recording's partial tail shorter than one stride, which is never built.
CREATE FUNCTION public.morlet_collection_status(collection regclass)
RETURNS TABLE(window_size integer,recordings bigint,raw_samples bigint,envelopes bigint,
  recordings_without_envelopes bigint,unbuilt_samples bigint,probe_rows bigint,probe_backlog bigint,
  row_security boolean,recordings_bytes bigint,chunk_bytes bigint,segment_bytes bigint,
  envelope_bytes bigint,probe_table_bytes bigint,probe_index_bytes bigint)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rels record; env regclass; probes regclass; w integer; n_rec bigint; n_samples bigint;
BEGIN
  SELECT * INTO rels FROM public.morlet_stream_relations(collection);
  SELECT to_regclass(format('%I.%I',ns.nspname,cl.relname||'_envelopes')) INTO env
    FROM pg_class cl JOIN pg_namespace ns ON ns.oid=cl.relnamespace WHERE cl.oid=collection;
  EXECUTE format('SELECT count(*),coalesce(sum((checkpoint->>''end'')::bigint),0)::bigint FROM %s',collection) INTO n_rec,n_samples;
  row_security:=public.morlet_row_security_enabled(collection);
  recordings:=n_rec; raw_samples:=n_samples;
  recordings_bytes:=pg_total_relation_size(collection);
  chunk_bytes:=pg_total_relation_size(rels.chunks);
  segment_bytes:=pg_total_relation_size(rels.segments);
  envelope_bytes:=CASE WHEN env IS NULL THEN 0 ELSE pg_total_relation_size(env) END;
  FOR w IN EXECUTE CASE WHEN env IS NULL THEN 'SELECT NULL::integer WHERE false'
      ELSE format('SELECT DISTINCT window_size FROM %s ORDER BY 1',env) END LOOP
    window_size:=w;
    EXECUTE format('SELECT count(*) FROM %s WHERE window_size=$1',env) INTO envelopes USING w;
    EXECUTE format('WITH last AS (SELECT recording_id,max(start_sample) s FROM %s WHERE window_size=$1 GROUP BY 1)
      SELECT count(*) FILTER (WHERE l.s IS NULL),
             coalesce(sum(CASE WHEN l.s IS NULL THEN (r.checkpoint->>''end'')::bigint
                               ELSE greatest(0,(r.checkpoint->>''end'')::bigint-(l.s+$1)) END),0)::bigint
      FROM %s r LEFT JOIN last l ON l.recording_id=r.recording_id',env,collection)
      INTO recordings_without_envelopes,unbuilt_samples USING w;
    probes:=public.morlet_envelope_probe_relation(collection,w);
    IF probes IS NULL THEN
      probe_rows:=NULL; probe_backlog:=NULL; probe_table_bytes:=NULL; probe_index_bytes:=NULL;
    ELSE
      EXECUTE format('SELECT count(*) FROM %s',probes) INTO probe_rows;
      probe_backlog:=envelopes-probe_rows;
      probe_table_bytes:=pg_table_size(probes);
      SELECT coalesce(sum(pg_relation_size(i.indexrelid)),0) INTO probe_index_bytes FROM pg_index i WHERE i.indrelid=probes;
    END IF;
    RETURN NEXT;
  END LOOP;
  IF NOT FOUND THEN
    window_size:=NULL; envelopes:=0; recordings_without_envelopes:=n_rec; unbuilt_samples:=n_samples;
    probe_rows:=NULL; probe_backlog:=NULL; probe_table_bytes:=NULL; probe_index_bytes:=NULL;
    RETURN NEXT;
  END IF;
END $$;

CREATE FUNCTION public.morlet_collection_storage(collection regclass)
RETURNS TABLE(recordings bigint,samples bigint,original_float64_bytes bigint,recordings_table_bytes bigint,
  chunk_table_bytes bigint,segment_table_bytes bigint,envelope_table_bytes bigint,total_bytes bigint,
  chunk_table_bits_per_sample float8,total_bits_per_sample float8)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE relations record; n bigint; total bigint; r bigint; c bigint; g bigint; e bigint; envelopes regclass;
BEGIN
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  EXECUTE format('SELECT count(*),coalesce(sum((checkpoint->>''end'')::bigint),0)::bigint FROM %s',collection)
    INTO recordings,n;
  SELECT to_regclass(format('%I.%I',ns.nspname,cl.relname||'_envelopes')) INTO envelopes
    FROM pg_class cl JOIN pg_namespace ns ON ns.oid=cl.relnamespace WHERE cl.oid=collection;
  r:=pg_total_relation_size(collection); c:=pg_total_relation_size(relations.chunks);
  g:=pg_total_relation_size(relations.segments);
  e:=CASE WHEN envelopes IS NULL THEN 0 ELSE pg_total_relation_size(envelopes) END; total:=r+c+g+e;
  RETURN QUERY SELECT recordings,n,8*n,r,c,g,e,total,(8.0*c/nullif(n,0))::float8,(8.0*total/nullif(n,0))::float8;
END $$;

-- Whether a row was inserted by the current transaction or one of its subtransactions (row_xmin is the
-- row's xmin): a row we can see whose inserting transaction is still in progress is ours. Used by the
-- acquisition row-security policy so that a new acquisition is visible to its creator until its first
-- channel is added. Fails closed (false) when no transaction ID is assigned yet and across an xid epoch boundary.
CREATE FUNCTION public.morlet_inserted_by_current_transaction(row_xmin xid)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE top bigint; x bigint;
BEGIN
  top:=pg_current_xact_id_if_assigned()::text::bigint;
  x:=row_xmin::text::bigint;
  IF top IS NULL OR x IS NULL OR x<(top&4294967295) THEN RETURN false; END IF;
  RETURN pg_xact_status(((top>>32<<32)|x)::text::xid8)='in progress';
END $$;

-- Materialized envelopes (xc-envelope-f16-v1): an opt-in, rebuildable
-- representation stored next to raw chunks; not an index. Rows hold binary16
-- envelopes of fixed windows; the payload's first byte is its encoding version.
-- Whether an acquisition has a channel visible to the caller (under the caller's row security).
CREATE FUNCTION public.morlet_acquisition_has_visible_channel(channels regclass,acquisition_id text)
RETURNS boolean LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE found boolean;
BEGIN
  EXECUTE format('SELECT EXISTS(SELECT 1 FROM %s WHERE acquisition_id=$1)',channels) INTO found USING acquisition_id;
  RETURN found;
END $$;

-- Row-level security for companion tables (opt-in). Search and read functions are
-- SECURITY INVOKER and need SELECT on the companion tables, which would otherwise let a
-- role read every recording's chunks, envelopes, probe vectors and context directly. This
-- adds one policy per companion table: a row is visible and writable only when its parent
-- recording is visible to the caller in the collection table, so the collection's own RLS
-- policies (for example one per tenant) govern every table. Idempotent; once used, the
-- enable functions apply it to companion tables created later. Table owners bypass RLS.
CREATE FUNCTION public.morlet_enable_row_security(collection regclass)
RETURNS integer LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s text; c text; t record; covered integer:=0; cond text; chk text;
BEGIN
  SELECT n.nspname,cl.relname INTO s,c FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE cl.oid=collection;
  FOR t IN SELECT cl.relname FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace
      WHERE n.nspname=s AND cl.relkind='r' AND (cl.relname IN (c||'_chunks',c||'_segments',c||'_envelopes',c||'_ctx',
        c||'_ctx_segments',c||'_ctx_values',c||'_labels',c||'_acq',c||'_acq_channels',c||'_rollups') OR (left(cl.relname,length(c)+8)=c||'_probes_' AND substr(cl.relname,length(c)+9) ~ '^[0-9]+$'))
      ORDER BY cl.relname LOOP
    IF t.relname=c||'_ctx_values' THEN
      cond:=format('EXISTS(SELECT 1 FROM %I.%I x WHERE x.segment_id=%I.segment_id)',s,c||'_ctx_segments',t.relname);
      chk:=cond;
    ELSIF t.relname=c||'_acq' THEN
      -- visible through a visible channel, and to its creator until it has one (inserting an acquisition
      -- precedes its channels)
      -- (the channel lookup runs in a function: the channel policy refers to this table, and a subquery here
      -- would make the policies recursive)
      cond:=format('public.morlet_acquisition_has_visible_channel(%L::regclass,%I.acquisition_id)
        OR public.morlet_inserted_by_current_transaction(%I.xmin)',format('%I.%I',s,c||'_acq_channels'),t.relname,t.relname);
      chk:='true';
    ELSIF t.relname=c||'_acq_channels' THEN
      -- a channel row follows its recording; adding or moving one also needs its acquisition to be visible,
      -- so a recording cannot join another tenant's acquisition by naming its ID (a foreign key is not
      -- an authorization check)
      cond:=format('%I.recording_id = ANY (ARRAY(SELECT x.recording_id FROM %s x))',t.relname,collection);
      chk:=format('%s AND EXISTS(SELECT 1 FROM %I.%I a WHERE a.acquisition_id=%I.acquisition_id)',
        cond,s,c||'_acq',t.relname);
    ELSE
      -- An uncorrelated ARRAY(...) becomes an InitPlan, which keeps scans parallel; a correlated
      -- EXISTS compiled to a parallel-restricted SubPlan and made exact search 3x slower.
      cond:=format('%I.recording_id = ANY (ARRAY(SELECT x.recording_id FROM %s x))',t.relname,collection);
      chk:=cond;
    END IF;
    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',s,t.relname);
    EXECUTE format('DROP POLICY IF EXISTS morlet_follows_recording ON %I.%I',s,t.relname);
    EXECUTE format('CREATE POLICY morlet_follows_recording ON %I.%I USING (%s) WITH CHECK (%s)',s,t.relname,cond,chk);
    covered:=covered+1;
  END LOOP;
  RETURN covered;
END $$;

-- Whether morlet_enable_row_security has been applied to this collection.
CREATE FUNCTION public.morlet_row_security_enabled(collection regclass)
RETURNS boolean LANGUAGE sql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT EXISTS(SELECT 1 FROM pg_policy p JOIN pg_class cl ON cl.oid=p.polrelid
    WHERE p.polname='morlet_follows_recording' AND cl.relnamespace=(SELECT relnamespace FROM pg_class WHERE oid=collection)
      AND cl.relname=(SELECT relname FROM pg_class WHERE oid=collection)||'_chunks')
$$;

CREATE FUNCTION public.morlet_enable_envelopes(collection regclass)
RETURNS regclass LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE schema_name text; collection_name text;
BEGIN
  SELECT n.nspname,c.relname INTO schema_name,collection_name
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection;
  EXECUTE format('CREATE TABLE %I.%I (
    recording_id text NOT NULL REFERENCES %I.%I(recording_id) ON DELETE CASCADE,
    window_size integer NOT NULL CHECK(window_size IN (512,1024,2048,4096,8192,16384,32768)),
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    envelope bytea COMPRESSION pglz NOT NULL CHECK(octet_length(envelope)=1+2*((window_size+9)/10) AND get_byte(envelope,0)=1),
    PRIMARY KEY(recording_id,window_size,start_sample))',schema_name,collection_name||'_envelopes',schema_name,collection_name);
  IF public.morlet_row_security_enabled(collection) THEN PERFORM public.morlet_enable_row_security(collection); END IF;
  RETURN format('%I.%I',schema_name,collection_name||'_envelopes')::regclass;
END $$;

CREATE FUNCTION public.morlet_envelope_relation(collection regclass)
RETURNS regclass LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel regclass;
BEGIN
  SELECT to_regclass(format('%I.%I',n.nspname,c.relname||'_envelopes')) INTO rel
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection;
  IF rel IS NULL THEN RAISE EXCEPTION 'envelopes are not enabled for this collection (morlet_enable_envelopes)'; END IF;
  RETURN rel;
END $$;

-- Per-chunk summaries (encoded collections; stored by every collection created with SQL 0.3.5 or later,
-- added to older ones by this function): count, min, max and compensated sums of every stored chunk, 66 bytes in a `summary` column of the chunk table (morlet_core::intervalstats), so
-- interval statistics and overviews read summaries instead of decoding chunks inside the interval.
-- Results are bitwise the same with and without them. Adding the column does not rewrite the table;
-- appends then store each new chunk's summary, and morlet_build_chunk_summaries covers chunks stored
-- before. Summaries follow their chunks' privileges and row-level security.
CREATE FUNCTION public.morlet_enable_chunk_summaries(collection regclass)
RETURNS regclass LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE relations record;
BEGIN
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF NOT relations.encoded THEN RAISE EXCEPTION 'chunk summaries need a collection with encoded chunks'; END IF;
  EXECUTE format('ALTER TABLE %s ADD COLUMN IF NOT EXISTS summary bytea COMPRESSION pglz
    CHECK(summary IS NULL OR (octet_length(summary)=66 AND get_byte(summary,0)=1))',relations.chunks);
  RETURN relations.chunks;
END $$;

-- Writes summaries of up to max_chunks stored chunks of a recording that have none, in start order.
-- Returns the number written; 0 means the recording is covered.
CREATE FUNCTION public.morlet_build_chunk_summaries(collection regclass,recording_id text,
  max_chunks integer DEFAULT 100000)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE relations record; written bigint;
BEGIN
  IF max_chunks NOT BETWEEN 1 AND 10000000 THEN RAISE EXCEPTION 'max_chunks must be 1..10000000'; END IF;
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF NOT relations.summarized THEN
    RAISE EXCEPTION 'chunk summaries are not enabled for this collection (morlet_enable_chunk_summaries)';
  END IF;
  EXECUTE format('UPDATE %1$s c SET summary=public.morlet_encoded_chunk_summary(c.encoded)
    FROM (SELECT x.start_sample FROM %1$s x WHERE x.recording_id=$1 AND x.summary IS NULL
          ORDER BY x.start_sample LIMIT $2) t
    WHERE c.recording_id=$1 AND c.start_sample=t.start_sample',relations.chunks) USING recording_id,max_chunks;
  GET DIAGNOSTICS written = ROW_COUNT;
  RETURN written;
END $$;

-- Exact roll-ups of chunk summaries (SQL 0.3.6, #219): one row per recording, level and block, holding the exact
-- merge (morlet_core::exactstats) of every chunk that starts in the block. Level L has blocks of 2^(15+5L)
-- samples (level 1: 1,048,576; level 2: 33,554,432; level 3: 1,073,741,824), so each block of a level is 32
-- blocks of the level below. Interval statistics and overviews then read a few roll-ups instead of every chunk
-- summary, with bitwise the same results. Roll-ups need chunk summaries (morlet_enable_chunk_summaries), are
-- built by morlet_build_rollups, survive memory levels 2 and 3 (they hold the same summaries that are kept), and
-- are deleted with the summaries they contain at level 4. They follow their recordings' privileges and row
-- security.
CREATE FUNCTION public.morlet_enable_rollups(collection regclass)
RETURNS regclass LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE relations record; schema_name text; collection_name text;
BEGIN
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF NOT relations.summarized THEN
    RAISE EXCEPTION 'roll-ups need chunk summaries (morlet_enable_chunk_summaries)';
  END IF;
  SELECT n.nspname,c.relname INTO schema_name,collection_name
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection;
  EXECUTE format('CREATE TABLE IF NOT EXISTS %1$I.%2$I (
    recording_id text NOT NULL REFERENCES %1$I.%3$I(recording_id) ON DELETE CASCADE,
    level smallint NOT NULL CHECK(level BETWEEN 1 AND 3),
    block bigint NOT NULL CHECK(block>=0),
    start_sample bigint NOT NULL, end_sample bigint NOT NULL,
    summary bytea COMPRESSION pglz NOT NULL CHECK(get_byte(summary,0)=2),
    CHECK(start_sample>=block<<(15+5*level) AND start_sample<(block+1)<<(15+5*level) AND end_sample>start_sample),
    PRIMARY KEY(recording_id,level,block))',schema_name,collection_name||'_rollups',collection_name);
  IF public.morlet_row_security_enabled(collection) THEN PERFORM public.morlet_enable_row_security(collection); END IF;
  RETURN format('%I.%I',schema_name,collection_name||'_rollups')::regclass;
END $$;

-- The roll-up table of a collection, or NULL when roll-ups are not enabled.
CREATE FUNCTION public.morlet_rollup_relation(collection regclass)
RETURNS regclass LANGUAGE sql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT to_regclass(format('%I.%I',n.nspname,c.relname||'_rollups'))
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection
$$;

-- Builds the roll-ups of up to max_blocks complete level-1 blocks of a recording after the last one stored, then
-- every level-2 and level-3 block whose 32 blocks below are all stored. A block is complete when the recording
-- has reached its end, so no chunk can start in it any more. A block whose chunks are not all stored (samples
-- forgotten at memory level 4) is not built, and neither are the blocks above it: statistics there come from
-- what is stored, as without roll-ups. Returns the number of roll-ups written; 0 means the recording is covered.
-- Resumable; one builder per recording at a time.
CREATE FUNCTION public.morlet_build_rollups(collection regclass,recording_id text,max_blocks integer DEFAULT 1000)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE roll regclass; relations record; mem record; rec_end bigint; written bigint:=0; n bigint; first bigint;
  last bigint; lvl integer; src text;
BEGIN
  IF max_blocks NOT BETWEEN 1 AND 1000000 THEN RAISE EXCEPTION 'max_blocks must be 1..1000000'; END IF;
  roll:=public.morlet_rollup_relation(collection);
  IF roll IS NULL THEN RAISE EXCEPTION 'roll-ups are not enabled for this collection (morlet_enable_rollups)'; END IF;
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  EXECUTE format('SELECT (checkpoint->>''end'')::bigint FROM %s WHERE recording_id=$1',collection)
    INTO rec_end USING recording_id;
  IF rec_end IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('morlet-rollup/'||collection::oid||'/'||recording_id,0));
  src:=format('SELECT c.start_sample,c.sample_count,coalesce(c.summary,public.morlet_encoded_chunk_summary(c.encoded)) AS summary
    FROM %s c WHERE c.recording_id=$1',relations.chunks);
  IF mem.kept IS NOT NULL THEN
    src:=src||format(' UNION ALL SELECT k.start_sample,k.sample_count,k.summary FROM %s k WHERE k.recording_id=$1',mem.kept);
  END IF;
  -- Level 1 from chunks: each stored row must start where the one before ends (the first at 0) and be followed by
  -- one that starts where it ends, unless it ends the recording; so a block holds every chunk that starts in it.
  EXECUTE format('SELECT coalesce(max(block)+1,0) FROM %s WHERE recording_id=$1 AND level=1',roll) INTO first USING recording_id;
  last:=least(rec_end/1048576-1,first+max_blocks-1);
  IF last>=first THEN
    EXECUTE format('WITH r AS (SELECT x.start_sample s,x.start_sample+x.sample_count e,x.summary,
          lag(x.start_sample+x.sample_count) OVER w AS pe,lead(x.start_sample) OVER w AS ns
        FROM (%s) x WHERE x.start_sample>=($2<<20)-32768 AND x.start_sample<(($3+1)<<20)+32768 WINDOW w AS (ORDER BY x.start_sample))
      INSERT INTO %s(recording_id,level,block,start_sample,end_sample,summary)
      SELECT $1,1,r.s>>20,min(r.s),max(r.e),public.morlet_rollup_agg(r.summary) FROM r
      WHERE r.s>>20 BETWEEN $2 AND $3
      GROUP BY r.s>>20
      HAVING bool_and((r.pe=r.s OR (r.pe IS NULL AND r.s=0)) AND (r.ns=r.e OR (r.ns IS NULL AND r.e=$4)))
      ON CONFLICT DO NOTHING',src,roll) USING recording_id,first,last,rec_end;
    GET DIAGNOSTICS n = ROW_COUNT; written:=written+n;
  END IF;
  -- Levels 2 and 3 from the level below: a block is built once all 32 blocks below are stored.
  FOR lvl IN 2..3 LOOP
    EXECUTE format('SELECT coalesce(max(block)+1,0) FROM %s WHERE recording_id=$1 AND level=$2',roll)
      INTO first USING recording_id,lvl;
    last:=(rec_end>>(15+5*lvl))-1;
    CONTINUE WHEN last<first;
    EXECUTE format('INSERT INTO %1$s(recording_id,level,block,start_sample,end_sample,summary)
      SELECT $1,$2,x.block>>5,min(x.start_sample),max(x.end_sample),public.morlet_rollup_agg(x.summary)
      FROM %1$s x WHERE x.recording_id=$1 AND x.level=$2-1 AND x.block BETWEEN $3*32 AND $4*32+31
      GROUP BY x.block>>5 HAVING count(*)=32
      ON CONFLICT DO NOTHING',roll) USING recording_id,lvl,first,last;
    GET DIAGNOSTICS n = ROW_COUNT; written:=written+n;
  END LOOP;
  RETURN written;
END $$;

-- Builds envelopes for complete windows on the grid start_sample = k*stride,
-- resuming after the last stored window of this size. At most max_windows per
-- call, so long recordings can be built in bounded transactions. Returns the
-- number of rows written; 0 means the recording is up to date.
CREATE FUNCTION public.morlet_build_envelopes(collection regclass,recording_id text,
  window_size integer DEFAULT 4096,stride integer DEFAULT 2048,max_windows integer DEFAULT 10000)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel regclass; recording_end bigint; s bigint; last bigint; written bigint:=0; probes regclass; vschema text;
  rels record; n integer; hi bigint; mem record; sup boolean:=false; skip boolean; k integer; w bigint;
BEGIN
  IF window_size NOT IN (512,1024,2048,4096,8192,16384,32768) OR stride NOT BETWEEN 1 AND 32768
    OR max_windows NOT BETWEEN 1 AND 1000000 THEN
    RAISE EXCEPTION 'window_size must be a power of two in 512..32768, stride 1..32768, max_windows 1..1000000';
  END IF;
  rel:=public.morlet_envelope_relation(collection);
  -- With probe search enabled for this window size, each new envelope's vector row is
  -- written in the same transaction (morlet_enable_envelope_probes).
  probes:=public.morlet_envelope_probe_relation(collection,window_size);
  IF probes IS NOT NULL THEN
    SELECT e.extnamespace::regnamespace::text INTO vschema FROM pg_extension e WHERE e.extname='vector';
  END IF;
  EXECUTE format('SELECT (checkpoint->>''end'')::bigint FROM %s WHERE recording_id=$1',collection)
    INTO recording_end USING recording_id;
  IF recording_end IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  -- One builder per recording and window size at a time (#225): the stride check and the inserts below run
  -- after any concurrent builder committed, so two incompatible grids cannot both commit, and an identical
  -- concurrent build resumes after the other's windows. (READ COMMITTED: later statements see its rows.)
  PERFORM pg_advisory_xact_lock(hashtextextended('morlet-env/'||collection::oid||'/'||recording_id||'/'||window_size,0));
  EXECUTE format('SELECT max(start_sample) FROM %s WHERE recording_id=$1 AND window_size=$2',rel)
    INTO last USING recording_id,window_size;
  -- The grid starts at 0 and its stride is fixed by the first build: the smallest positive stored start
  -- is the stride in use. Building on with another stride would report completion without the requested
  -- windows, or mix two grids, so it is an error.
  EXECUTE format('SELECT start_sample FROM %s WHERE recording_id=$1 AND window_size=$2 AND start_sample>0
    ORDER BY start_sample LIMIT 1',rel) INTO hi USING recording_id,window_size;
  IF hi IS NOT NULL AND hi<>stride THEN
    RAISE EXCEPTION 'envelopes of window % for recording % were built with stride %, not %',window_size,recording_id,hi,stride
      USING HINT='Keep the stride, or delete this recording''s envelopes of this window size and build again.';
  END IF;
  s:=CASE WHEN last IS NULL THEN 0 ELSE (last/stride+1)*stride END;
  SELECT * INTO rels FROM public.morlet_stream_relations(collection);
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  -- Encoded collections: batches of up to 256 windows from chunks decoded once
  -- (morlet_envelopes_from_chunks); the bytes equal the per-window path below.
  WHILE rels.encoded AND s+window_size<=recording_end AND written<max_windows LOOP
    n:=least(max_windows-written,256,(recording_end-window_size-s)/stride+1);
    hi:=s+(n-1)::bigint*stride+window_size;
    -- Episodic memory: windows inside an episode at level 1 or more are not built (not searchable), nor windows
    -- touching samples removed at level 2 or more; such batches are built window by window.
    IF mem.episodes IS NOT NULL THEN
      EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s o WHERE o.recording_id=$1 AND o.level>=1
        AND o.start_sample<$3 AND o.end_sample>$2)',mem.episodes) INTO sup USING recording_id,s,hi;
    END IF;
    IF sup THEN
      FOR k IN 0..n-1 LOOP
        w:=s+k::bigint*stride;
        -- Same rule as morlet_set_level: a window is not searchable when episodes at level 1 or more cover it
        -- completely and no episode at level 0 overlaps it; and it cannot be built when samples are missing.
        EXECUTE format('SELECT (coalesce(int8range($2,$2+$3) <@ (SELECT range_agg(int8range(o.start_sample,o.end_sample))
              FROM %1$s o WHERE o.recording_id=$1 AND o.level>=1 AND o.start_sample<$2+$3 AND o.end_sample>$2),false)
            AND NOT EXISTS (SELECT 1 FROM %1$s o WHERE o.recording_id=$1 AND o.level=0
              AND o.start_sample<$2+$3 AND o.end_sample>$2))
          OR coalesce((SELECT sum(least(c.start_sample+c.sample_count,$2+$3)-greatest(c.start_sample,$2)) FROM %2$s c
              WHERE c.recording_id=$1 AND c.start_sample<$2+$3 AND c.start_sample+c.sample_count>$2
                AND c.start_sample>$2-32768),0)<$3',mem.episodes,rels.chunks)
          INTO skip USING recording_id,w,window_size;
        IF NOT skip THEN
          EXECUTE format('INSERT INTO %s(recording_id,window_size,start_sample,envelope)
            SELECT $1,$2,$3,public.morlet_envelope_encode(i.samples)
            FROM public.morlet_read_interval($4,$1,$3,$3+$2) i ON CONFLICT DO NOTHING',rel)
            USING recording_id,window_size,w,collection;
          IF probes IS NOT NULL THEN
            EXECUTE format('INSERT INTO %s SELECT recording_id,window_size,start_sample,public.morlet_envelope_decode(envelope)::%I.halfvec
              FROM %s WHERE recording_id=$1 AND window_size=$2 AND start_sample=$3 ON CONFLICT DO NOTHING',probes,vschema,rel)
              USING recording_id,window_size,w;
          END IF;
        END IF;
      END LOOP;
    ELSE
      EXECUTE format('WITH c AS (SELECT array_agg(c.encoded ORDER BY c.start_sample) enc,
          array_agg(c.start_sample ORDER BY c.start_sample) st
          FROM %s c WHERE c.recording_id=$1 AND c.start_sample<$3 AND c.start_sample+c.sample_count>$2)
        INSERT INTO %s(recording_id,window_size,start_sample,envelope)
        SELECT $1,$4,$2+(w.k-1)*$5,w.envelope
        FROM c, unnest(public.morlet_envelopes_from_chunks(c.enc,c.st,$2,$3,$4,$5,$6)) WITH ORDINALITY w(envelope,k)
        ON CONFLICT DO NOTHING',rels.chunks,rel)
        USING recording_id,s,hi,window_size,stride,n;
      -- Probe search may have been enabled by a transaction that committed while this insert waited
      -- for its lock; re-check so that these windows get their vector rows (no lasting probe_backlog).
      IF probes IS NULL THEN
        probes:=public.morlet_envelope_probe_relation(collection,window_size);
        IF probes IS NOT NULL THEN
          SELECT e.extnamespace::regnamespace::text INTO vschema FROM pg_extension e WHERE e.extname='vector';
        END IF;
      END IF;
      IF probes IS NOT NULL THEN
        EXECUTE format('INSERT INTO %s SELECT recording_id,window_size,start_sample,public.morlet_envelope_decode(envelope)::%I.halfvec
          FROM %s WHERE recording_id=$1 AND window_size=$2 AND start_sample BETWEEN $3 AND $4 ON CONFLICT DO NOTHING',probes,vschema,rel)
          USING recording_id,window_size,s,s+(n-1)::bigint*stride;
      END IF;
    END IF;
    written:=written+n;
    s:=s+n::bigint*stride;
  END LOOP;
  -- Legacy float8[] collections: one window at a time.
  WHILE NOT rels.encoded AND s+window_size<=recording_end AND written<max_windows LOOP
    EXECUTE format('INSERT INTO %s(recording_id,window_size,start_sample,envelope)
      SELECT $1,$2,$3,public.morlet_envelope_encode(i.samples)
      FROM public.morlet_read_interval($4,$1,$3,$3+$2) i ON CONFLICT DO NOTHING',rel)
      USING recording_id,window_size,s,collection;
    -- Probe search may have been enabled by a transaction that committed while this insert waited
    -- for its lock; re-check so that these windows get their vector rows (no lasting probe_backlog).
    IF probes IS NULL THEN
      probes:=public.morlet_envelope_probe_relation(collection,window_size);
      IF probes IS NOT NULL THEN
        SELECT e.extnamespace::regnamespace::text INTO vschema FROM pg_extension e WHERE e.extname='vector';
      END IF;
    END IF;
    IF probes IS NOT NULL THEN
      EXECUTE format('INSERT INTO %s SELECT recording_id,window_size,start_sample,public.morlet_envelope_decode(envelope)::%I.halfvec
        FROM %s WHERE recording_id=$1 AND window_size=$2 AND start_sample=$3 ON CONFLICT DO NOTHING',probes,vschema,rel)
        USING recording_id,window_size,s;
    END IF;
    written:=written+1;
    s:=s+stride;
  END LOOP;
  RETURN written;
END $$;

-- Exact scan of every eligible stored envelope (same rate, channel, unit and
-- window; metadata containment), scored in Rust against the query's
-- full-precision envelope. Lag tolerance is a fraction of the envelope length
-- (0.075 reproduces the evaluated +/-30 envelope samples at 4096-sample windows). Above max_candidates the
-- call fails rather than sampling. Source exclusion uses the optional
-- exclude_* arguments (NULL disables it). Overlapping windows of one recording
-- are suppressed greedily in rank order before top_k.
-- The scan is one statement fetching the best m rows into arrays (not a cursor
-- loop), so PostgreSQL can run it with parallel workers. Suppression then walks
-- those rows; if it uses them up before top_k survive while more candidates
-- remain, the statement is repeated with 4x m. Results equal a full ordered scan.
-- Recording start time in nanoseconds since 1970 from its metadata: `start_ns` (exact, as the Python SDK
-- writes it), else the first of `start`, `starttime`, `start_time` (ISO 8601, as the importers write it),
-- as the SDK and the MCP server read it; NULL when none is present and valid.
CREATE FUNCTION public.morlet_recording_start_ns(metadata jsonb) RETURNS numeric
-- PL/pgSQL, not SQL: an inlined SQL CASE can have its casts folded at plan time (PostgreSQL 18) before the
-- validity test runs.
LANGUAGE plpgsql STABLE PARALLEL SAFE SET search_path=pg_catalog AS $$
DECLARE v text;
BEGIN
  IF pg_input_is_valid(metadata->>'start_ns','numeric') THEN RETURN (metadata->>'start_ns')::numeric; END IF;
  v:=coalesce(nullif(metadata->>'start',''),nullif(metadata->>'starttime',''),nullif(metadata->>'start_time',''));
  IF pg_input_is_valid(v,'timestamptz') THEN RETURN extract(epoch FROM v::timestamptz)*1000000000; END IF;
  RETURN NULL;
END $$;

-- Time filters of the search functions as per-recording window-start bounds: windows of `window_size`
-- samples of an eligible recording whose whole span lies in [from_ns, to_ns) start in [lo, hi]. Recordings
-- without a start time (morlet_recording_start_ns) have no row, so time filters exclude them.
CREATE FUNCTION public.morlet_time_bounds(collection regclass,sample_rate_hz float8,channel text,unit text,
  metadata_filter jsonb,window_size integer,from_ns numeric,to_ns numeric)
RETURNS TABLE(recording_id text,lo bigint,hi bigint)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  RETURN QUERY EXECUTE format('SELECT x.recording_id,x.lo,x.hi FROM (
      SELECT r.recording_id,
        CASE WHEN $5 IS NULL THEN 0 ELSE greatest(0,ceil(($5-r.t)*r.sample_rate_hz::numeric/1000000000)) END::bigint AS lo,
        CASE WHEN $6 IS NULL THEN 9223372036854775807 ELSE least(9223372036854775807,
          floor(($6-r.t)*r.sample_rate_hz::numeric/1000000000)-$7) END::bigint AS hi
      FROM (SELECT c.recording_id,c.sample_rate_hz,public.morlet_recording_start_ns(c.metadata) AS t FROM %s c
            WHERE c.sample_rate_hz=$1 AND c.channel=$2 AND c.unit=$3 AND c.metadata @> $4) r
      WHERE r.t IS NOT NULL) x WHERE x.hi>=x.lo',collection)
    USING sample_rate_hz,channel,unit,metadata_filter,from_ns,to_ns,window_size;
END $$;

-- Nanoseconds since 1970 of a timestamp (microsecond precision), for the search time filters.
CREATE FUNCTION public.morlet_time_ns(t timestamptz) RETURNS numeric
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path=pg_catalog AS $$
  SELECT extract(epoch FROM t)*1000000000
$$;

-- Upper time bound for before_only: the start of the query interval in its recording's time.
CREATE FUNCTION public.morlet_before_ns(metadata jsonb,sample_rate_hz float8,interval_start bigint) RETURNS numeric
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE t numeric:=public.morlet_recording_start_ns(metadata);
BEGIN
  IF t IS NULL THEN
    RAISE EXCEPTION 'before_only needs the query recording''s start time (metadata start_ns or start)';
  END IF;
  RETURN t+interval_start::numeric*1000000000/sample_rate_hz::numeric;
END $$;

-- Raises when more windows are eligible than max_candidates; returns the count otherwise.
CREATE FUNCTION public.morlet_check_candidates(candidates bigint,max_candidates integer) RETURNS bigint
LANGUAGE plpgsql STABLE STRICT PARALLEL SAFE SET search_path=pg_catalog AS $$
BEGIN
  IF candidates>max_candidates THEN
    RAISE EXCEPTION '% windows are eligible, more than max_candidates (%)',candidates,max_candidates
      USING HINT=format('Pass max_candidates => %s, narrow the metadata filter, or enable probe search '
        '(morlet_enable_envelope_probes) and use morlet_search_similar.',candidates);
  END IF;
  RETURN candidates;
END $$;

CREATE FUNCTION public.morlet_envelope_ranked(collection regclass,query_samples float8[],query_rate_hz float8,
  query_channel text,query_unit text,metadata_filter jsonb,top_k integer,max_lag_fraction float8,
  suppress_overlaps boolean,max_candidates integer,
  exclude_recording_id text,exclude_start bigint,exclude_end bigint,time_from_ns numeric,time_to_ns numeric)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel regclass; width integer; query_envelope float8[]; lag integer; candidates bigint; m bigint;
  ids text[]; starts bigint[]; ends bigint[]; dists float8[]; metas jsonb[]; n integer; i integer;
  picked integer[]; idx integer; blocked boolean; screened boolean; kept jsonb;
  b_ids text[]; b_lo bigint[]; b_hi bigint[]; tjoin text:='';
BEGIN
  IF collection IS NULL OR query_samples IS NULL OR query_channel IS NULL OR query_unit IS NULL THEN
    RAISE EXCEPTION 'collection, query samples, channel and unit must not be NULL';
  END IF;
  IF top_k IS NULL OR top_k NOT BETWEEN 1 AND 100 OR metadata_filter IS NULL OR jsonb_typeof(metadata_filter)<>'object'
    OR max_lag_fraction IS NULL OR NOT (max_lag_fraction BETWEEN 0 AND 0.5) OR max_candidates IS NULL OR max_candidates<1
    OR query_rate_hz IS NULL OR suppress_overlaps IS NULL THEN
    RAISE EXCEPTION 'invalid top_k, metadata filter, max_lag_fraction (0..0.5) or max_candidates';
  END IF;
  rel:=public.morlet_envelope_relation(collection);
  width:=cardinality(query_samples);
  query_envelope:=public.morlet_envelope(query_samples);
  lag:=floor(max_lag_fraction*cardinality(query_envelope));
  -- Time filters become per-recording window-start bounds, joined as a relation (keeps the scan parallel).
  IF time_from_ns IS NOT NULL OR time_to_ns IS NOT NULL THEN
    SELECT coalesce(array_agg(b.recording_id),'{}'),coalesce(array_agg(b.lo),'{}'),coalesce(array_agg(b.hi),'{}')
      INTO b_ids,b_lo,b_hi
      FROM public.morlet_time_bounds(collection,query_rate_hz,query_channel,query_unit,metadata_filter,width,
        time_from_ns,time_to_ns) b;
    tjoin:=' JOIN unnest($%1$s::text[],$%2$s::bigint[],$%3$s::bigint[]) b(rid,lo,hi) ON b.rid=e.recording_id AND e.start_sample BETWEEN b.lo AND b.hi';
  END IF;
  -- Screened path (default): a parallel aggregate bounds every eligible window's distance in float32 and
  -- keeps each window that could be among the m nearest (morlet_core::envelope_screen); only those are
  -- scored by morlet_envelope_distance, sorted and cut as in the full path, so results are identical. The
  -- same scan counts the eligible windows for the max_candidates check, which raises before any window is
  -- scored; once a scan has seen more than max_candidates windows it stops screening and only counts.
  -- SET morlet.exact_screen = off scores every eligible window (the previous path), and so does a
  -- query too large to prepare for screening (window 32768 from lag fraction 0.1, 16384 at 0.5).
  screened:=coalesce(current_setting('morlet.exact_screen',true),'on')<>'off'
    AND public.morlet_envelope_screen_supported(cardinality(query_envelope),lag);
  IF NOT screened THEN
    EXECUTE format('SELECT count(*) FROM %s e JOIN %s r ON r.recording_id=e.recording_id%s
      WHERE e.window_size=$1 AND r.sample_rate_hz=$2 AND r.channel=$3 AND r.unit=$4 AND r.metadata @> $5',rel,collection,
      format(tjoin,6,7,8))
      INTO candidates USING width,query_rate_hz,query_channel,query_unit,metadata_filter,b_ids,b_lo,b_hi;
    PERFORM public.morlet_check_candidates(candidates,max_candidates);
  END IF;
  m:=CASE WHEN suppress_overlaps THEN greatest(200,20*top_k) ELSE top_k END;
  LOOP
    IF screened THEN
      -- Screen and count in one scan, refuse over budget, and only then score the kept windows (#279): the
      -- budget check never waits for scoring.
      EXECUTE format('SELECT count(*),public.morlet_envelope_screen(e.envelope,e.recording_id,
            e.start_sample,public.morlet_float8_le_bytes($6),$7,$8::integer,$9::bigint)
            FILTER (WHERE $10::text IS NULL OR e.recording_id<>$10 OR e.start_sample+e.window_size<=$11 OR e.start_sample>=$12)
          FROM %1$s e JOIN %2$s r ON r.recording_id=e.recording_id%3$s
          WHERE e.window_size=$1 AND r.sample_rate_hz=$2 AND r.channel=$3 AND r.unit=$4 AND r.metadata @> $5',
          rel,collection,format(tjoin,13,14,15))
        INTO candidates,kept
        USING width,query_rate_hz,query_channel,query_unit,metadata_filter,query_envelope,lag,m,max_candidates,
          exclude_recording_id,exclude_start,exclude_end,b_ids,b_lo,b_hi;
      PERFORM public.morlet_check_candidates(candidates,max_candidates);
      EXECUTE format('WITH k AS (SELECT x->>0 AS recording_id,(x->>1)::bigint AS start_sample FROM jsonb_array_elements($4) x)
        SELECT array_agg(s.recording_id ORDER BY s.o),array_agg(s.start_sample ORDER BY s.o),
          array_agg(s.end_sample ORDER BY s.o),array_agg(s.distance ORDER BY s.o),array_agg(s.metadata ORDER BY s.o)
        FROM (SELECT t.*,row_number() OVER (ORDER BY t.distance,t.recording_id,t.start_sample) AS o FROM (
          SELECT e.recording_id,e.start_sample,e.start_sample+e.window_size AS end_sample,
            public.morlet_envelope_distance($2,e.envelope,$3) AS distance,r.metadata
          FROM k JOIN %1$s e ON e.recording_id=k.recording_id AND e.window_size=$1 AND e.start_sample=k.start_sample
          JOIN %2$s r ON r.recording_id=e.recording_id
          ORDER BY distance,e.recording_id,e.start_sample LIMIT $5) t) s',rel,collection)
        INTO ids,starts,ends,dists,metas
        USING width,query_envelope,lag,coalesce(kept,'[]'::jsonb),m;
    ELSE
    EXECUTE format('SELECT array_agg(s.recording_id ORDER BY s.o),array_agg(s.start_sample ORDER BY s.o),
        array_agg(s.end_sample ORDER BY s.o),array_agg(s.distance ORDER BY s.o),array_agg(s.metadata ORDER BY s.o)
      FROM (SELECT t.*,row_number() OVER (ORDER BY t.distance,t.recording_id,t.start_sample) AS o FROM (
        SELECT e.recording_id,e.start_sample,e.start_sample+e.window_size AS end_sample,
          public.morlet_envelope_distance($6,e.envelope,$7) AS distance,r.metadata
        FROM %s e JOIN %s r ON r.recording_id=e.recording_id%s
        WHERE e.window_size=$1 AND r.sample_rate_hz=$2 AND r.channel=$3 AND r.unit=$4 AND r.metadata @> $5
          AND ($8::text IS NULL OR e.recording_id<>$8 OR e.start_sample+e.window_size<=$9 OR e.start_sample>=$10)
        ORDER BY distance,e.recording_id,e.start_sample LIMIT $11) t) s',rel,collection,format(tjoin,12,13,14))
      INTO ids,starts,ends,dists,metas
      USING width,query_rate_hz,query_channel,query_unit,metadata_filter,query_envelope,lag,
        exclude_recording_id,exclude_start,exclude_end,m,b_ids,b_lo,b_hi;
    END IF;
    n:=coalesce(cardinality(ids),0);
    picked:=ARRAY[]::integer[];
    FOR i IN 1..n LOOP
      blocked:=false;
      IF suppress_overlaps THEN
        FOREACH idx IN ARRAY picked LOOP
          IF ids[idx]=ids[i] AND starts[idx]<ends[i] AND ends[idx]>starts[i] THEN
            blocked:=true; EXIT;
          END IF;
        END LOOP;
      END IF;
      CONTINUE WHEN blocked;
      picked:=picked||i;
      EXIT WHEN cardinality(picked)=top_k;
    END LOOP;
    EXIT WHEN cardinality(picked)=top_k OR n<m;
    m:=m*4;
  END LOOP;
  RETURN QUERY SELECT ids[p],starts[p],ends[p],dists[p],metas[p] FROM unnest(picked) WITH ORDINALITY u(p,k) ORDER BY k;
END $$;

CREATE FUNCTION public.morlet_search_envelopes(collection regclass,query_samples float8[],query_rate_hz float8,
  query_channel text,query_unit text,metadata_filter jsonb DEFAULT '{}'::jsonb,top_k integer DEFAULT 10,
  max_lag_fraction float8 DEFAULT 0.075,suppress_overlaps boolean DEFAULT true,max_candidates integer DEFAULT 1000000,
  time_from timestamptz DEFAULT NULL,time_to timestamptz DEFAULT NULL)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT * FROM public.morlet_envelope_ranked(collection,query_samples,query_rate_hz,query_channel,query_unit,
    metadata_filter,top_k,max_lag_fraction,suppress_overlaps,max_candidates,NULL,NULL,NULL,
    public.morlet_time_ns(time_from),public.morlet_time_ns(time_to))
$$;

-- Query by a stored interval. With exclude_source, windows overlapping the
-- source interval in its own recording are not returned. Time filters (SQL 0.3.3) keep windows whose span
-- lies in [time_from, time_to); before_only keeps windows that end before the query interval starts. Both
-- use recording start times from the metadata (morlet_recording_start_ns) and exclude recordings without.
CREATE FUNCTION public.morlet_search_envelope_interval(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,metadata_filter jsonb DEFAULT '{}'::jsonb,top_k integer DEFAULT 10,
  max_lag_fraction float8 DEFAULT 0.075,exclude_source boolean DEFAULT true,suppress_overlaps boolean DEFAULT true,
  max_candidates integer DEFAULT 1000000,time_from timestamptz DEFAULT NULL,time_to timestamptz DEFAULT NULL,
  before_only boolean DEFAULT false)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; to_ns numeric;
BEGIN
  IF collection IS NULL OR source_recording_id IS NULL OR interval_start IS NULL OR interval_end IS NULL
    OR exclude_source IS NULL OR before_only IS NULL THEN
    RAISE EXCEPTION 'only time_from and time_to may be NULL';
  END IF;
  SELECT * INTO source FROM public.morlet_read_interval(collection,source_recording_id,interval_start,interval_end);
  to_ns:=public.morlet_time_ns(time_to);
  IF before_only THEN
    to_ns:=least(to_ns,public.morlet_before_ns(source.metadata,source.sample_rate_hz,interval_start));
  END IF;
  RETURN QUERY SELECT * FROM public.morlet_envelope_ranked(collection,source.samples,source.sample_rate_hz,
    source.channel,source.unit,metadata_filter,top_k,max_lag_fraction,suppress_overlaps,max_candidates,
    CASE WHEN exclude_source THEN source_recording_id END,interval_start,interval_end,
    public.morlet_time_ns(time_from),to_ns);
END $$;

-- Probe search (opt-in; needs pgvector). A float16 vector copy of one window size's
-- envelopes with an inner-product HNSW index. Each lag term of morlet_envelope_distance is
-- the inner product of the stored envelope with a zero-padded shifted query
-- (morlet_envelope_probe), so a grid of shifted probes retrieves candidates that the exact
-- scorer then re-ranks. Approximate: see docs/durable-ingestion.md#probe-search. Objects
-- that use pgvector are created with dynamic SQL so pg_morlet does not depend on it.
CREATE FUNCTION public.morlet_envelope_probe_relation(collection regclass,window_size integer)
RETURNS regclass LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel regclass;
BEGIN
  SELECT to_regclass(format('%I.%I',n.nspname,c.relname||'_probes_'||window_size)) INTO rel
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection;
  RETURN rel;
END $$;

-- Creates <collection>_probes_<window_size>, fills it from the stored envelopes and builds
-- the index. Afterwards morlet_build_envelopes keeps it current; rows follow their envelope
-- (foreign key, ON DELETE CASCADE). DROP TABLE disables it.
CREATE FUNCTION public.morlet_enable_envelope_probes(collection regclass,window_size integer DEFAULT 4096)
RETURNS regclass LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE env regclass; vschema text; schema_name text; collection_name text; rel regclass;
BEGIN
  IF window_size NOT IN (512,1024,2048,4096,8192,16384,32768) THEN
    RAISE EXCEPTION 'window_size must be a power of two in 512..32768';
  END IF;
  env:=public.morlet_envelope_relation(collection);
  SELECT e.extnamespace::regnamespace::text INTO vschema FROM pg_extension e WHERE e.extname='vector';
  IF vschema IS NULL THEN
    RAISE EXCEPTION 'probe search needs the pgvector extension (CREATE EXTENSION vector)';
  END IF;
  IF public.morlet_envelope_probe_relation(collection,window_size) IS NOT NULL THEN
    RAISE EXCEPTION 'probe search is already enabled for window_size %',window_size;
  END IF;
  SELECT n.nspname,c.relname INTO schema_name,collection_name
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection;
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    recording_id text NOT NULL,
    window_size integer NOT NULL CHECK(window_size=%5$s),
    start_sample bigint NOT NULL,
    v %3$I.halfvec(%4$s) NOT NULL,
    PRIMARY KEY(recording_id,start_sample),
    FOREIGN KEY(recording_id,window_size,start_sample) REFERENCES %6$s(recording_id,window_size,start_sample) ON DELETE CASCADE)',
    schema_name,collection_name||'_probes_'||window_size,vschema,(window_size+9)/10,window_size,env);
  rel:=public.morlet_envelope_probe_relation(collection,window_size);
  EXECUTE format('INSERT INTO %s SELECT recording_id,window_size,start_sample,public.morlet_envelope_decode(envelope)::%I.halfvec
    FROM %s WHERE window_size=$1',rel,vschema,env) USING window_size;
  EXECUTE format('CREATE INDEX ON %s USING hnsw (v %I.halfvec_ip_ops) WITH (m=16,ef_construction=64)',rel,vschema);
  IF public.morlet_row_security_enabled(collection) THEN PERFORM public.morlet_enable_row_security(collection); END IF;
  RETURN rel;
END $$;

-- Query by a stored interval, like morlet_search_envelope_interval. Routing (evaluation
-- SS3): when at most exact_threshold windows are eligible, the exact search runs instead.
-- Otherwise candidates come from shifted probes at lags -L, -L+probe_step, ..., L
-- (L = floor(max_lag_fraction*n)), per_probe each from the index, each probe scan restricted
-- to eligible recordings (iterative scan); their union is re-ranked by the exact scorer with
-- the same eligibility, exclusion, ordering and overlap suppression.
CREATE FUNCTION public.morlet_search_envelope_probes(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,metadata_filter jsonb DEFAULT '{}'::jsonb,top_k integer DEFAULT 10,
  max_lag_fraction float8 DEFAULT 0.075,exclude_source boolean DEFAULT true,suppress_overlaps boolean DEFAULT true,
  per_probe integer DEFAULT 50,probe_step integer DEFAULT 5,exact_threshold integer DEFAULT 5000,
  time_from timestamptz DEFAULT NULL,time_to timestamptz DEFAULT NULL,before_only boolean DEFAULT false,
  max_candidates integer DEFAULT 1000000)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record; env regclass; probes regclass; vschema text; q float8[]; width integer; lag integer;
  l integer; lags integer[]; ids text[]:=ARRAY[]::text[]; starts bigint[]:=ARRAY[]::bigint[]; got_ids text[];
  got_starts bigint[]; ef text; its text; eligible text[]; eligible_windows bigint; hit record; picked_ids text[]:=ARRAY[]::text[]; picked_starts bigint[]:=ARRAY[]::bigint[];
  picked_ends bigint[]:=ARRAY[]::bigint[]; picked integer:=0; idx integer; blocked boolean;
  from_ns numeric; to_ns numeric; b_lo bigint[]; b_hi bigint[]; bounded boolean;
BEGIN
  IF collection IS NULL OR source_recording_id IS NULL OR interval_start IS NULL OR interval_end IS NULL
    OR metadata_filter IS NULL OR top_k IS NULL OR max_lag_fraction IS NULL OR exclude_source IS NULL
    OR suppress_overlaps IS NULL OR per_probe IS NULL OR probe_step IS NULL OR exact_threshold IS NULL
    OR before_only IS NULL OR max_candidates IS NULL THEN
    RAISE EXCEPTION 'only time_from and time_to may be NULL';
  END IF;
  IF top_k NOT BETWEEN 1 AND 100 OR jsonb_typeof(metadata_filter)<>'object' OR NOT (max_lag_fraction BETWEEN 0 AND 0.5)
    OR per_probe NOT BETWEEN 1 AND 1000 OR probe_step<1 OR exact_threshold NOT BETWEEN 0 AND 1000000 OR max_candidates<1 THEN
    RAISE EXCEPTION 'invalid top_k (1..100), metadata filter, max_lag_fraction (0..0.5), per_probe (1..1000), probe_step (>= 1), exact_threshold (0..1000000) or max_candidates (>= 1)';
  END IF;
  SELECT * INTO source FROM public.morlet_read_interval(collection,source_recording_id,interval_start,interval_end);
  env:=public.morlet_envelope_relation(collection);
  width:=cardinality(source.samples);
  probes:=public.morlet_envelope_probe_relation(collection,width);
  IF probes IS NULL THEN
    RAISE EXCEPTION 'probe search is not enabled for window_size % (morlet_enable_envelope_probes)',width;
  END IF;
  SELECT e.extnamespace::regnamespace::text INTO vschema FROM pg_extension e WHERE e.extname='vector';
  from_ns:=public.morlet_time_ns(time_from);
  to_ns:=public.morlet_time_ns(time_to);
  IF before_only THEN
    to_ns:=least(to_ns,public.morlet_before_ns(source.metadata,source.sample_rate_hz,interval_start));
  END IF;
  bounded:=from_ns IS NOT NULL OR to_ns IS NOT NULL;
  IF bounded THEN
    -- Time filters: eligible recordings with their window-start bounds, applied in the index scans below.
    SELECT coalesce(array_agg(b.recording_id),'{}'),coalesce(array_agg(b.lo),'{}'),coalesce(array_agg(b.hi),'{}')
      INTO eligible,b_lo,b_hi
      FROM public.morlet_time_bounds(collection,source.sample_rate_hz,source.channel,source.unit,metadata_filter,
        width,from_ns,to_ns) b;
    EXECUTE format('SELECT count(*) FROM (SELECT 1 FROM %s e JOIN unnest($1::text[],$4::bigint[],$5::bigint[]) b(rid,lo,hi)
        ON b.rid=e.recording_id AND e.start_sample BETWEEN b.lo AND b.hi WHERE e.window_size=$2 LIMIT $3) x',env)
      INTO eligible_windows USING eligible,width,exact_threshold::bigint+1,b_lo,b_hi;
  ELSE
    EXECUTE format('SELECT array_agg(r.recording_id) FROM %s r WHERE r.sample_rate_hz=$1 AND r.channel=$2 AND r.unit=$3
      AND r.metadata @> $4',collection) INTO eligible USING source.sample_rate_hz,source.channel,source.unit,metadata_filter;
    eligible:=coalesce(eligible,ARRAY[]::text[]);
    -- Counting stops after exact_threshold+1 windows, so routing costs at most that many index entries.
    EXECUTE format('SELECT count(*) FROM (SELECT 1 FROM %s e WHERE e.recording_id=ANY($1) AND e.window_size=$2 LIMIT $3) x',env)
      INTO eligible_windows USING eligible,width,exact_threshold::bigint+1;
  END IF;
  IF eligible_windows<=exact_threshold THEN
    -- Small gallery: exact search under the caller's budget (#226), the same contract as the exact path:
    -- more eligible windows than max_candidates is an error, never a silent prefix.
    RETURN QUERY SELECT * FROM public.morlet_envelope_ranked(collection,source.samples,source.sample_rate_hz,
      source.channel,source.unit,metadata_filter,top_k,max_lag_fraction,suppress_overlaps,max_candidates,
      CASE WHEN exclude_source THEN source_recording_id END,interval_start,interval_end,from_ns,to_ns);
    RETURN;
  END IF;
  q:=public.morlet_envelope(source.samples);
  lag:=floor(max_lag_fraction*cardinality(q));
  lags:=ARRAY(SELECT generate_series(-lag,lag,probe_step));
  IF lags[cardinality(lags)]<>lag THEN lags:=lags||lag; END IF;
  -- Probe search scores at most per_probe candidates per probe exactly; that is its share of the budget (#226).
  IF cardinality(lags)::bigint*per_probe>max_candidates THEN
    RAISE EXCEPTION 'probe search scores up to % candidate windows (% probes x per_probe %), more than max_candidates (%)',
      cardinality(lags)::bigint*per_probe,cardinality(lags),per_probe,max_candidates
      USING HINT='Pass a larger max_candidates, or a smaller per_probe or max_lag_fraction.';
  END IF;
  -- The index returns at most hnsw.ef_search rows per scan. Use max(per_probe, 100), as in
  -- the evaluated configuration, for this call only.
  ef:=current_setting('hnsw.ef_search',true);
  its:=current_setting('hnsw.iterative_scan',true);
  PERFORM set_config('hnsw.ef_search',greatest(per_probe,100)::text,true);
  PERFORM set_config('hnsw.iterative_scan','relaxed_order',true);
  FOREACH l IN ARRAY lags LOOP
    -- With time filters the bounds are part of the scan's filter, so the iterative scan keeps going
    -- until it has per_probe windows inside them.
    EXECUTE format('SELECT array_agg(p.recording_id),array_agg(p.start_sample) FROM (SELECT recording_id,start_sample
      FROM %s WHERE recording_id=ANY($3)%s ORDER BY v OPERATOR(%I.<#>) $1::%I.halfvec LIMIT $2) p',probes,
      CASE WHEN bounded THEN ' AND start_sample BETWEEN $4[array_position($3,recording_id)] AND $5[array_position($3,recording_id)]' ELSE '' END,
      vschema,vschema)
      INTO got_ids,got_starts USING public.morlet_envelope_probe(q,l),per_probe,eligible,b_lo,b_hi;
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

-- One entry point for interval search: probe search when it is enabled for the window size (it routes
-- small or selective galleries to exact search itself), otherwise exact search. Same result contract as
-- both; the existing functions remain for callers who choose the path explicitly.
CREATE FUNCTION public.morlet_search_similar(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,metadata_filter jsonb DEFAULT '{}'::jsonb,top_k integer DEFAULT 10,
  max_lag_fraction float8 DEFAULT 0.075,exclude_source boolean DEFAULT true,suppress_overlaps boolean DEFAULT true,
  max_candidates integer DEFAULT 1000000,time_from timestamptz DEFAULT NULL,time_to timestamptz DEFAULT NULL,
  before_only boolean DEFAULT false)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  IF interval_start IS NULL OR interval_end IS NULL THEN RAISE EXCEPTION 'only time_from and time_to may be NULL'; END IF;
  -- One budget contract on both routes (#226): max_candidates bounds the windows scored exactly.
  IF max_candidates IS NULL OR max_candidates<1 THEN RAISE EXCEPTION 'max_candidates must be at least 1'; END IF;
  IF interval_end-interval_start BETWEEN 1 AND 32768
    AND public.morlet_envelope_probe_relation(collection,(interval_end-interval_start)::integer) IS NOT NULL THEN
    RETURN QUERY SELECT * FROM public.morlet_search_envelope_probes(collection,source_recording_id,interval_start,
      interval_end,metadata_filter,top_k,max_lag_fraction,exclude_source,suppress_overlaps,
      time_from=>time_from,time_to=>time_to,before_only=>before_only,max_candidates=>max_candidates);
  ELSE
    RETURN QUERY SELECT * FROM public.morlet_search_envelope_interval(collection,source_recording_id,interval_start,
      interval_end,metadata_filter,top_k,max_lag_fraction,exclude_source,suppress_overlaps,max_candidates,
      time_from,time_to,before_only);
  END IF;
END $$;

-- Similar intervals with their evidence inline (SQL 0.3.3), so a reader or an agent can judge each match
-- without further calls: UTC start and end (when the recording has a start time), count, min, max, mean and
-- RMS of the matched interval, and an overview of `overview_buckets` equal parts as [[start, end, min, max,
-- rms], ...]. Same arguments and order as morlet_search_similar. The statistics need encoded chunks (NULL
-- otherwise); they equal morlet_interval_stats of the match.
CREATE FUNCTION public.morlet_search_similar_evidence(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,metadata_filter jsonb DEFAULT '{}'::jsonb,top_k integer DEFAULT 10,
  max_lag_fraction float8 DEFAULT 0.075,exclude_source boolean DEFAULT true,suppress_overlaps boolean DEFAULT true,
  max_candidates integer DEFAULT 1000000,time_from timestamptz DEFAULT NULL,time_to timestamptz DEFAULT NULL,
  before_only boolean DEFAULT false,overview_buckets integer DEFAULT 16)
RETURNS TABLE(rank integer,recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb,
  start_utc timestamptz,end_utc timestamptz,sample_count bigint,min float8,max float8,mean float8,rms float8,
  overview jsonb)
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE hit record; k integer:=0; encoded boolean; rate float8; t0 numeric; b float8[]; i integer;
BEGIN
  IF overview_buckets IS NULL OR overview_buckets NOT BETWEEN 1 AND 1024 THEN
    RAISE EXCEPTION 'overview_buckets must be 1..1024';
  END IF;
  SELECT r.encoded INTO encoded FROM public.morlet_stream_relations(collection) r;
  FOR hit IN SELECT * FROM public.morlet_search_similar(collection,source_recording_id,interval_start,interval_end,
      metadata_filter,top_k,max_lag_fraction,exclude_source,suppress_overlaps,max_candidates,time_from,time_to,before_only)
  LOOP
    k:=k+1;
    rank:=k; recording_id:=hit.recording_id; start_sample:=hit.start_sample; end_sample:=hit.end_sample;
    distance:=hit.distance; metadata:=hit.metadata;
    EXECUTE format('SELECT sample_rate_hz FROM %s WHERE recording_id=$1',collection) INTO rate USING hit.recording_id;
    t0:=public.morlet_recording_start_ns(hit.metadata);
    start_utc:=to_timestamp((t0+hit.start_sample::numeric*1000000000/rate::numeric)/1000000000);
    end_utc:=to_timestamp((t0+hit.end_sample::numeric*1000000000/rate::numeric)/1000000000);
    sample_count:=NULL; min:=NULL; max:=NULL; mean:=NULL; rms:=NULL; overview:=NULL;
    IF encoded THEN
      b:=public.morlet_interval_buckets(collection,hit.recording_id,hit.start_sample,hit.end_sample,1,'evidence');
      sample_count:=b[3]::bigint; min:=b[4]; max:=b[5]; mean:=b[6]; rms:=b[7];
    END IF;
    -- The overview splits the matched samples into equal parts (at most 32,768 samples are read).
    SELECT public.morlet_samples_overview(x.samples,overview_buckets) INTO b
      FROM public.morlet_read_interval(collection,hit.recording_id,hit.start_sample,hit.end_sample) x;
    SELECT jsonb_agg(jsonb_build_array(hit.start_sample+b[5*j+1]::bigint,hit.start_sample+b[5*j+2]::bigint,
        b[5*j+3],b[5*j+4],b[5*j+5]) ORDER BY j)
      INTO overview FROM generate_series(0,cardinality(b)/5-1) j;
    RETURN NEXT;
  END LOOP;
END $$;

-- Context model, phase 1 (docs/context-model.md): a context timeline, context
-- segments, simple-v1 values and compare-to-history, on single recordings in
-- sample coordinates. Opt-in per collection; all tables are caller-owned.
CREATE FUNCTION public.morlet_enable_context(collection regclass)
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s text; c text;
BEGIN
  SELECT n.nspname,cl.relname INTO s,c FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE cl.oid=collection;
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    ctx_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recording_id text NOT NULL REFERENCES %1$I.%3$I(recording_id) ON DELETE CASCADE,
    kind text NOT NULL CHECK(kind IN (''interval'',''event'')),
    key text NOT NULL CHECK(key<>''''),
    value jsonb NOT NULL,
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    end_sample bigint CHECK(end_sample>start_sample),
    batch_id text NOT NULL,
    supersedes bigint REFERENCES %1$I.%2$I(ctx_id),
    retracted boolean NOT NULL DEFAULT false,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    recorded_by text NOT NULL DEFAULT current_user,
    CHECK((kind=''event'')=(end_sample IS NULL)))',s,c||'_ctx',c);
  EXECUTE format('CREATE INDEX ON %I.%I(recording_id,key,start_sample)',s,c||'_ctx');
  EXECUTE format('CREATE UNIQUE INDEX ON %I.%I(supersedes) WHERE supersedes IS NOT NULL',s,c||'_ctx');
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    segment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recording_id text NOT NULL REFERENCES %1$I.%3$I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    end_sample bigint NOT NULL CHECK(end_sample>start_sample),
    context jsonb NOT NULL, occurrence jsonb NOT NULL, ctx_ids bigint[] NOT NULL, spec jsonb NOT NULL,
    stale boolean NOT NULL DEFAULT false,
    UNIQUE(recording_id,start_sample,end_sample,spec))',s,c||'_ctx_segments',c);
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    segment_id bigint NOT NULL REFERENCES %1$I.%3$I(segment_id) ON DELETE CASCADE,
    representation text NOT NULL, vals float8[] COMPRESSION pglz NOT NULL,
    PRIMARY KEY(segment_id,representation))',s,c||'_ctx_values',c||'_ctx_segments');
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    label_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recording_id text NOT NULL REFERENCES %1$I.%3$I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    end_sample bigint NOT NULL CHECK(end_sample>start_sample),
    label text NOT NULL CHECK(label<>''''), note text NOT NULL DEFAULT '''',
    author text NOT NULL DEFAULT current_user, created_at timestamptz NOT NULL DEFAULT now(),
    retracted boolean NOT NULL DEFAULT false)',s,c||'_labels',c);
  IF public.morlet_row_security_enabled(collection) THEN PERFORM public.morlet_enable_row_security(collection); END IF;
END $$;

CREATE FUNCTION public.morlet_context_relations(collection regclass)
RETURNS TABLE(ctx regclass,segments regclass,vals regclass,labels regclass)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s text; c text;
BEGIN
  SELECT n.nspname,cl.relname INTO s,c FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE cl.oid=collection;
  ctx:=to_regclass(format('%I.%I',s,c||'_ctx')); segments:=to_regclass(format('%I.%I',s,c||'_ctx_segments'));
  vals:=to_regclass(format('%I.%I',s,c||'_ctx_values')); labels:=to_regclass(format('%I.%I',s,c||'_labels'));
  IF ctx IS NULL OR segments IS NULL OR vals IS NULL OR labels IS NULL THEN
    RAISE EXCEPTION 'context is not enabled for this collection (morlet_enable_context)';
  END IF;
  RETURN NEXT;
END $$;

-- Serializes context writes (additions, revisions, segment builds) per recording, and additions per batch
-- ID, with transaction-scoped advisory locks: batch first, then recordings in sorted order. Under READ
-- COMMITTED each later statement sees what a writer that held the lock committed, so overlap and retry
-- checks cannot both pass for concurrent transactions. No table privileges are needed. (Under REPEATABLE
-- READ the transaction snapshot predates the lock; use READ COMMITTED for concurrent context writers.)
CREATE FUNCTION public.morlet_lock_context(collection regclass,batch_id text,recording_ids text[])
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE r text;
BEGIN
  IF batch_id IS NOT NULL THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('morlet-ctx-batch/'||collection::oid||'/'||batch_id,0));
  END IF;
  FOR r IN SELECT DISTINCT x FROM unnest(recording_ids) x WHERE x IS NOT NULL ORDER BY x LOOP
    PERFORM pg_advisory_xact_lock(hashtextextended('morlet-ctx-rec/'||collection::oid||'/'||r,0));
  END LOOP;
END $$;

-- Marks stale the segments of a recording that context on `key` in [start_sample, end_sample) can change:
-- segments whose spec uses the key (as a segment or occurrence key) and whose piece, before trimming,
-- overlaps the interval. A new or revised interval can split a segment, change its context, or change its
-- occurrence; morlet_build_context_segments then replaces it (its values are deleted with it).
CREATE FUNCTION public.morlet_invalidate_context_segments(collection regclass,recording_id text,key text,
  start_sample bigint,end_sample bigint)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; n bigint;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  EXECUTE format('UPDATE %s s SET stale=true WHERE s.recording_id=$1 AND NOT s.stale
      AND (s.spec->''keys'' ? $2 OR s.spec->''occurrence_keys'' ? $2)
      AND s.start_sample-coalesce((s.spec->>''trim_samples'')::bigint,0)<$4
      AND $3<s.end_sample+coalesce((s.spec->>''trim_samples'')::bigint,0)',rel.segments)
    USING recording_id,key,start_sample,end_sample;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- Rejects overlapping active intervals of one key in one recording. Active rows
-- are neither retracted nor superseded by a later revision.
CREATE FUNCTION public.morlet_check_context_overlaps(collection regclass,recording_ids text[])
RETURNS void LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; clash record;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  EXECUTE format('WITH act AS (SELECT * FROM %1$s x WHERE x.kind=''interval'' AND NOT x.retracted
      AND x.recording_id=ANY($1) AND NOT EXISTS(SELECT 1 FROM %1$s y WHERE y.supersedes=x.ctx_id))
    SELECT a.recording_id,a.key,a.ctx_id AS a_id,b.ctx_id AS b_id FROM act a JOIN act b
      ON a.recording_id=b.recording_id AND a.key=b.key AND a.ctx_id<b.ctx_id
      AND a.start_sample<b.end_sample AND b.start_sample<a.end_sample LIMIT 1',rel.ctx)
    INTO clash USING recording_ids;
  IF clash.a_id IS NOT NULL THEN
    RAISE EXCEPTION 'overlapping context intervals for key % in recording % (ctx_id % and %)',
      clash.key,clash.recording_id,clash.a_id,clash.b_id;
  END IF;
END $$;

-- rows: JSON array of {"recording_id","key","value","start_sample"[,"end_sample"]};
-- rows without end_sample are events. An identical retry of batch_id returns 0;
-- a different batch under the same id is an error.
CREATE FUNCTION public.morlet_add_context(collection regclass,batch_id text,rows jsonb)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; existing jsonb; incoming jsonb; n bigint;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  IF jsonb_typeof(rows)<>'array' OR jsonb_array_length(rows)=0 OR batch_id='' THEN
    RAISE EXCEPTION 'context rows must be a non-empty JSON array and batch_id non-empty';
  END IF;
  SELECT jsonb_agg(o ORDER BY o::text) INTO incoming FROM (SELECT jsonb_build_object('recording_id',r->>'recording_id',
      'key',r->>'key','value',r->'value','start_sample',(r->>'start_sample')::bigint,
      'end_sample',(r->>'end_sample')::bigint) AS o FROM jsonb_array_elements(rows) r) x;
  -- Concurrent additions of the same batch, or to the same recordings, run one after another.
  PERFORM public.morlet_lock_context(collection,batch_id,
    ARRAY(SELECT r->>'recording_id' FROM jsonb_array_elements(incoming) r));
  EXECUTE format('SELECT jsonb_agg(o ORDER BY o::text) FROM (SELECT jsonb_build_object(''recording_id'',recording_id,
      ''key'',key,''value'',value,''start_sample'',start_sample,''end_sample'',end_sample) AS o
    FROM %s WHERE batch_id=$1) x',rel.ctx) INTO existing USING batch_id;
  IF existing IS NOT NULL THEN
    IF existing=incoming THEN RETURN 0; END IF;
    RAISE EXCEPTION 'context batch % already exists with different rows',batch_id;
  END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(incoming) r WHERE r->>'recording_id' IS NULL OR r->>'key' IS NULL
      OR r->'value' IS NULL OR r->>'start_sample' IS NULL) THEN
    RAISE EXCEPTION 'every context row needs recording_id, key, value and start_sample';
  END IF;
  EXECUTE format('INSERT INTO %s(recording_id,kind,key,value,start_sample,end_sample,batch_id)
    SELECT r->>''recording_id'',CASE WHEN r->>''end_sample'' IS NULL THEN ''event'' ELSE ''interval'' END,
      r->>''key'',r->''value'',(r->>''start_sample'')::bigint,(r->>''end_sample'')::bigint,$2
    FROM jsonb_array_elements($1) r',rel.ctx) USING incoming,batch_id;
  GET DIAGNOSTICS n = ROW_COUNT;
  PERFORM public.morlet_check_context_overlaps(collection,
    ARRAY(SELECT DISTINCT r->>'recording_id' FROM jsonb_array_elements(incoming) r));
  -- Late context changes segments built before it (a new occurrence, a split): mark them stale.
  PERFORM public.morlet_invalidate_context_segments(collection,r->>'recording_id',r->>'key',
      (r->>'start_sample')::bigint,(r->>'end_sample')::bigint)
    FROM jsonb_array_elements(incoming) r WHERE r->>'end_sample' IS NOT NULL;
  RETURN n;
END $$;

-- Appends a revision of an active row (changes may set value, start_sample,
-- end_sample) or a retraction. Segments built from the old row become stale, and so do segments the
-- revised interval overlaps (morlet_invalidate_context_segments).
CREATE FUNCTION public.morlet_revise_context(collection regclass,ctx_id bigint,changes jsonb DEFAULT '{}'::jsonb,
  retract boolean DEFAULT false)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; old record; new_id bigint; rid text; changed record;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  EXECUTE format('SELECT recording_id FROM %s WHERE ctx_id=$1',rel.ctx) INTO rid USING ctx_id;
  PERFORM public.morlet_lock_context(collection,NULL,ARRAY[rid]);
  EXECUTE format('SELECT * FROM %1$s x WHERE x.ctx_id=$1 AND NOT x.retracted
    AND NOT EXISTS(SELECT 1 FROM %1$s y WHERE y.supersedes=x.ctx_id) FOR UPDATE',rel.ctx) INTO old USING ctx_id;
  IF old.ctx_id IS NULL THEN RAISE EXCEPTION 'context row % is not active or not visible',ctx_id; END IF;
  IF jsonb_typeof(changes)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(changes) k
      WHERE k NOT IN ('value','start_sample','end_sample')) THEN
    RAISE EXCEPTION 'changes may only set value, start_sample and end_sample';
  END IF;
  EXECUTE format('INSERT INTO %s(recording_id,kind,key,value,start_sample,end_sample,batch_id,supersedes,retracted)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING ctx_id',rel.ctx) INTO new_id
    USING old.recording_id,old.kind,old.key,coalesce(changes->'value',old.value),
      coalesce((changes->>'start_sample')::bigint,old.start_sample),
      CASE WHEN changes ? 'end_sample' THEN (changes->>'end_sample')::bigint ELSE old.end_sample END,
      'revision:'||old.ctx_id,old.ctx_id,retract;
  PERFORM public.morlet_check_context_overlaps(collection,ARRAY[old.recording_id]);
  EXECUTE format('UPDATE %s SET stale=true WHERE $1=ANY(ctx_ids)',rel.segments) USING old.ctx_id;
  -- The revised interval can also reach segments the old row did not cover.
  IF old.kind='interval' THEN
    EXECUTE format('SELECT start_sample,end_sample FROM %s WHERE ctx_id=$1',rel.ctx) INTO changed USING new_id;
    PERFORM public.morlet_invalidate_context_segments(collection,old.recording_id,old.key,old.start_sample,old.end_sample);
    PERFORM public.morlet_invalidate_context_segments(collection,old.recording_id,old.key,changed.start_sample,changed.end_sample);
  END IF;
  RETURN new_id;
END $$;

-- One segment per maximal intersection of the active intervals of all `keys`,
-- within committed samples. occurrence holds the values of `occurrence_keys`
-- intervals that cover the whole segment (for example the part). Stale segments
-- of this recording and spec are replaced (context added or revised later marks the segments it can
-- change as stale). Returns the number of new segments.
CREATE FUNCTION public.morlet_build_context_segments(collection regclass,recording_id text,keys text[],
  occurrence_keys text[] DEFAULT '{}'::text[],min_samples integer DEFAULT 1,trim_samples integer DEFAULT 0)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; recording_end bigint; spec jsonb; n bigint;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  IF cardinality(keys)=0 OR min_samples<1 OR trim_samples<0 OR keys && occurrence_keys THEN
    RAISE EXCEPTION 'keys must be non-empty, disjoint from occurrence_keys, min_samples >= 1 and trim_samples >= 0';
  END IF;
  keys:=ARRAY(SELECT DISTINCT k FROM unnest(keys) k ORDER BY k);
  occurrence_keys:=ARRAY(SELECT DISTINCT k FROM unnest(occurrence_keys) k ORDER BY k);
  -- Builds wait for context writes to this recording (and the reverse), so a build sees either all or
  -- none of a concurrent addition and its invalidations.
  PERFORM public.morlet_lock_context(collection,NULL,ARRAY[recording_id]);
  EXECUTE format('SELECT (checkpoint->>''end'')::bigint FROM %s WHERE recording_id=$1',collection)
    INTO recording_end USING recording_id;
  IF recording_end IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  spec:=jsonb_build_object('version','ctx-seg-v1','keys',to_jsonb(keys),'occurrence_keys',to_jsonb(occurrence_keys),
    'min_samples',min_samples);
  -- Trimmed segments drop trim_samples at each end (uncertain edges); the spec records it.
  IF trim_samples>0 THEN spec:=spec||jsonb_build_object('trim_samples',trim_samples); END IF;
  EXECUTE format('DELETE FROM %s WHERE recording_id=$1 AND spec=$2 AND stale',rel.segments) USING recording_id,spec;
  EXECUTE format('WITH act AS (SELECT * FROM %1$s x WHERE x.recording_id=$1 AND x.kind=''interval'' AND NOT x.retracted
        AND NOT EXISTS(SELECT 1 FROM %1$s y WHERE y.supersedes=x.ctx_id)),
      pts AS (SELECT DISTINCT p FROM act, LATERAL (VALUES (act.start_sample),(act.end_sample)) v(p) WHERE act.key=ANY($2)),
      pieces AS (SELECT p AS a,lead(p) OVER (ORDER BY p) AS b FROM pts),
      cov AS (SELECT pc.a,pc.b,array_agg(act.ctx_id ORDER BY act.key) AS ids,jsonb_object_agg(act.key,act.value) AS ctx,
          count(*) AS n FROM pieces pc JOIN act ON act.key=ANY($2) AND act.start_sample<=pc.a AND act.end_sample>=pc.b
        WHERE pc.b IS NOT NULL GROUP BY pc.a,pc.b)
    INSERT INTO %2$s(recording_id,start_sample,end_sample,context,occurrence,ctx_ids,spec)
    SELECT $1,cov.a+$7,cov.b-$7,cov.ctx,coalesce(o.occ,''{}''::jsonb),cov.ids||coalesce(o.ids,''{}''::bigint[]),$3
    FROM cov LEFT JOIN LATERAL (SELECT jsonb_object_agg(act.key,act.value) AS occ,array_agg(act.ctx_id ORDER BY act.key) AS ids
        FROM act WHERE act.key=ANY($4) AND act.start_sample<=cov.a AND act.end_sample>=cov.b) o ON true
    WHERE cov.n=cardinality($2) AND cov.b<=$5 AND cov.b-cov.a-2*$7>=$6
    ON CONFLICT DO NOTHING',rel.ctx,rel.segments)
    USING recording_id,keys,spec,occurrence_keys,recording_end,min_samples,trim_samples;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- Computes simple-v1 values for this recording's non-stale segments that lack
-- them, at most max_segments per call. Segments are read in 32768-sample pieces.
CREATE FUNCTION public.morlet_build_context_values(collection regclass,recording_id text,max_segments integer DEFAULT 1000)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; seg record; samples float8[]; piece float8[]; s bigint; rate float8; n bigint:=0;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  IF max_segments NOT BETWEEN 1 AND 100000 THEN RAISE EXCEPTION 'max_segments must be 1..100000'; END IF;
  EXECUTE format('SELECT sample_rate_hz FROM %s WHERE recording_id=$1',collection) INTO rate USING recording_id;
  IF rate IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  FOR seg IN EXECUTE format('SELECT s.segment_id,s.start_sample,s.end_sample FROM %s s WHERE s.recording_id=$1 AND NOT s.stale
      AND NOT EXISTS(SELECT 1 FROM %s v WHERE v.segment_id=s.segment_id AND v.representation=''simple-v1'')
      ORDER BY s.start_sample LIMIT $2',rel.segments,rel.vals) USING recording_id,max_segments
  LOOP
    IF seg.end_sample-seg.start_sample>1048576 THEN
      RAISE EXCEPTION 'segment % has % samples; simple-v1 supports at most 1048576',seg.segment_id,seg.end_sample-seg.start_sample;
    END IF;
    samples:=ARRAY[]::float8[];
    s:=seg.start_sample;
    WHILE s<seg.end_sample LOOP
      SELECT i.samples INTO piece FROM public.morlet_read_interval(collection,recording_id,s,least(s+32768,seg.end_sample)) i;
      samples:=samples||piece;
      s:=least(s+32768,seg.end_sample);
    END LOOP;
    EXECUTE format('INSERT INTO %s(segment_id,representation,vals) VALUES ($1,''simple-v1'',public.morlet_simple_values($2,$3))',
      rel.vals) USING seg.segment_id,samples,rate;
    n:=n+1;
  END LOOP;
  RETURN n;
END $$;

CREATE FUNCTION public.morlet_add_label(collection regclass,recording_id text,start_sample bigint,end_sample bigint,
  label text,note text DEFAULT '')
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; id bigint;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  EXECUTE format('INSERT INTO %s(recording_id,start_sample,end_sample,label,note) VALUES ($1,$2,$3,$4,$5) RETURNING label_id',
    rel.labels) INTO id USING recording_id,start_sample,end_sample,label,note;
  RETURN id;
END $$;

CREATE FUNCTION public.morlet_retract_label(collection regclass,label_id bigint)
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; n bigint;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  EXECUTE format('UPDATE %s SET retracted=true WHERE label_id=$1 AND NOT retracted',rel.labels) USING label_id;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n<>1 THEN RAISE EXCEPTION 'label % is not active or not visible',label_id; END IF;
END $$;

-- History selection shared by compare-to-history and its summary. Parameters: $1 query
-- segment, $2 context match, $3 rate, $4 channel, $5 unit, $6 metadata filter, $7 query
-- occurrence, $8 excluded labels. With population=true the context match and occurrence
-- exclusion are dropped (all comparable segments, for pooled spreads) and the context is
-- returned with the values. cutoff_ns (absolute ns since 1970) keeps only segments that
-- started earlier, for history "before this point"; it needs acquisitions.
-- History counts independent observations (#224): only segments built with the query segment's segmentation spec
-- (keys, occurrence keys, min_samples, trim_samples; segments of another spec describe other boundaries or repeat
-- the same samples), and never a segment that overlaps the query segment's own samples on its recording (another
-- representation of the same occurrence). $1 is always the query segment.
CREATE FUNCTION public.morlet_history_sql(collection regclass,population boolean,cutoff_ns numeric DEFAULT NULL)
RETURNS text LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; acq record; before text:='';
BEGIN
  IF collection IS NULL OR population IS NULL THEN RAISE EXCEPTION 'collection and population are required'; END IF;
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  IF cutoff_ns IS NOT NULL THEN
    -- Only segments that started before the cutoff on the absolute time axis
    -- (acquisition origin + channel offset + sample time); others are excluded.
    SELECT * INTO acq FROM public.morlet_acquisition_relations(collection);
    before:=format(' AND EXISTS(SELECT 1 FROM %s ch JOIN %s a ON a.acquisition_id=ch.acquisition_id
        WHERE ch.recording_id=s.recording_id AND a.origin_utc IS NOT NULL
          AND extract(epoch FROM a.origin_utc)*1000000000+ch.offset_ns
            +s.start_sample::numeric*1000000000/r.sample_rate_hz::numeric<%s)',acq.channels,acq.acq,cutoff_ns);
  END IF;
  RETURN format('SELECT v.vals,s.segment_id,s.recording_id,s.start_sample,s.end_sample,s.occurrence%5$s FROM %1$s s JOIN %2$s v ON v.segment_id=s.segment_id AND v.representation=''simple-v1''
        JOIN %3$s r ON r.recording_id=s.recording_id JOIN %1$s qs ON qs.segment_id=$1
      WHERE s.segment_id<>$1 AND NOT s.stale AND s.spec=qs.spec
        AND NOT (s.recording_id=qs.recording_id AND s.start_sample<qs.end_sample AND qs.start_sample<s.end_sample)%6$s
        AND r.sample_rate_hz=$3 AND r.channel=$4 AND r.unit=$5
        AND r.metadata @> $6
        AND NOT EXISTS(SELECT 1 FROM %4$s l WHERE l.recording_id=s.recording_id AND NOT l.retracted AND l.label=ANY($8)
          AND l.start_sample<s.end_sample AND s.start_sample<l.end_sample)%7$s',
    rel.segments,rel.vals,collection,rel.labels,
    CASE WHEN population THEN ',s.context' ELSE '' END,
    CASE WHEN population THEN ' AND $2 IS NOT NULL AND $7 IS NOT NULL'
      ELSE ' AND s.context @> $2 AND ($7=''{}''::jsonb OR s.occurrence<>$7)' END,
    before);
END $$;

-- Pooled relative spread per simple-v1 value: for every context with at least 3
-- comparable segments, median(|x - median|)/|median|; then the median over contexts,
-- floored at spread_floor (default 1%) so near-constant values cannot turn negligible
-- shifts into large calibrated deviations. NULL where no context qualifies (greatest() ignores
-- NULL, so the floor applies only to an existing estimate). Population
-- size is limited by max_candidates.
CREATE FUNCTION public.morlet_value_spreads(collection regclass,segment_id bigint,
  metadata_filter jsonb DEFAULT '{}'::jsonb,exclude_labels text[] DEFAULT '{}'::text[],max_candidates integer DEFAULT 100000,
  spread_floor float8 DEFAULT 0.01,cutoff_ns numeric DEFAULT NULL)
RETURNS float8[] LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE q record; qr record; pop text; n bigint; spreads float8[];
BEGIN
  IF collection IS NULL OR segment_id IS NULL OR metadata_filter IS NULL OR exclude_labels IS NULL
    OR max_candidates IS NULL OR spread_floor IS NULL THEN
    RAISE EXCEPTION 'only cutoff_ns may be NULL';
  END IF;
  SELECT * INTO q FROM public.morlet_comparable_segment(collection,segment_id);
  EXECUTE format('SELECT sample_rate_hz,channel,unit FROM %s WHERE recording_id=$1',collection) INTO qr USING q.recording_id;
  pop:=public.morlet_history_sql(collection,true,cutoff_ns);
  EXECUTE 'SELECT count(*) FROM ('||pop||' LIMIT $9) h' INTO n
    USING q.segment_id,q.context,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,
      max_candidates::bigint+1;
  IF n>max_candidates THEN
    RAISE EXCEPTION 'spread population exceeds % segments; narrow the metadata filter',max_candidates;
  END IF;
  IF NOT (spread_floor>0) THEN RAISE EXCEPTION 'spread_floor must be positive'; END IF;
  EXECUTE 'WITH pop AS MATERIALIZED ('||pop||'),
      k AS (SELECT generate_series(1,cardinality(public.morlet_simple_value_names())) AS ord),
      g AS (SELECT p.context,k.ord,percentile_cont(0.5) WITHIN GROUP (ORDER BY p.vals[k.ord]) AS med,count(*) AS n
        FROM pop p CROSS JOIN k GROUP BY p.context,k.ord HAVING count(*)>=3),
      lv AS (SELECT p.context,public.morlet_simple_value_floors(percentile_cont(0.5) WITHIN GROUP (ORDER BY p.vals[array_position(public.morlet_simple_value_names(),''rms'')])) AS fl FROM pop p GROUP BY p.context),
      r AS (SELECT g.ord,percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(p.vals[g.ord]-g.med))/abs(g.med) AS rel
        FROM g JOIN lv ON lv.context=g.context JOIN pop p ON p.context=g.context WHERE abs(g.med)>lv.fl[g.ord]
        GROUP BY g.context,g.ord,g.med)
    SELECT array_agg(CASE WHEN m.rel IS NOT NULL THEN greatest(m.rel,$9) END ORDER BY k.ord)
      FROM k CROSS JOIN LATERAL (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r.rel) AS rel FROM r WHERE r.ord=k.ord) m'
    INTO spreads USING q.segment_id,q.context,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,
      spread_floor;
  RETURN spreads;
END $$;

-- The query segment with its simple-v1 values; errors if it cannot be compared.
CREATE FUNCTION public.morlet_comparable_segment(collection regclass,query_segment bigint,
  OUT segment_id bigint,OUT recording_id text,OUT context jsonb,OUT occurrence jsonb,OUT vals float8[])
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; q record; id bigint:=query_segment;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  EXECUTE format('SELECT s.segment_id,s.recording_id,s.context,s.occurrence,s.stale,v.vals FROM %s s
    LEFT JOIN %s v ON v.segment_id=s.segment_id AND v.representation=''simple-v1'' WHERE s.segment_id=$1',rel.segments,rel.vals)
    INTO q USING id;
  IF q.segment_id IS NULL THEN RAISE EXCEPTION 'segment % does not exist or is not visible',id; END IF;
  IF q.stale THEN RAISE EXCEPTION 'segment % is stale; rebuild context segments',id; END IF;
  IF q.vals IS NULL THEN RAISE EXCEPTION 'segment % has no simple-v1 values (morlet_build_context_values)',id; END IF;
  segment_id:=q.segment_id; recording_id:=q.recording_id; context:=q.context; occurrence:=q.occurrence; vals:=q.vals;
END $$;

-- Context match for match_keys (all of the segment's context keys when empty).
CREATE FUNCTION public.morlet_context_match(context jsonb,match_keys text[])
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
DECLARE m jsonb;
BEGIN
  IF cardinality(match_keys)=0 THEN RETURN context; END IF;
  IF EXISTS(SELECT 1 FROM unnest(match_keys) k WHERE NOT context ? k) THEN
    RAISE EXCEPTION 'match_keys must be keys of the segment context %',context;
  END IF;
  SELECT jsonb_object_agg(k,context->k) INTO m FROM unnest(match_keys) k;
  RETURN m;
END $$;

-- Compares one segment's simple-v1 values with history: other non-stale
-- segments whose context contains the query's values for match_keys (all of the
-- query's context keys when empty), on recordings with the same rate, channel
-- and unit and matching metadata, excluding the query's own occurrence and any
-- segment overlapping an active label in exclude_labels. status says whether the
-- calibrated deviation could be computed; robust_z is NULL when the history's MAD is 0.
-- zero_median: the history median is 0 or, for values in channel units, at or below
-- morlet_simple_value_floors of the history's median rms (rounding residue).
-- calibrated_deviation divides relative_deviation by the
-- value's pooled spread (morlet_value_spreads), so noisy values weigh less (C2's M03
-- note); relative_deviation is the uncalibrated score of evaluations C1 and C2.
CREATE FUNCTION public.morlet_compare_to_history(collection regclass,segment_id bigint,
  match_keys text[] DEFAULT '{}'::text[],metadata_filter jsonb DEFAULT '{}'::jsonb,
  exclude_labels text[] DEFAULT '{}'::text[],min_history integer DEFAULT 5,max_candidates integer DEFAULT 10000,
  before_only boolean DEFAULT false)
RETURNS TABLE(value_name text,value float8,hist_n integer,hist_median float8,relative_deviation float8,
  robust_z float8,quantile float8,spread float8,calibrated_deviation float8,status text,query_occurrence jsonb)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE q record; qr record; match jsonb; candidates bigint; history text; spreads float8[]; cutoff numeric;
BEGIN
  IF min_history<1 OR max_candidates<1 OR jsonb_typeof(metadata_filter)<>'object' THEN
    RAISE EXCEPTION 'invalid min_history, max_candidates or metadata filter';
  END IF;
  SELECT * INTO q FROM public.morlet_comparable_segment(collection,segment_id);
  match:=public.morlet_context_match(q.context,match_keys);
  EXECUTE format('SELECT sample_rate_hz,channel,unit FROM %s WHERE recording_id=$1',collection) INTO qr USING q.recording_id;
  cutoff:=CASE WHEN before_only THEN public.morlet_segment_start_ns(collection,q.segment_id) END;
  IF before_only AND cutoff IS NULL THEN
    RAISE EXCEPTION 'before_only needs the segment''s recording in an acquisition with origin_utc';
  END IF;
  history:=public.morlet_history_sql(collection,false,cutoff);
  EXECUTE 'SELECT count(*) FROM ('||history||' LIMIT $9) h' INTO candidates
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,
      max_candidates::bigint+1;
  IF candidates>max_candidates THEN
    RAISE EXCEPTION 'history exceeds % segments; narrow match_keys or the metadata filter',max_candidates;
  END IF;
  spreads:=public.morlet_value_spreads(collection,q.segment_id,metadata_filter,exclude_labels,100000,0.01,cutoff);
  RETURN QUERY EXECUTE 'WITH hist AS MATERIALIZED ('||history||'),
    budget AS (SELECT count(*)::integer AS n FROM hist),
    q AS (SELECT name,ord,$9[ord] AS x FROM unnest(public.morlet_simple_value_names()) WITH ORDINALITY AS t(name,ord)),
    st AS (SELECT q.name,q.ord,q.x,b.n,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY h.vals[q.ord]) AS med,
        count(*) FILTER (WHERE h.vals[q.ord]<q.x) AS below,count(*) FILTER (WHERE h.vals[q.ord]=q.x) AS equal
      FROM q CROSS JOIN budget b LEFT JOIN hist h ON true GROUP BY q.name,q.ord,q.x,b.n),
    lv AS (SELECT public.morlet_simple_value_floors(percentile_cont(0.5) WITHIN GROUP (ORDER BY h.vals[array_position(public.morlet_simple_value_names(),''rms'')])) AS fl FROM hist h),
    sp AS (SELECT st.*,(SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(h.vals[st.ord]-st.med)) FROM hist h) AS mad,
        $11[st.ord] AS spread,(SELECT lv.fl[st.ord] FROM lv) AS fl FROM st)
    SELECT name,x,n,CASE WHEN n>=$10 THEN med END,
      CASE WHEN n>=$10 AND abs(med)>fl THEN (x-med)/abs(med) END,
      CASE WHEN n>=$10 AND mad>0 THEN (x-med)/(1.4826*mad) END,
      CASE WHEN n>=$10 THEN (below+0.5*equal)::float8/n END,
      spread,
      CASE WHEN n>=$10 AND abs(med)>fl AND spread>0 THEN abs(x-med)/abs(med)/spread END,
      CASE WHEN n<$10 THEN ''insufficient_history'' WHEN abs(med)<=fl THEN ''zero_median''
        WHEN spread IS NULL THEN ''no_pooled_spread'' ELSE ''ok'' END,$7
    FROM sp ORDER BY ord'
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,
      q.vals,min_history,spreads;
END $$;

-- One row per segment comparison. score is the maximum relative deviation over
-- score_values. The default, duration_s, std, crest and kurtosis, is the evaluated summary of
-- C2 and C3 on vibration (C2: AUROC 0.81 on Bosch CNC; C3: 0.88 on MillingVibes). For
-- level signals such as motor current add the level (p75 or mean): their level is the
-- signal (C1 found the p75 of DC spindle current informative), while for vibration the mean
-- is near zero and noisy. score_quantile ranks it among leave-one-out
-- scores of the same history (a per-context calibration; NULL above 2000 history
-- segments), top_value names the value behind it, and the duration deviation is
-- reported separately. calibrated_score (calibrated-v1: deviations divided by pooled
-- spreads over all values) is experimental: it ranked worse than score in C3.
CREATE FUNCTION public.morlet_compare_summary(collection regclass,segment_id bigint,
  match_keys text[] DEFAULT '{}'::text[],metadata_filter jsonb DEFAULT '{}'::jsonb,
  exclude_labels text[] DEFAULT '{}'::text[],min_history integer DEFAULT 5,max_candidates integer DEFAULT 10000,
  before_only boolean DEFAULT false,
  score_values text[] DEFAULT ARRAY['duration_s','std','crest','kurtosis'])
RETURNS TABLE(score float8,score_quantile float8,top_value text,top_relative_deviation float8,
  duration_relative_deviation float8,calibrated_score float8,hist_n integer,status text,query_occurrence jsonb)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE q record; qr record; match jsonb; history text; flat float8[]; n integer; spreads float8[]; res float8[]; cutoff numeric;
  names text[]:=public.morlet_simple_value_names(); width integer; unit_spreads float8[];
BEGIN
  IF min_history<1 OR max_candidates<1 OR jsonb_typeof(metadata_filter)<>'object' THEN
    RAISE EXCEPTION 'invalid min_history, max_candidates or metadata filter';
  END IF;
  IF cardinality(score_values)=0 OR NOT score_values <@ names THEN
    RAISE EXCEPTION 'score_values must be a non-empty subset of %',names;
  END IF;
  width:=cardinality(names);
  SELECT * INTO q FROM public.morlet_comparable_segment(collection,segment_id);
  match:=public.morlet_context_match(q.context,match_keys);
  EXECUTE format('SELECT sample_rate_hz,channel,unit FROM %s WHERE recording_id=$1',collection) INTO qr USING q.recording_id;
  cutoff:=CASE WHEN before_only THEN public.morlet_segment_start_ns(collection,q.segment_id) END;
  IF before_only AND cutoff IS NULL THEN
    RAISE EXCEPTION 'before_only needs the segment''s recording in an acquisition with origin_utc';
  END IF;
  history:=public.morlet_history_sql(collection,false,cutoff);
  -- Refuse an oversized history before flattening it: count at most max_candidates+1 rows (the function is
  -- STABLE, so this count and the read below see the same snapshot).
  EXECUTE 'SELECT count(*) FROM ('||history||' LIMIT $9) h' INTO n
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,
      max_candidates::bigint+1;
  IF n>max_candidates THEN
    RAISE EXCEPTION 'history exceeds % segments; narrow match_keys or the metadata filter',max_candidates;
  END IF;
  EXECUTE 'SELECT coalesce(array_agg(x ORDER BY i,j),''{}''::float8[]) FROM (SELECT row_number() OVER () AS i,h.vals FROM ('
    ||history||') h) t, LATERAL unnest(t.vals) WITH ORDINALITY u(x,j)' INTO flat
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels;
  n:=cardinality(flat)/width;
  query_occurrence:=q.occurrence; hist_n:=n;
  SELECT c.relative_deviation INTO duration_relative_deviation
    FROM public.morlet_compare_to_history(collection,q.segment_id,match_keys,metadata_filter,exclude_labels,min_history,
      max_candidates,before_only) c WHERE c.value_name='duration_s';
  IF n<min_history THEN status:='insufficient_history'; RETURN NEXT; RETURN; END IF;
  -- Spread 1 on the evaluated value set and NULL elsewhere turns the calibrated score
  -- into the plain maximum relative deviation over that set.
  unit_spreads:=ARRAY(SELECT CASE WHEN nm=ANY(score_values) THEN 1.0::float8 END
    FROM unnest(names) WITH ORDINALITY AS t(nm,o) ORDER BY o);
  res:=public.morlet_calibrated_score(q.vals,flat,width,unit_spreads);
  score:=res[1]; score_quantile:=res[2];
  IF res[3]>0 THEN top_value:=names[res[3]::integer]; top_relative_deviation:=score; END IF;
  spreads:=public.morlet_value_spreads(collection,q.segment_id,metadata_filter,exclude_labels,100000,0.01,cutoff);
  calibrated_score:=(public.morlet_calibrated_score(q.vals,flat,width,spreads))[1];
  status:=CASE WHEN score IS NULL THEN 'no_defined_value' ELSE 'ok' END;
  RETURN NEXT;
END $$;

-- Acquisitions (context model phase 2): several channel recordings on one time axis,
-- acquisition time in integer nanoseconds from the acquisition origin. Each channel
-- declares where its sample 0 lies (offset_ns), its clock source and the offset's
-- uncertainty (NULL = exact, a shared DAQ clock). Channels keep their native rates;
-- nothing is resampled. Recordings without an acquisition work as before.
CREATE FUNCTION public.morlet_enable_acquisitions(collection regclass)
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s text; c text;
BEGIN
  SELECT n.nspname,cl.relname INTO s,c FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE cl.oid=collection;
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    acquisition_id text PRIMARY KEY CHECK(acquisition_id<>''''),
    machine text NOT NULL,
    origin_utc timestamptz,
    metadata jsonb NOT NULL DEFAULT ''{}'' CHECK(jsonb_typeof(metadata)=''object''))',s,c||'_acq');
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    acquisition_id text NOT NULL REFERENCES %1$I.%3$I(acquisition_id) ON DELETE CASCADE,
    recording_id text NOT NULL UNIQUE REFERENCES %1$I.%4$I(recording_id) ON DELETE CASCADE,
    role text NOT NULL CHECK(role<>''''),
    offset_ns bigint NOT NULL,
    clock_source text NOT NULL CHECK(clock_source IN (''daq'',''wallclock'',''controller'')),
    offset_uncertainty_ns bigint CHECK(offset_uncertainty_ns>=0),
    PRIMARY KEY(acquisition_id,role))',s,c||'_acq_channels',c||'_acq',c);
  IF public.morlet_row_security_enabled(collection) THEN PERFORM public.morlet_enable_row_security(collection); END IF;
END $$;

CREATE FUNCTION public.morlet_acquisition_relations(collection regclass)
RETURNS TABLE(acq regclass,channels regclass)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE s text; c text;
BEGIN
  SELECT n.nspname,cl.relname INTO s,c FROM pg_class cl JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE cl.oid=collection;
  acq:=to_regclass(format('%I.%I',s,c||'_acq')); channels:=to_regclass(format('%I.%I',s,c||'_acq_channels'));
  IF acq IS NULL OR channels IS NULL THEN
    RAISE EXCEPTION 'acquisitions are not enabled for this collection (morlet_enable_acquisitions)';
  END IF;
  RETURN NEXT;
END $$;

-- Half-open sample interval of acquisition-time interval [t_start_ns, t_end_ns) on a
-- channel: sample n (at offset_ns + n*1e9/rate) belongs to it when its time does, i.e.
-- [ceil((t_start - offset)*rate/1e9), ceil((t_end - offset)*rate/1e9)), clamped at 0 and
-- computed in numeric so boundaries do not depend on float rounding (25.6 kHz has a
-- 39062.5 ns period).
CREATE FUNCTION public.morlet_time_to_samples(sample_rate_hz float8,offset_ns bigint,t_start_ns bigint,t_end_ns bigint,
  OUT start_sample bigint,OUT end_sample bigint)
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
BEGIN
  IF NOT (sample_rate_hz>0 AND sample_rate_hz<'Infinity') OR t_end_ns<t_start_ns THEN
    RAISE EXCEPTION 'sample rate must be positive and t_end_ns >= t_start_ns';
  END IF;
  start_sample:=greatest(0,ceil((t_start_ns-offset_ns)::numeric*sample_rate_hz::numeric/1000000000))::bigint;
  end_sample:=greatest(0,ceil((t_end_ns-offset_ns)::numeric*sample_rate_hz::numeric/1000000000))::bigint;
END $$;

-- Acquisition time (ns, exact numeric) of a channel sample.
CREATE FUNCTION public.morlet_sample_time_ns(sample_rate_hz float8,offset_ns bigint,sample bigint)
RETURNS numeric LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog AS $$
  SELECT offset_ns+sample::numeric*1000000000/sample_rate_hz::numeric
$$;

-- Creates an acquisition, or verifies that an identical one exists (returns false).
CREATE FUNCTION public.morlet_create_acquisition(collection regclass,acquisition_id text,machine text,
  origin_utc timestamptz DEFAULT NULL,metadata jsonb DEFAULT '{}'::jsonb)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; existing record;
BEGIN
  IF collection IS NULL OR acquisition_id IS NULL OR machine IS NULL OR metadata IS NULL THEN
    RAISE EXCEPTION 'only origin_utc may be NULL';
  END IF;
  SELECT * INTO rel FROM public.morlet_acquisition_relations(collection);
  -- One creator per acquisition at a time (#227): a concurrent identical call waits here, then (READ COMMITTED)
  -- the check below sees the committed row and returns false instead of failing with a unique violation.
  -- (ON CONFLICT is not used: under row security it applies the read policy to the new row, which an
  -- acquisition without a channel does not pass yet.)
  PERFORM pg_advisory_xact_lock(hashtextextended('morlet-acq/'||collection::oid||'/'||acquisition_id,0));
  EXECUTE format('SELECT machine,origin_utc,metadata FROM %s WHERE acquisition_id=$1',rel.acq) INTO existing USING acquisition_id;
  IF existing.machine IS NOT NULL THEN
    IF existing.machine=machine AND existing.origin_utc IS NOT DISTINCT FROM origin_utc AND existing.metadata=metadata THEN
      RETURN false;
    END IF;
    RAISE EXCEPTION 'acquisition % already exists with different attributes',acquisition_id;
  END IF;
  EXECUTE format('INSERT INTO %s(acquisition_id,machine,origin_utc,metadata) VALUES ($1,$2,$3,$4)',rel.acq)
    USING acquisition_id,machine,origin_utc,metadata;
  RETURN true;
END $$;

-- Adds a channel recording to an acquisition, or verifies an identical membership.
CREATE FUNCTION public.morlet_add_acquisition_channel(collection regclass,acquisition_id text,recording_id text,
  role text,offset_ns bigint DEFAULT 0,clock_source text DEFAULT 'daq',offset_uncertainty_ns bigint DEFAULT NULL)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; existing record; n bigint;
BEGIN
  IF collection IS NULL OR acquisition_id IS NULL OR recording_id IS NULL OR role IS NULL OR offset_ns IS NULL
    OR clock_source IS NULL THEN
    RAISE EXCEPTION 'only offset_uncertainty_ns may be NULL';
  END IF;
  SELECT * INTO rel FROM public.morlet_acquisition_relations(collection);
  -- One writer per acquisition and per recording at a time (#227), in a fixed order: a concurrent identical
  -- membership waits, then sees the committed row and returns false; a taken role is refused with a message.
  PERFORM pg_advisory_xact_lock(k) FROM unnest(ARRAY[
      hashtextextended('morlet-acq/'||collection::oid||'/'||acquisition_id,0),
      hashtextextended('morlet-acq-rec/'||collection::oid||'/'||recording_id,0)]) k ORDER BY k;
  EXECUTE format('SELECT * FROM %s WHERE recording_id=$1',rel.channels) INTO existing USING recording_id;
  IF existing.recording_id IS NOT NULL THEN
    IF existing.acquisition_id=acquisition_id AND existing.role=role AND existing.offset_ns=offset_ns
      AND existing.clock_source=clock_source AND existing.offset_uncertainty_ns IS NOT DISTINCT FROM offset_uncertainty_ns THEN
      RETURN false;
    END IF;
    RAISE EXCEPTION 'recording % already belongs to an acquisition with different attributes',recording_id;
  END IF;
  IF row_security_active(rel.acq) THEN
    EXECUTE format('SELECT count(*) FROM %s WHERE acquisition_id=$1',rel.acq) INTO n USING acquisition_id;
    IF n=0 THEN
      RAISE EXCEPTION 'acquisition % does not exist or is not visible to this role',acquisition_id
        USING HINT='Under row security, a channel joins an acquisition the caller can see: one with a visible channel, or one created in the same transaction.';
    END IF;
  END IF;
  EXECUTE format('SELECT count(*) FROM %s WHERE acquisition_id=$1 AND role=$2',rel.channels) INTO n USING acquisition_id,role;
  IF n>0 THEN
    RAISE EXCEPTION 'role % of acquisition % is already taken by another recording',role,acquisition_id;
  END IF;
  EXECUTE format('INSERT INTO %s(acquisition_id,recording_id,role,offset_ns,clock_source,offset_uncertainty_ns)
    VALUES ($1,$2,$3,$4,$5,$6)',rel.channels)
    USING acquisition_id,recording_id,role,offset_ns,clock_source,offset_uncertainty_ns;
  RETURN true;
END $$;

-- Absolute start (ns since 1970) of a context segment, or NULL when its recording is not
-- in an acquisition with origin_utc.
CREATE FUNCTION public.morlet_segment_start_ns(collection regclass,segment_id bigint)
RETURNS numeric LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE ctx record; acq record; t numeric;
BEGIN
  SELECT * INTO ctx FROM public.morlet_context_relations(collection);
  SELECT * INTO acq FROM public.morlet_acquisition_relations(collection);
  EXECUTE format('SELECT extract(epoch FROM a.origin_utc)*1000000000+public.morlet_sample_time_ns(r.sample_rate_hz,ch.offset_ns,s.start_sample)
    FROM %s s JOIN %s r ON r.recording_id=s.recording_id JOIN %s ch ON ch.recording_id=s.recording_id
      JOIN %s a ON a.acquisition_id=ch.acquisition_id WHERE s.segment_id=$1',ctx.segments,collection,acq.channels,acq.acq)
    INTO t USING segment_id;
  RETURN t;
END $$;

-- Context in acquisition time: rows {"key","value","t_start_ns"[,"t_end_ns"]} are mapped to
-- every channel of the acquisition in its own sample coordinates (morlet_time_to_samples)
-- and added with morlet_add_context under batch '<batch_id>@<acquisition_id>/<role>'. Intervals that map to
-- no sample on a channel are skipped there. Returns the rows added over all channels; an
-- identical retry returns 0.
CREATE FUNCTION public.morlet_add_acquisition_context(collection regclass,acquisition_id text,batch_id text,rows jsonb)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; ch record; mapped jsonb; n bigint:=0;
BEGIN
  SELECT * INTO rel FROM public.morlet_acquisition_relations(collection);
  IF jsonb_typeof(rows)<>'array' OR jsonb_array_length(rows)=0 THEN RAISE EXCEPTION 'rows must be a non-empty JSON array'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(rows) r WHERE r->>'key' IS NULL OR r->'value' IS NULL OR r->>'t_start_ns' IS NULL) THEN
    RAISE EXCEPTION 'every row needs key, value and t_start_ns';
  END IF;
  FOR ch IN EXECUTE format('SELECT c.recording_id,c.role,c.offset_ns,r.sample_rate_hz FROM %s c JOIN %s r ON r.recording_id=c.recording_id
      WHERE c.acquisition_id=$1 ORDER BY c.role',rel.channels,collection) USING acquisition_id
  LOOP
    SELECT jsonb_agg(CASE WHEN r->>'t_end_ns' IS NULL
        THEN jsonb_build_object('recording_id',ch.recording_id,'key',r->>'key','value',r->'value',
          'start_sample',(public.morlet_time_to_samples(ch.sample_rate_hz,ch.offset_ns,(r->>'t_start_ns')::bigint,(r->>'t_start_ns')::bigint)).start_sample)
        ELSE jsonb_build_object('recording_id',ch.recording_id,'key',r->>'key','value',r->'value',
          'start_sample',m.start_sample,'end_sample',m.end_sample) END)
      INTO mapped
    FROM jsonb_array_elements(rows) r
      LEFT JOIN LATERAL public.morlet_time_to_samples(ch.sample_rate_hz,ch.offset_ns,(r->>'t_start_ns')::bigint,
        coalesce((r->>'t_end_ns')::bigint,(r->>'t_start_ns')::bigint)) m ON true
    WHERE r->>'t_end_ns' IS NULL OR m.end_sample>m.start_sample;
    IF mapped IS NOT NULL THEN
      n:=n+public.morlet_add_context(collection,batch_id||'@'||acquisition_id||'/'||ch.role,mapped);
    END IF;
  END LOOP;
  IF NOT FOUND THEN RAISE EXCEPTION 'acquisition % has no visible channels',acquisition_id; END IF;
  RETURN n;
END $$;

-- One acquisition-time interval read from every channel, each in its own samples
-- (at most 32768 per channel, clamped to the recording's length).
CREATE FUNCTION public.morlet_read_acquisition(collection regclass,acquisition_id text,t_start_ns bigint,t_end_ns bigint)
RETURNS TABLE(role text,recording_id text,sample_rate_hz float8,start_sample bigint,end_sample bigint,
  start_time_ns numeric,samples float8[])
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; ch record; m record; recording_end bigint;
BEGIN
  SELECT * INTO rel FROM public.morlet_acquisition_relations(collection);
  FOR ch IN EXECUTE format('SELECT c.recording_id,c.role,c.offset_ns,r.sample_rate_hz,(r.checkpoint->>''end'')::bigint AS n
      FROM %s c JOIN %s r ON r.recording_id=c.recording_id WHERE c.acquisition_id=$1 ORDER BY c.role',rel.channels,collection)
    USING acquisition_id
  LOOP
    SELECT * INTO m FROM public.morlet_time_to_samples(ch.sample_rate_hz,ch.offset_ns,t_start_ns,t_end_ns);
    recording_end:=least(m.end_sample,ch.n);
    CONTINUE WHEN recording_end<=m.start_sample;
    IF recording_end-m.start_sample>32768 THEN
      RAISE EXCEPTION 'channel % would return % samples; read at most 32768 per channel',ch.role,recording_end-m.start_sample;
    END IF;
    RETURN QUERY SELECT ch.role,ch.recording_id,ch.sample_rate_hz,m.start_sample,recording_end,
      public.morlet_sample_time_ns(ch.sample_rate_hz,ch.offset_ns,m.start_sample),i.samples
      FROM public.morlet_read_interval(collection,ch.recording_id,m.start_sample,recording_end) i;
  END LOOP;
END $$;

-- As morlet_read_acquisition, with each channel's samples as raw IEEE 754 float64 values, little-endian,
-- 8 bytes each (as morlet_read_interval_raw), read in one statement; up to 16,777,216 samples per
-- channel; encoded collections only.
CREATE FUNCTION public.morlet_read_acquisition_raw(collection regclass,acquisition_id text,t_start_ns bigint,t_end_ns bigint)
RETURNS TABLE(role text,recording_id text,sample_rate_hz float8,start_sample bigint,end_sample bigint,
  start_time_ns numeric,samples bytea)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record;
BEGIN
  SELECT * INTO rel FROM public.morlet_acquisition_relations(collection);
  RETURN QUERY EXECUTE format('SELECT w.role,w.recording_id,w.sample_rate_hz,w.s,w.e,
      public.morlet_sample_time_ns(w.sample_rate_hz,w.offset_ns,w.s),public.morlet_read_interval_raw($1,w.recording_id,w.s,w.e)
    FROM (SELECT c.role,c.recording_id,c.offset_ns,r.sample_rate_hz,m.start_sample AS s,
        least(m.end_sample,(r.checkpoint->>''end'')::bigint) AS e
      FROM %s c JOIN %s r ON r.recording_id=c.recording_id
        CROSS JOIN LATERAL public.morlet_time_to_samples(r.sample_rate_hz,c.offset_ns,$3,$4) m
      WHERE c.acquisition_id=$2) w
    WHERE w.e>w.s ORDER BY w.role',rel.channels,collection)
    USING collection,acquisition_id,t_start_ns,t_end_ns;
END $$;

-- Compares one occurrence of a context on every channel of an acquisition, side by side:
-- for each channel, the non-stale segment with exactly this context and occurrence is
-- compared with morlet_compare_summary (status 'no_segment' when a channel has none).
CREATE FUNCTION public.morlet_compare_acquisition(collection regclass,acquisition_id text,context jsonb,occurrence jsonb,
  match_keys text[] DEFAULT '{}'::text[],metadata_filter jsonb DEFAULT '{}'::jsonb,
  exclude_labels text[] DEFAULT '{}'::text[],min_history integer DEFAULT 5,max_candidates integer DEFAULT 10000,
  before_only boolean DEFAULT false,score_values text[] DEFAULT ARRAY['duration_s','std','crest','kurtosis'])
RETURNS TABLE(role text,recording_id text,segment_id bigint,score float8,score_quantile float8,top_value text,
  top_relative_deviation float8,duration_relative_deviation float8,calibrated_score float8,hist_n integer,status text)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; ctx record; ch record; seg bigint;
BEGIN
  SELECT * INTO rel FROM public.morlet_acquisition_relations(collection);
  SELECT * INTO ctx FROM public.morlet_context_relations(collection);
  FOR ch IN EXECUTE format('SELECT c.recording_id,c.role FROM %s c WHERE c.acquisition_id=$1 ORDER BY c.role',rel.channels)
    USING acquisition_id
  LOOP
    EXECUTE format('SELECT min(s.segment_id) FROM %s s WHERE s.recording_id=$1 AND NOT s.stale AND s.context=$2 AND s.occurrence=$3',
      ctx.segments) INTO seg USING ch.recording_id,context,occurrence;
    IF seg IS NULL THEN
      RETURN QUERY SELECT ch.role,ch.recording_id,NULL::bigint,NULL::float8,NULL::float8,NULL::text,NULL::float8,NULL::float8,
        NULL::float8,NULL::integer,'no_segment'::text;
      CONTINUE;
    END IF;
    RETURN QUERY SELECT ch.role,ch.recording_id,seg,s.score,s.score_quantile,s.top_value,s.top_relative_deviation,
      s.duration_relative_deviation,s.calibrated_score,s.hist_n,s.status
      FROM public.morlet_compare_summary(collection,seg,match_keys,metadata_filter,exclude_labels,min_history,max_candidates,
        before_only,score_values) s;
  END LOOP;
END $$;

-- Builds context segments and simple-v1 values on every channel of an acquisition. Each
-- channel's segments are trimmed by its clock uncertainty, ceil(offset_uncertainty_ns *
-- rate / 1e9) samples at each end, so samples that may belong to a neighbouring context
-- interval are left out. Returns the number of new segments over all channels.
CREATE FUNCTION public.morlet_build_acquisition_segments(collection regclass,acquisition_id text,keys text[],
  occurrence_keys text[] DEFAULT '{}'::text[],min_samples integer DEFAULT 1)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; ch record; n bigint:=0;
BEGIN
  SELECT * INTO rel FROM public.morlet_acquisition_relations(collection);
  FOR ch IN EXECUTE format('SELECT c.recording_id,ceil(coalesce(c.offset_uncertainty_ns,0)::numeric*r.sample_rate_hz::numeric
      /1000000000)::integer AS trim FROM %s c JOIN %s r ON r.recording_id=c.recording_id WHERE c.acquisition_id=$1
      ORDER BY c.role',rel.channels,collection) USING acquisition_id
  LOOP
    n:=n+public.morlet_build_context_segments(collection,ch.recording_id,keys,occurrence_keys,min_samples,ch.trim);
    PERFORM public.morlet_build_context_values(collection,ch.recording_id,100000);
  END LOOP;
  IF NOT FOUND THEN RAISE EXCEPTION 'acquisition % has no visible channels',acquisition_id; END IF;
  RETURN n;
END $$;

-- Nearest past segments of the same context: the history of morlet_compare_summary
-- (same filters, budget and before_only), ranked by the root mean square over
-- score_values of (x_query - x_history) / |history median|, ties by recording and
-- start, skipping values whose median is zero or negligible (morlet_compare_to_history's
-- zero_median). vals are the history segment's simple-v1 values, labels its overlapping active
-- labels. top_k up to 1000, so a caller can also draw the whole history distribution.
CREATE FUNCTION public.morlet_history_neighbors(collection regclass,segment_id bigint,
  match_keys text[] DEFAULT '{}'::text[],metadata_filter jsonb DEFAULT '{}'::jsonb,
  exclude_labels text[] DEFAULT '{}'::text[],top_k integer DEFAULT 10,max_candidates integer DEFAULT 10000,
  before_only boolean DEFAULT false,score_values text[] DEFAULT ARRAY['duration_s','std','crest','kurtosis'])
RETURNS TABLE(neighbor_segment_id bigint,recording_id text,start_sample bigint,end_sample bigint,occurrence jsonb,
  distance float8,vals float8[],labels jsonb)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; q record; qr record; match jsonb; history text; n bigint; cutoff numeric;
  names text[]:=public.morlet_simple_value_names(); ords integer[];
BEGIN
  IF top_k NOT BETWEEN 1 AND 1000 OR max_candidates<1 OR jsonb_typeof(metadata_filter)<>'object' THEN
    RAISE EXCEPTION 'invalid top_k (1..1000), max_candidates or metadata filter';
  END IF;
  IF cardinality(score_values)=0 OR NOT score_values <@ names THEN
    RAISE EXCEPTION 'score_values must be a non-empty subset of %',names;
  END IF;
  ords:=ARRAY(SELECT o::integer FROM unnest(names) WITH ORDINALITY AS t(nm,o) WHERE nm=ANY(score_values) ORDER BY o);
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
  SELECT * INTO q FROM public.morlet_comparable_segment(collection,segment_id);
  match:=public.morlet_context_match(q.context,match_keys);
  EXECUTE format('SELECT sample_rate_hz,channel,unit FROM %s WHERE recording_id=$1',collection) INTO qr USING q.recording_id;
  cutoff:=CASE WHEN before_only THEN public.morlet_segment_start_ns(collection,q.segment_id) END;
  IF before_only AND cutoff IS NULL THEN
    RAISE EXCEPTION 'before_only needs the segment''s recording in an acquisition with origin_utc';
  END IF;
  history:=public.morlet_history_sql(collection,false,cutoff);
  EXECUTE 'SELECT count(*) FROM ('||history||' LIMIT $9) h' INTO n
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,
      max_candidates::bigint+1;
  IF n>max_candidates THEN
    RAISE EXCEPTION 'history exceeds % segments; narrow match_keys or the metadata filter',max_candidates;
  END IF;
  RETURN QUERY EXECUTE format('WITH hist AS MATERIALIZED ('||history||'),
      med AS (SELECT o,percentile_cont(0.5) WITHIN GROUP (ORDER BY h.vals[o]) AS m FROM hist h, unnest($10) o GROUP BY o),
      lv AS (SELECT public.morlet_simple_value_floors(percentile_cont(0.5) WITHIN GROUP (ORDER BY h.vals[array_position(public.morlet_simple_value_names(),''rms'')])) AS fl FROM hist h),
      d AS (SELECT h.segment_id,sqrt(avg(((($9)[med.o]-h.vals[med.o])/abs(med.m))^2)) AS dist
        FROM hist h CROSS JOIN med CROSS JOIN lv WHERE abs(med.m)>lv.fl[med.o] GROUP BY h.segment_id)
    SELECT h.segment_id,h.recording_id,h.start_sample,h.end_sample,h.occurrence,d.dist,h.vals,
      coalesce((SELECT jsonb_agg(jsonb_build_object(''label_id'',l.label_id,''label'',l.label,''note'',l.note) ORDER BY l.label_id)
        FROM %s l WHERE l.recording_id=h.recording_id AND NOT l.retracted
          AND l.start_sample<h.end_sample AND h.start_sample<l.end_sample),''[]''::jsonb)
    FROM hist h JOIN d ON d.segment_id=h.segment_id
    ORDER BY d.dist,h.recording_id,h.start_sample LIMIT $11',rel.labels)
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,
      q.vals,ords,top_k;
END $$;

-- Level track (local-level Kalman filter, model local-level-v1) of one simple-v1 value
-- over the episodes of a segment's history: the segments morlet_compare_summary would
-- compare with (same context for match_keys, rate, channel, unit, metadata_filter and
-- exclude_labels; other occurrences), restricted to the query's machine and to segments
-- starting before the query and at or after since_utc, followed by the query segment.
-- Computed at query time from the stored values; nothing is persisted.
--   y = ln(value) with transform 'log' (a value <= 0 is reported as invalid_value and
--   skipped; there is no epsilon), or the value itself with transform 'none'.
--   Level x: x_k = x_{k-1} + w, Var(w) = process_variance_per_hour * hours since the
--   last valid episode; y_k = x_k + v, Var(v) = observation_variance (units of y squared).
--   The first valid episode sets x = y, Var(x) = observation_variance (the diffuse-prior limit).
-- Episodes are ordered by start time, then segment id; equal start times add no process
-- variance. level_var is the uncertainty of the level; innovation_var
-- (= predicted_level_var + observation_variance) is the model-based variance of the
-- observation predicted before it was seen. Gaussian, independent errors are assumed:
-- correlated or overlapping episodes make both variances too small.
CREATE FUNCTION public.morlet_level_track(collection regclass,segment_id bigint,
  process_variance_per_hour float8,observation_variance float8,value_name text DEFAULT 'std',
  transform text DEFAULT 'log',match_keys text[] DEFAULT '{}'::text[],metadata_filter jsonb DEFAULT '{}'::jsonb,
  exclude_labels text[] DEFAULT '{}'::text[],since_utc timestamptz DEFAULT '-infinity',max_candidates integer DEFAULT 10000)
RETURNS TABLE(episode integer,episode_segment_id bigint,occurrence jsonb,start_ns numeric,hours_since_previous float8,
  observed float8,y float8,predicted_level float8,predicted_level_var float8,innovation float8,innovation_var float8,
  standardized_innovation float8,level float8,level_var float8,status text,model text)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE acq record; q record; qr record; match jsonb; history text; n bigint; cutoff numeric; machine text;
  ord integer:=array_position(public.morlet_simple_value_names(),value_name); since numeric; r record;
  m float8; p float8; last_t numeric; k float8;
BEGIN
  IF ord IS NULL THEN
    RAISE EXCEPTION 'value_name must be one of %',public.morlet_simple_value_names();
  END IF;
  IF transform NOT IN ('log','none') THEN RAISE EXCEPTION 'transform must be log or none'; END IF;
  IF NOT (process_variance_per_hour>=0 AND process_variance_per_hour<'Infinity')
    OR NOT (observation_variance>0 AND observation_variance<'Infinity') THEN
    RAISE EXCEPTION 'process_variance_per_hour must be finite and >= 0, observation_variance finite and > 0';
  END IF;
  IF max_candidates<1 OR jsonb_typeof(metadata_filter)<>'object' THEN
    RAISE EXCEPTION 'invalid max_candidates or metadata filter';
  END IF;
  SELECT * INTO acq FROM public.morlet_acquisition_relations(collection);
  SELECT * INTO q FROM public.morlet_comparable_segment(collection,segment_id);
  match:=public.morlet_context_match(q.context,match_keys);
  EXECUTE format('SELECT sample_rate_hz,channel,unit FROM %s WHERE recording_id=$1',collection) INTO qr USING q.recording_id;
  cutoff:=public.morlet_segment_start_ns(collection,q.segment_id);
  IF cutoff IS NULL THEN
    RAISE EXCEPTION 'a level track needs the segment''s recording in an acquisition with origin_utc';
  END IF;
  EXECUTE format('SELECT a.machine FROM %s ch JOIN %s a ON a.acquisition_id=ch.acquisition_id WHERE ch.recording_id=$1',
    acq.channels,acq.acq) INTO machine USING q.recording_id;
  since:=CASE WHEN isfinite(since_utc) THEN extract(epoch FROM since_utc)*1000000000 END;
  IF since IS NOT NULL AND cutoff<since THEN RAISE EXCEPTION 'the segment starts before since_utc'; END IF;
  history:=format('SELECT h.segment_id,h.occurrence,h.vals[$9] AS x,
      extract(epoch FROM a.origin_utc)*1000000000+public.morlet_sample_time_ns(r.sample_rate_hz,ch.offset_ns,h.start_sample) AS t
    FROM (%s) h JOIN %s r ON r.recording_id=h.recording_id JOIN %s ch ON ch.recording_id=h.recording_id
      JOIN %s a ON a.acquisition_id=ch.acquisition_id WHERE a.machine=$10',
    public.morlet_history_sql(collection,false,cutoff),collection,acq.channels,acq.acq);
  history:='SELECT * FROM ('||history||') e WHERE $11::numeric IS NULL OR e.t>=$11';
  EXECUTE 'SELECT count(*) FROM ('||history||' LIMIT $12) c' INTO n
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,ord,machine,since,
      max_candidates::bigint;
  IF n+1>max_candidates THEN
    RAISE EXCEPTION 'track exceeds % episodes; narrow match_keys, the metadata filter or since_utc',max_candidates;
  END IF;
  episode:=0; model:='local-level-v1';
  FOR r IN EXECUTE history||' UNION ALL SELECT $1,$7,$12,$13 ORDER BY t,segment_id'
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels,ord,machine,since,
      q.vals[ord],cutoff
  LOOP
    episode:=episode+1; episode_segment_id:=r.segment_id; occurrence:=r.occurrence; start_ns:=r.t; observed:=r.x;
    y:=CASE WHEN transform='none' THEN r.x WHEN r.x>0 THEN ln(r.x) END;
    hours_since_previous:=CASE WHEN last_t IS NOT NULL THEN ((r.t-last_t)/3600000000000)::float8 END;
    predicted_level:=m;
    predicted_level_var:=p+process_variance_per_hour*hours_since_previous;
    innovation_var:=predicted_level_var+observation_variance;
    innovation:=y-predicted_level;
    standardized_innovation:=innovation/sqrt(innovation_var);
    IF y IS NULL THEN
      status:='invalid_value';
    ELSIF m IS NULL THEN
      m:=y; p:=observation_variance; last_t:=r.t; status:='initialized';
    ELSE
      k:=predicted_level_var/innovation_var;
      m:=m+k*innovation; p:=predicted_level_var*observation_variance/innovation_var; last_t:=r.t; status:='ok';
    END IF;
    level:=CASE WHEN status='invalid_value' THEN NULL ELSE m END;
    level_var:=CASE WHEN status='invalid_value' THEN NULL ELSE p END;
    RETURN NEXT;
  END LOOP;
END $$;

-- Integrity scrub (SQL 0.3.5, #193). Re-reads every chunk of up to max_recordings recordings after
-- after_recording (in recording_id order; call again with the last returned id to continue) and reports, per
-- recording, the chunks and samples it read and every problem found, without changing anything: a chunk that
-- does not decode (structure, CRC-32C) to its sample_count, a missing or wrong chunk summary (collections with
-- summaries), a gap or overlap between chunks, and chunks that do not end where the checkpoint says. At most 100
-- problems are listed per recording, then their number. Reads follow privileges and row security: run it as a
-- role that sees every row, or hidden chunks are reported as gaps.
CREATE FUNCTION public.morlet_verify(collection regclass,after_recording text DEFAULT NULL,
  max_recordings integer DEFAULT 100)
RETURNS TABLE(recording_id text,chunks bigint,samples bigint,problems text[])
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE relations record; r record; c record; expected bigint; found bigint; checks text; mem record; src text;
  gone boolean;
BEGIN
  IF max_recordings IS NULL OR max_recordings NOT BETWEEN 1 AND 100000 THEN
    RAISE EXCEPTION 'max_recordings must be 1..100000';
  END IF;
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  checks:=CASE WHEN relations.encoded THEN format('public.morlet_check_chunk(c.encoded,c.sample_count,%s,%L)',
    CASE WHEN relations.summarized THEN 'c.summary' ELSE 'NULL::bytea' END,relations.summarized)
    ELSE 'NULL::text' END;
  FOR r IN EXECUTE format('SELECT r.recording_id,(r.checkpoint->>''end'')::bigint AS stored_end FROM %s r
      WHERE $1 IS NULL OR r.recording_id>$1 ORDER BY r.recording_id LIMIT $2',collection)
      USING after_recording,max_recordings LOOP
    recording_id:=r.recording_id; chunks:=0; samples:=0; problems:='{}'; expected:=0; found:=0;
    -- With episodic memory, chunks compacted to levels 2 and 3 are their kept summaries.
    src:=format('SELECT c.start_sample AS s,%s AS n,%s AS bad FROM %s c WHERE c.recording_id=$1',
      relations.count_expr,checks,relations.chunks);
    IF mem.kept IS NOT NULL THEN
      src:=src||format(' UNION ALL SELECT k.start_sample,k.sample_count,NULL::text FROM %s k WHERE k.recording_id=$1',mem.kept);
    END IF;
    FOR c IN EXECUTE src||' ORDER BY 1' USING r.recording_id LOOP
      chunks:=chunks+1; samples:=samples+c.n;
      gone:=false;
      IF c.s>expected AND mem.episodes IS NOT NULL THEN   -- removed at level 4, as the ledger records
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s e WHERE e.recording_id=$1 AND e.level=4
          AND e.start_sample<=$2 AND e.end_sample>=$3)',mem.episodes) INTO gone USING r.recording_id,expected,c.s;
      END IF;
      IF c.s>expected AND NOT gone THEN
        found:=found+1;
        IF found<=100 THEN problems:=problems||format('gap: samples %s..%s have no chunk',expected,c.s); END IF;
      ELSIF c.s<expected THEN
        found:=found+1;
        IF found<=100 THEN problems:=problems||format('overlap: chunk at %s starts before %s',c.s,expected); END IF;
      END IF;
      IF c.bad IS NOT NULL THEN
        found:=found+1;
        IF found<=100 THEN problems:=problems||format('chunk at %s: %s',c.s,c.bad); END IF;
      END IF;
      expected:=greatest(expected,c.s+c.n);
    END LOOP;
    gone:=false;
    IF expected<r.stored_end AND mem.episodes IS NOT NULL THEN   -- the tail removed at level 4
      EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s e WHERE e.recording_id=$1 AND e.level=4
        AND e.start_sample<=$2 AND e.end_sample>=$3)',mem.episodes) INTO gone USING r.recording_id,expected,r.stored_end;
    END IF;
    IF expected IS DISTINCT FROM r.stored_end AND NOT gone THEN
      found:=found+1;
      IF found<=100 THEN
        problems:=problems||format('chunks end at %s, the checkpoint at %s',expected,coalesce(r.stored_end::text,'NULL'));
      END IF;
    END IF;
    IF found>100 THEN problems:=problems||format('%s more problems not listed',found-100); END IF;
    RETURN NEXT;
  END LOOP;
END $$;

-- ---- Episodic memory and graded compaction (SQL 0.3.5; docs/memory-design.md, #221) ----------------------
-- Episodes are sample intervals of recordings with context, importance and a level on the compaction ladder:
-- 0 full, 1 not searchable, 2 represented (cluster member), 3 summarized, 4 gone. morlet_set_level moves
-- episodes down and records every move in the ledger. Chunks, derived rows and kept summaries are removed only
-- when they lie entirely inside the episode and no overlapping episode at a lower level still needs them.

CREATE FUNCTION public.morlet_enable_memory(collection regclass)
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE schema_name text; collection_name text; relations record;
BEGIN
  SELECT * INTO relations FROM public.morlet_stream_relations(collection);
  IF NOT relations.encoded THEN RAISE EXCEPTION 'episodic memory needs a collection with encoded chunks'; END IF;
  SELECT n.nspname,c.relname INTO schema_name,collection_name
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection;
  IF to_regclass(format('%I.%I',schema_name,collection_name||'_episodes')) IS NOT NULL THEN RETURN; END IF;
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    episode_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recording_id text NOT NULL REFERENCES %1$I.%3$I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    end_sample bigint NOT NULL,
    level smallint NOT NULL DEFAULT 0 CHECK(level BETWEEN 0 AND 4),
    context jsonb NOT NULL DEFAULT ''{}'' CHECK(jsonb_typeof(context)=''object''),
    importance float8 NOT NULL DEFAULT 0 CHECK(importance BETWEEN 0 AND 1),
    exempt boolean NOT NULL DEFAULT false,
    cluster_id bigint, representative bigint, distance float8,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK(end_sample>start_sample), CHECK(NOT exempt OR level<=1),
    CHECK((level=2)=(representative IS NOT NULL)))',schema_name,collection_name||'_episodes',collection_name);
  EXECUTE format('CREATE INDEX ON %I.%I(recording_id,start_sample)',schema_name,collection_name||'_episodes');
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    policy text NOT NULL, policy_version text NOT NULL, params jsonb NOT NULL DEFAULT ''{}'',
    dry_run boolean NOT NULL, status text NOT NULL DEFAULT ''running'' CHECK(status IN (''running'',''finished'',''failed'')),
    summary jsonb, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz)',
    schema_name,collection_name||'_memory_runs');
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    ledger_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES %1$I.%3$I(run_id),
    episode_id bigint NOT NULL, recording_id text NOT NULL, start_sample bigint NOT NULL, end_sample bigint NOT NULL,
    from_level smallint NOT NULL, to_level smallint NOT NULL, reason jsonb NOT NULL DEFAULT ''{}'',
    chunks_removed bigint NOT NULL, summaries_removed bigint NOT NULL, derived_removed bigint NOT NULL,
    bytes_freed bigint NOT NULL, removed_md5 text, cold_copy text, at timestamptz NOT NULL DEFAULT now())',
    schema_name,collection_name||'_ledger',collection_name||'_memory_runs');
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    recording_id text NOT NULL REFERENCES %1$I.%3$I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL, sample_count integer NOT NULL CHECK(sample_count BETWEEN 1 AND 32768),
    summary bytea COMPRESSION pglz NOT NULL CHECK(octet_length(summary)=66), ledger_id bigint NOT NULL,
    PRIMARY KEY(recording_id,start_sample))',schema_name,collection_name||'_kept_summaries',collection_name);
END $$;

-- The memory tables of a collection, or NULLs when episodic memory is not enabled.
CREATE FUNCTION public.morlet_memory_relations(collection regclass)
RETURNS TABLE(episodes regclass,ledger regclass,runs regclass,kept regclass)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
  RETURN QUERY SELECT to_regclass(format('%I.%I',n.nspname,c.relname||'_episodes')),
      to_regclass(format('%I.%I',n.nspname,c.relname||'_ledger')),
      to_regclass(format('%I.%I',n.nspname,c.relname||'_memory_runs')),
      to_regclass(format('%I.%I',n.nspname,c.relname||'_kept_summaries'))
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection;
END $$;

-- Registers episodes: a JSON array of {recording_id, start_sample, end_sample, context?, importance?, exempt?}.
-- Each interval must lie within its recording's stored samples. Returns the new episode ids in input order.
CREATE FUNCTION public.morlet_add_episodes(collection regclass,rows jsonb)
RETURNS SETOF bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE mem record; r jsonb; rec_end bigint; id bigint;
BEGIN
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  IF mem.episodes IS NULL THEN RAISE EXCEPTION 'episodic memory is not enabled for this collection (morlet_enable_memory)'; END IF;
  IF jsonb_typeof(rows)<>'array' OR jsonb_array_length(rows) NOT BETWEEN 1 AND 100000 THEN
    RAISE EXCEPTION 'rows must be a JSON array of 1..100000 episodes';
  END IF;
  FOR r IN SELECT value FROM jsonb_array_elements(rows) LOOP
    EXECUTE format('SELECT (checkpoint->>''end'')::bigint FROM %s WHERE recording_id=$1',collection)
      INTO rec_end USING r->>'recording_id';
    IF rec_end IS NULL THEN RAISE EXCEPTION 'recording % does not exist or is not visible',r->>'recording_id'; END IF;
    IF (r->>'start_sample')::bigint<0 OR (r->>'end_sample')::bigint<=(r->>'start_sample')::bigint
      OR (r->>'end_sample')::bigint>rec_end THEN
      RAISE EXCEPTION 'episode interval %..% is outside recording % (0..%)',r->>'start_sample',r->>'end_sample',
        r->>'recording_id',rec_end;
    END IF;
    EXECUTE format('INSERT INTO %s(recording_id,start_sample,end_sample,context,importance,exempt)
      VALUES ($1,$2,$3,coalesce($4,''{}''),coalesce($5,0),coalesce($6,false)) RETURNING episode_id',mem.episodes)
      INTO id USING r->>'recording_id',(r->>'start_sample')::bigint,(r->>'end_sample')::bigint,r->'context',
        (r->>'importance')::float8,(r->>'exempt')::boolean;
    RETURN NEXT id;
  END LOOP;
END $$;

CREATE FUNCTION public.morlet_start_memory_run(collection regclass,policy text,policy_version text,
  params jsonb DEFAULT '{}'::jsonb,dry_run boolean DEFAULT false)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE mem record; id bigint;
BEGIN
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  IF mem.runs IS NULL THEN RAISE EXCEPTION 'episodic memory is not enabled for this collection (morlet_enable_memory)'; END IF;
  EXECUTE format('INSERT INTO %s(policy,policy_version,params,dry_run) VALUES ($1,$2,$3,$4) RETURNING run_id',mem.runs)
    INTO id USING policy,policy_version,params,dry_run;
  RETURN id;
END $$;

CREATE FUNCTION public.morlet_finish_memory_run(collection regclass,run_id bigint,status text,summary jsonb DEFAULT NULL)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE mem record; n bigint;
BEGIN
  IF status NOT IN ('finished','failed') THEN RAISE EXCEPTION 'status must be finished or failed'; END IF;
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  EXECUTE format('UPDATE %s SET status=$2,summary=$3,finished_at=now() WHERE run_id=$1 AND status=''running''',mem.runs)
    USING run_id,status,summary;
  GET DIAGNOSTICS n = ROW_COUNT;
  IF n<>1 THEN RAISE EXCEPTION 'run % is not running',run_id; END IF;
END $$;

-- Moves episodes down the ladder in one transaction. moves: a JSON array of {episode_id, level, representative?,
-- distance?, reason?}. Levels only go down; exempt episodes stay at 0 or 1; a level-2 move names a representative
-- episode at level 0 or 1. Removed per move, restricted to rows entirely inside the episode that no overlapping
-- episode at a lower level still needs: from level 1 the envelopes (and their probe rows) and feature segments;
-- from level 2 the encoded chunks, whose summaries are kept; at level 4 the kept summaries too. Every move writes
-- a ledger row with what it removed and the md5 of the removed chunks. With dry_run nothing changes and the result
-- says what would be removed (ledger_id NULL). The run must be running and match dry_run.
CREATE FUNCTION public.morlet_set_level(collection regclass,run_id bigint,moves jsonb,dry_run boolean DEFAULT false)
RETURNS TABLE(episode_id bigint,from_level smallint,to_level smallint,chunks_removed bigint,summaries_removed bigint,
  derived_removed bigint,bytes_freed bigint,ledger_id bigint)
LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE mem record; rel record; env regclass; m jsonb; ep record; run record; target smallint;
  rep_id bigint; rep_level smallint; rep_cluster bigint; part bigint;
  win integer; n_env bigint; n_seg bigint; n_chunks bigint; n_sum bigint; bytes bigint; md5sum text; lid bigint;
  chunk_filter text; sum_filter text; env_filter text; seg_filter text; summary_expr text; roll regclass; n_roll bigint;
BEGIN
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  roll:=public.morlet_rollup_relation(collection);
  IF mem.episodes IS NULL THEN RAISE EXCEPTION 'episodic memory is not enabled for this collection (morlet_enable_memory)'; END IF;
  SELECT * INTO rel FROM public.morlet_stream_relations(collection);
  env:=to_regclass(format('%s_envelopes',collection::text));
  EXECUTE format('SELECT dry_run,status FROM %s WHERE run_id=$1',mem.runs) INTO run USING run_id;
  IF run.status IS DISTINCT FROM 'running' THEN RAISE EXCEPTION 'run % is not running',run_id; END IF;
  IF run.dry_run<>morlet_set_level.dry_run THEN
    RAISE EXCEPTION 'run % was started with dry_run=%',run_id,run.dry_run;
  END IF;
  IF jsonb_typeof(moves)<>'array' OR jsonb_array_length(moves) NOT BETWEEN 1 AND 100000 THEN
    RAISE EXCEPTION 'moves must be a JSON array of 1..100000 moves';
  END IF;
  summary_expr:=CASE WHEN rel.summarized THEN 'coalesce(c.summary,public.morlet_encoded_chunk_summary(c.encoded))'
    ELSE 'public.morlet_encoded_chunk_summary(c.encoded)' END;
  FOR m IN SELECT value FROM jsonb_array_elements(moves) LOOP
    EXECUTE format('SELECT * FROM %s WHERE episode_id=$1 FOR UPDATE',mem.episodes) INTO ep USING (m->>'episode_id')::bigint;
    IF ep.episode_id IS NULL THEN RAISE EXCEPTION 'episode % does not exist or is not visible',m->>'episode_id'; END IF;
    target:=(m->>'level')::smallint;
    IF target IS NULL OR target NOT BETWEEN 1 AND 4 THEN RAISE EXCEPTION 'target level must be 1..4'; END IF;
    IF target<=ep.level THEN
      RAISE EXCEPTION 'episode % is at level %; levels only move down',ep.episode_id,ep.level
        USING HINT='Moving up means restoring from a cold copy or rebuilding derived data.';
    END IF;
    IF ep.exempt AND target>1 THEN RAISE EXCEPTION 'episode % is exempt (event library) and stays at level 0 or 1',ep.episode_id; END IF;
    rep_id:=NULL; rep_level:=NULL; rep_cluster:=NULL;
    IF target=2 THEN
      EXECUTE format('SELECT episode_id,level,cluster_id FROM %s WHERE episode_id=$1',mem.episodes)
        INTO rep_id,rep_level,rep_cluster USING (m->>'representative')::bigint;
      IF rep_id IS NULL OR rep_id=ep.episode_id OR rep_level>1 THEN
        RAISE EXCEPTION 'a level-2 move needs another episode at level 0 or 1 as representative';
      END IF;
    END IF;
    -- Rows entirely inside the episode, not needed by an overlapping episode at a lower level.
    chunk_filter:=format('c.recording_id=$1 AND c.start_sample>=$2 AND c.start_sample+c.sample_count<=$3
      AND NOT EXISTS (SELECT 1 FROM %s o WHERE o.recording_id=$1 AND o.episode_id<>$4 AND o.level<=%s
        AND o.start_sample<c.start_sample+c.sample_count AND o.end_sample>c.start_sample)',mem.episodes,'%s');
    n_env:=0; n_seg:=0; n_chunks:=0; n_sum:=0; bytes:=0; md5sum:=NULL;
    IF target>=1 AND ep.level<1 THEN
      IF env IS NOT NULL THEN
        -- A window leaves search when episodes out of search (this one included) cover it completely, possibly
        -- several adjacent ones, and no episode at level 0 overlaps it (the builder applies the same rule).
        env_filter:=format('e.recording_id=$1 AND e.start_sample<$3 AND e.start_sample+e.window_size>$2
          AND int8range(e.start_sample,e.start_sample+e.window_size) <@ (SELECT range_agg(int8range(o2.start_sample,o2.end_sample))
            FROM %1$s o2 WHERE o2.recording_id=$1 AND (o2.level>=1 OR o2.episode_id=$4)
              AND o2.start_sample<e.start_sample+e.window_size AND o2.end_sample>e.start_sample)
          AND NOT EXISTS (SELECT 1 FROM %1$s o WHERE o.recording_id=$1 AND o.episode_id<>$4 AND o.level=0
            AND o.start_sample<e.start_sample+e.window_size AND o.end_sample>e.start_sample)',mem.episodes);
        EXECUTE format('SELECT count(*),coalesce(sum(pg_column_size(e.*)),0) FROM %s e WHERE %s',env,env_filter)
          INTO n_env,part USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id;
        IF NOT dry_run THEN
          EXECUTE format('DELETE FROM %s e WHERE %s',env,env_filter)
            USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id;
        END IF;
        bytes:=bytes+part;
      END IF;
      EXECUTE format('SELECT (checkpoint->>''window'')::integer FROM %s WHERE recording_id=$1',collection)
        INTO win USING ep.recording_id;
      seg_filter:=format('s.recording_id=$1 AND s.start_sample<$3 AND s.start_sample+$5>$2
        AND int8range(s.start_sample,s.start_sample+$5) <@ (SELECT range_agg(int8range(o2.start_sample,o2.end_sample))
          FROM %1$s o2 WHERE o2.recording_id=$1 AND (o2.level>=1 OR o2.episode_id=$4)
            AND o2.start_sample<s.start_sample+$5 AND o2.end_sample>s.start_sample)
        AND NOT EXISTS (SELECT 1 FROM %1$s o WHERE o.recording_id=$1 AND o.episode_id<>$4 AND o.level=0
          AND o.start_sample<s.start_sample+$5 AND o.end_sample>s.start_sample)',mem.episodes);
      EXECUTE format('SELECT count(*) FROM %s s WHERE %s',rel.segments,seg_filter)
        INTO n_seg USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id,coalesce(win,512);
      IF NOT dry_run THEN
        EXECUTE format('DELETE FROM %s s WHERE %s',rel.segments,seg_filter)
          USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id,coalesce(win,512);
      END IF;
      n_env:=n_env+n_seg;
    END IF;
    IF target>=2 AND ep.level<2 THEN
      EXECUTE format('SELECT count(*),coalesce(sum(octet_length(c.encoded)),0),
          md5(string_agg(md5(c.encoded),'''' ORDER BY c.start_sample)) FROM %s c WHERE %s',
          rel.chunks,format(chunk_filter,1)) INTO n_chunks,part,md5sum
        USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id;
      bytes:=bytes+part;
    END IF;
    IF target=4 THEN
      EXECUTE format('SELECT count(*),coalesce(sum(octet_length(c.summary)),0) FROM %s c WHERE %s',
          mem.kept,format(chunk_filter,3)) INTO n_sum,part
        USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id;
      bytes:=bytes+part;
      -- Roll-ups that hold any chunk or kept summary removed here go with them (#219): they are derived rows.
      -- The builder's lock keeps it from writing a roll-up of these rows while they are removed.
      IF roll IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('morlet-rollup/'||collection::oid||'/'||ep.recording_id,0));
        EXECUTE format('SELECT count(*),coalesce(sum(octet_length(r.summary)),0) FROM %s r WHERE r.recording_id=$1
            AND EXISTS (SELECT 1 FROM (SELECT c.start_sample,c.sample_count FROM %s c WHERE %s
              UNION ALL SELECT c.start_sample,c.sample_count FROM %s c WHERE %s) x
              WHERE x.start_sample<r.end_sample AND x.start_sample+x.sample_count>r.start_sample)',
            roll,rel.chunks,format(chunk_filter,1),mem.kept,format(chunk_filter,3)) INTO n_roll,part
          USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id;
        n_env:=n_env+n_roll; bytes:=bytes+part;
        IF NOT dry_run AND n_roll>0 THEN
          EXECUTE format('DELETE FROM %s r WHERE r.recording_id=$1
              AND EXISTS (SELECT 1 FROM (SELECT c.start_sample,c.sample_count FROM %s c WHERE %s
                UNION ALL SELECT c.start_sample,c.sample_count FROM %s c WHERE %s) x
                WHERE x.start_sample<r.end_sample AND x.start_sample+x.sample_count>r.start_sample)',
              roll,rel.chunks,format(chunk_filter,1),mem.kept,format(chunk_filter,3))
            USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id;
        END IF;
      END IF;
    END IF;
    IF NOT dry_run THEN
      EXECUTE format('INSERT INTO %s(run_id,episode_id,recording_id,start_sample,end_sample,from_level,to_level,reason,
          chunks_removed,summaries_removed,derived_removed,bytes_freed,removed_md5)
        VALUES ($1,$2,$3,$4,$5,$6,$7,coalesce($8,''{}''),$9,$10,$11,$12,$13) RETURNING ledger_id',mem.ledger)
        INTO lid USING run_id,ep.episode_id,ep.recording_id,ep.start_sample,ep.end_sample,ep.level,target,m->'reason',
          n_chunks,n_sum,n_env,bytes,md5sum;
      IF target>=2 AND ep.level<2 AND n_chunks>0 THEN
        IF target<4 THEN
          EXECUTE format('INSERT INTO %s(recording_id,start_sample,sample_count,summary,ledger_id)
            SELECT c.recording_id,c.start_sample,c.sample_count,%s,$5 FROM %s c WHERE %s',
            mem.kept,summary_expr,rel.chunks,format(chunk_filter,1))
            USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id,lid;
        END IF;
        EXECUTE format('DELETE FROM %s c WHERE %s',rel.chunks,format(chunk_filter,1))
          USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id;
      END IF;
      IF target=4 THEN
        EXECUTE format('DELETE FROM %s c WHERE %s',mem.kept,format(chunk_filter,3))
          USING ep.recording_id,ep.start_sample,ep.end_sample,ep.episode_id;
      END IF;
      EXECUTE format('UPDATE %s SET level=$2,updated_at=now(),
          representative=CASE WHEN $2=2 THEN $3 END,
          cluster_id=CASE WHEN $2=2 THEN coalesce($4,$3) ELSE cluster_id END,
          distance=CASE WHEN $2=2 THEN $5 ELSE distance END WHERE episode_id=$1',mem.episodes)
        USING ep.episode_id,target,rep_id,rep_cluster,(m->>'distance')::float8;
    END IF;
    episode_id:=ep.episode_id; from_level:=ep.level; to_level:=target; chunks_removed:=n_chunks;
    summaries_removed:=n_sum; derived_removed:=n_env; bytes_freed:=bytes; ledger_id:=lid;
    RETURN NEXT;
  END LOOP;
END $$;

-- Per level: episodes and the samples they span.
CREATE FUNCTION public.morlet_memory_status(collection regclass)
RETURNS TABLE(level smallint,episodes bigint,samples numeric)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE mem record;
BEGIN
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  IF mem.episodes IS NULL THEN RAISE EXCEPTION 'episodic memory is not enabled for this collection (morlet_enable_memory)'; END IF;
  RETURN QUERY EXECUTE format('SELECT e.level,count(*),sum(e.end_sample-e.start_sample)::numeric FROM %s e
    GROUP BY e.level ORDER BY e.level',mem.episodes);
END $$;

-- What a search can find (SQL 0.3.6, #215). Splits each recording's samples 0..end into ranges, each in one state:
--   searchable      inside a stored envelope window (of window_size; of any size when it is NULL)
--   not_indexed     stored and at level 0, but no window covers it: the builder has not run there yet, or a tail
--                   shorter than a window
--   not_searchable  stored, but out of search at memory level 1 or above (episodes cover it, none at level 0)
--   represented     level 2: samples removed, the episode searchable only through its representative
--   summarized      level 3: chunk summaries only
--   gone            level 4: nothing kept in the database (the ledger names any cold copy)
--   missing         never stored (a gap between chunks)
-- Search returns only windows in "searchable" ranges, so a search without a match says nothing about the other
-- states: "not found" differs from "not searched". windows counts the stored windows inside a searchable range.
-- recording_ids NULL means every recording; recordings without samples have no rows. Row security and privileges
-- apply as for every reader.
CREATE FUNCTION public.morlet_coverage(collection regclass,recording_ids text[] DEFAULT NULL,
  window_size integer DEFAULT NULL)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,state text,windows bigint)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; mem record; env regclass; w_sql text; e_sql text; n_sql text;
BEGIN
  IF collection IS NULL THEN RAISE EXCEPTION 'collection must not be NULL'; END IF;
  IF window_size IS NOT NULL AND window_size<=0 THEN RAISE EXCEPTION 'window_size must be positive'; END IF;
  SELECT * INTO rel FROM public.morlet_stream_relations(collection);
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  env:=to_regclass(format('%s_envelopes',collection::text));
  IF env IS NULL THEN
    w_sql:='SELECT NULL::text AS recording_id,NULL::int8multirange AS m WHERE false';
    n_sql:='0::bigint';
  ELSE
    w_sql:=format('SELECT v.recording_id,range_agg(int8range(v.start_sample,v.start_sample+v.window_size)) AS m
      FROM %s v WHERE v.recording_id IN (SELECT r.recording_id FROM r) AND ($2::integer IS NULL OR v.window_size=$2)
      GROUP BY 1',env);
    n_sql:=format('(SELECT count(*) FROM %s v WHERE v.recording_id=s.recording_id AND ($2::integer IS NULL OR v.window_size=$2)
      AND v.start_sample>=lower(x) AND v.start_sample+v.window_size<=upper(x))',env);
  END IF;
  IF mem.episodes IS NULL THEN
    e_sql:='SELECT NULL::text AS recording_id,NULL::int8multirange AS u0,NULL::int8multirange AS u1,
      NULL::int8multirange AS u2,NULL::int8multirange AS u3,NULL::int8multirange AS u4 WHERE false';
  ELSE
    e_sql:=format('SELECT o.recording_id,
        range_agg(int8range(o.start_sample,o.end_sample)) FILTER (WHERE o.level=0) AS u0,
        range_agg(int8range(o.start_sample,o.end_sample)) FILTER (WHERE o.level=1) AS u1,
        range_agg(int8range(o.start_sample,o.end_sample)) FILTER (WHERE o.level=2) AS u2,
        range_agg(int8range(o.start_sample,o.end_sample)) FILTER (WHERE o.level=3) AS u3,
        range_agg(int8range(o.start_sample,o.end_sample)) FILTER (WHERE o.level=4) AS u4
      FROM %s o WHERE o.recording_id IN (SELECT r.recording_id FROM r) GROUP BY 1',mem.episodes);
  END IF;
  -- A sample's level is the lowest level of the episodes that contain it (0 without an episode): rows are removed
  -- only where no overlapping episode at a lower level still needs them.
  RETURN QUERY EXECUTE format('WITH r AS (SELECT t.recording_id,int8multirange(int8range(0,(t.checkpoint->>''end'')::bigint)) AS f
      FROM %1$s t WHERE ($1::text[] IS NULL OR t.recording_id=ANY($1)) AND coalesce((t.checkpoint->>''end'')::bigint,0)>0),
    st AS (SELECT c.recording_id,range_agg(int8range(c.start_sample,c.start_sample+%3$s)) AS m FROM %2$s c
      WHERE c.recording_id IN (SELECT r.recording_id FROM r) GROUP BY 1),
    w AS (%4$s), e AS (%5$s),
    q AS (SELECT r.recording_id,r.f,coalesce(st.m,''{}'')*r.f AS stored,coalesce(w.m,''{}'')*r.f AS win,
        coalesce(e.u0,''{}'') AS u0,coalesce(e.u1,''{}'') AS u1,coalesce(e.u2,''{}'') AS u2,
        coalesce(e.u3,''{}'') AS u3,coalesce(e.u4,''{}'') AS u4
      FROM r LEFT JOIN st ON st.recording_id=r.recording_id LEFT JOIN w ON w.recording_id=r.recording_id
        LEFT JOIN e ON e.recording_id=r.recording_id),
    p AS (SELECT q.recording_id,q.win,q.f-q.win AS rest,q.stored,
        q.f-((q.u1+q.u2+q.u3+q.u4)-q.u0) AS l0,q.u1-q.u0 AS l1,q.u2-(q.u0+q.u1) AS l2,
        q.u3-(q.u0+q.u1+q.u2) AS l3,q.u4-(q.u0+q.u1+q.u2+q.u3) AS l4 FROM q),
    s AS (SELECT p.recording_id,''searchable''::text AS state,p.win AS m FROM p
      UNION ALL SELECT p.recording_id,''not_indexed'',p.rest*p.stored*p.l0 FROM p
      UNION ALL SELECT p.recording_id,''not_searchable'',p.rest*p.stored*(p.l1+p.l2+p.l3+p.l4) FROM p
      UNION ALL SELECT p.recording_id,''represented'',p.rest*p.l2-p.stored FROM p
      UNION ALL SELECT p.recording_id,''summarized'',p.rest*p.l3-p.stored FROM p
      UNION ALL SELECT p.recording_id,''gone'',p.rest*p.l4-p.stored FROM p
      UNION ALL SELECT p.recording_id,''missing'',p.rest*(p.l0+p.l1)-p.stored FROM p)
    SELECT s.recording_id,lower(x),upper(x),s.state,CASE WHEN s.state=''searchable'' THEN %6$s END
    FROM s CROSS JOIN LATERAL unnest(s.m) x ORDER BY 1,2',collection,rel.chunks,rel.count_expr,w_sql,e_sql,n_sql)
    USING recording_ids,window_size;
END $$;

-- Raises the error for an interval whose samples are not all stored: names the level and ledger entry when the
-- missing samples were compacted, otherwise the general message.
CREATE FUNCTION public.morlet_raise_incomplete(collection regclass,recording_id text,interval_start bigint,interval_end bigint)
RETURNS void LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE mem record; e record;
BEGIN
  SELECT * INTO mem FROM public.morlet_memory_relations(collection);
  IF mem.ledger IS NOT NULL AND has_table_privilege(mem.ledger,'SELECT') THEN
    EXECUTE format('SELECT l.episode_id,l.to_level,l.ledger_id,l.start_sample,l.end_sample FROM %s l
      WHERE l.recording_id=$1 AND l.to_level>=2 AND l.chunks_removed+l.summaries_removed>0
        AND l.start_sample<$3 AND l.end_sample>$2 ORDER BY l.ledger_id DESC LIMIT 1',mem.ledger)
      INTO e USING recording_id,interval_start,interval_end;
    IF e.episode_id IS NOT NULL THEN
      RAISE EXCEPTION 'samples of recording % in %..% were compacted to level % (episode %, ledger %)',
        recording_id,e.start_sample,e.end_sample,e.to_level,e.episode_id,e.ledger_id
        USING HINT='Level 2 and 3 keep exact statistics on chunk boundaries; the ledger records what was removed.';
    END IF;
  END IF;
  RAISE EXCEPTION 'source interval is incomplete or not fully visible';
END $$;
