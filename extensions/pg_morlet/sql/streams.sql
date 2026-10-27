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
    checkpoint jsonb NOT NULL,
    sample_quantum float8 NOT NULL DEFAULT 1 CHECK(sample_quantum>0 AND sample_quantum<''Infinity''))',schema_name,collection_name);
  EXECUTE format('CREATE TABLE %I.%I (
    recording_id text NOT NULL REFERENCES %I.%I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    sample_count integer NOT NULL CHECK(sample_count BETWEEN 1 AND 32768),
    encoded bytea NOT NULL CHECK(public.morlet_valid_encoded_chunk(encoded,sample_count)),
    PRIMARY KEY(recording_id,start_sample))',schema_name,collection_name||'_chunks',schema_name,collection_name);
  EXECUTE format('CREATE TABLE %I.%I (
    recording_id text NOT NULL REFERENCES %I.%I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),features float8[] NOT NULL,
    PRIMARY KEY(recording_id,start_sample))',schema_name,collection_name||'_segments',schema_name,collection_name);
  RETURN format('%I.%I',schema_name,collection_name)::regclass;
END $$;

-- Collections created before encoded storage keep float8[] chunks; the
-- expressions let every reader handle both layouts with one query text.
CREATE FUNCTION public.morlet_stream_relations(collection regclass)
RETURNS TABLE(chunks regclass,segments regclass,encoded boolean,samples_expr text,count_expr text)
LANGUAGE sql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT r.chunks,r.segments,e.encoded,
    CASE WHEN e.encoded THEN 'public.morlet_decode_chunk(c.encoded)' ELSE 'c.samples' END,
    CASE WHEN e.encoded THEN 'c.sample_count' ELSE 'cardinality(c.samples)' END
  FROM (SELECT format('%I.%I',n.nspname,c.relname||'_chunks')::regclass AS chunks,
      format('%I.%I',n.nspname,c.relname||'_segments')::regclass AS segments
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=collection) r
  CROSS JOIN LATERAL (SELECT EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=r.chunks
    AND a.attname='encoded' AND NOT a.attisdropped) AS encoded) e
$$;

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
    IF previous IS DISTINCT FROM samples THEN RAISE EXCEPTION 'conflicting replay at chunk offset'; END IF;
    RETURN (state->>'end')::bigint;
  END IF;
  result:=public.morlet_chunk_step(state,start_sample,samples);
  IF relations.encoded THEN
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
  EXECUTE format('SELECT array_agg(v.value ORDER BY c.start_sample,v.ordinality),count(*)
    FROM %1$s c CROSS JOIN LATERAL unnest((%2$s)[
      greatest(1,$2-c.start_sample+1)::integer:
      least((%3$s)::bigint,$3-c.start_sample)::integer
    ]) WITH ORDINALITY AS v(value,ordinality)
    WHERE c.recording_id=$1 AND c.start_sample<$3 AND c.start_sample>$2-32768
      AND c.start_sample+(%3$s)>$2',relations.chunks,relations.samples_expr,relations.count_expr)
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

-- Materialized envelopes (xc-envelope-f16-v1): an opt-in, rebuildable
-- representation stored next to raw chunks; not an index. Rows hold binary16
-- envelopes of fixed windows; the payload's first byte is its encoding version.
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
    envelope bytea NOT NULL CHECK(octet_length(envelope)=1+2*((window_size+9)/10) AND get_byte(envelope,0)=1),
    PRIMARY KEY(recording_id,window_size,start_sample))',schema_name,collection_name||'_envelopes',schema_name,collection_name);
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

-- Builds envelopes for complete windows on the grid start_sample = k*stride,
-- resuming after the last stored window of this size. At most max_windows per
-- call, so long recordings can be built in bounded transactions. Returns the
-- number of rows written; 0 means the recording is up to date.
CREATE FUNCTION public.morlet_build_envelopes(collection regclass,recording_id text,
  window_size integer DEFAULT 4096,stride integer DEFAULT 2048,max_windows integer DEFAULT 10000)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel regclass; recording_end bigint; s bigint; last bigint; written bigint:=0;
BEGIN
  IF window_size NOT IN (512,1024,2048,4096,8192,16384,32768) OR stride NOT BETWEEN 1 AND 32768
    OR max_windows NOT BETWEEN 1 AND 1000000 THEN
    RAISE EXCEPTION 'window_size must be a power of two in 512..32768, stride 1..32768, max_windows 1..1000000';
  END IF;
  rel:=public.morlet_envelope_relation(collection);
  EXECUTE format('SELECT (checkpoint->>''end'')::bigint FROM %s WHERE recording_id=$1',collection)
    INTO recording_end USING recording_id;
  IF recording_end IS NULL THEN RAISE EXCEPTION 'recording does not exist or is not visible'; END IF;
  EXECUTE format('SELECT max(start_sample) FROM %s WHERE recording_id=$1 AND window_size=$2',rel)
    INTO last USING recording_id,window_size;
  s:=CASE WHEN last IS NULL THEN 0 ELSE (last/stride+1)*stride END;
  WHILE s+window_size<=recording_end AND written<max_windows LOOP
    EXECUTE format('INSERT INTO %s(recording_id,window_size,start_sample,envelope)
      SELECT $1,$2,$3,public.morlet_envelope_encode(i.samples)
      FROM public.morlet_read_interval($4,$1,$3,$3+$2) i ON CONFLICT DO NOTHING',rel)
      USING recording_id,window_size,s,collection;
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
CREATE FUNCTION public.morlet_envelope_ranked(collection regclass,query_samples float8[],query_rate_hz float8,
  query_channel text,query_unit text,metadata_filter jsonb,top_k integer,max_lag_fraction float8,
  suppress_overlaps boolean,max_candidates integer,
  exclude_recording_id text,exclude_start bigint,exclude_end bigint)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel regclass; width integer; query_envelope float8[]; lag integer; candidates bigint; hit record;
  picked_ids text[]:=ARRAY[]::text[]; picked_starts bigint[]:=ARRAY[]::bigint[];
  picked_ends bigint[]:=ARRAY[]::bigint[]; picked integer:=0; idx integer; blocked boolean;
BEGIN
  IF top_k IS NULL OR top_k NOT BETWEEN 1 AND 100 OR metadata_filter IS NULL OR jsonb_typeof(metadata_filter)<>'object'
    OR max_lag_fraction IS NULL OR NOT (max_lag_fraction BETWEEN 0 AND 0.5) OR max_candidates IS NULL OR max_candidates<1
    OR query_rate_hz IS NULL OR suppress_overlaps IS NULL THEN
    RAISE EXCEPTION 'invalid top_k, metadata filter, max_lag_fraction (0..0.5) or max_candidates';
  END IF;
  rel:=public.morlet_envelope_relation(collection);
  width:=cardinality(query_samples);
  query_envelope:=public.morlet_envelope(query_samples);
  lag:=floor(max_lag_fraction*cardinality(query_envelope));
  EXECUTE format('SELECT count(*) FROM %s e JOIN %s r ON r.recording_id=e.recording_id
    WHERE e.window_size=$1 AND r.sample_rate_hz=$2 AND r.channel=$3 AND r.unit=$4 AND r.metadata @> $5',rel,collection)
    INTO candidates USING width,query_rate_hz,query_channel,query_unit,metadata_filter;
  IF candidates>max_candidates THEN
    RAISE EXCEPTION 'envelope search exceeds % candidates; narrow the metadata filter',max_candidates;
  END IF;
  FOR hit IN EXECUTE format('SELECT e.recording_id,e.start_sample,e.start_sample+e.window_size AS end_sample,
      public.morlet_envelope_distance($6,e.envelope,$7) AS distance,r.metadata
    FROM %s e JOIN %s r ON r.recording_id=e.recording_id
    WHERE e.window_size=$1 AND r.sample_rate_hz=$2 AND r.channel=$3 AND r.unit=$4 AND r.metadata @> $5
      AND ($8::text IS NULL OR e.recording_id<>$8 OR e.start_sample+e.window_size<=$9 OR e.start_sample>=$10)
    ORDER BY distance,e.recording_id,e.start_sample',rel,collection)
    USING width,query_rate_hz,query_channel,query_unit,metadata_filter,query_envelope,lag,
      exclude_recording_id,exclude_start,exclude_end
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

CREATE FUNCTION public.morlet_search_envelopes(collection regclass,query_samples float8[],query_rate_hz float8,
  query_channel text,query_unit text,metadata_filter jsonb DEFAULT '{}'::jsonb,top_k integer DEFAULT 10,
  max_lag_fraction float8 DEFAULT 0.075,suppress_overlaps boolean DEFAULT true,max_candidates integer DEFAULT 4096)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE sql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
  SELECT * FROM public.morlet_envelope_ranked(collection,query_samples,query_rate_hz,query_channel,query_unit,
    metadata_filter,top_k,max_lag_fraction,suppress_overlaps,max_candidates,NULL,NULL,NULL)
$$;

-- Query by a stored interval. With exclude_source, windows overlapping the
-- source interval in its own recording are not returned.
CREATE FUNCTION public.morlet_search_envelope_interval(collection regclass,source_recording_id text,
  interval_start bigint,interval_end bigint,metadata_filter jsonb DEFAULT '{}'::jsonb,top_k integer DEFAULT 10,
  max_lag_fraction float8 DEFAULT 0.075,exclude_source boolean DEFAULT true,suppress_overlaps boolean DEFAULT true,
  max_candidates integer DEFAULT 4096)
RETURNS TABLE(recording_id text,start_sample bigint,end_sample bigint,distance float8,metadata jsonb)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE source record;
BEGIN
  SELECT * INTO source FROM public.morlet_read_interval(collection,source_recording_id,interval_start,interval_end);
  RETURN QUERY SELECT * FROM public.morlet_envelope_ranked(collection,source.samples,source.sample_rate_hz,
    source.channel,source.unit,metadata_filter,top_k,max_lag_fraction,suppress_overlaps,max_candidates,
    CASE WHEN exclude_source THEN source_recording_id END,interval_start,interval_end);
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
    representation text NOT NULL, vals float8[] NOT NULL,
    PRIMARY KEY(segment_id,representation))',s,c||'_ctx_values',c||'_ctx_segments');
  EXECUTE format('CREATE TABLE %1$I.%2$I (
    label_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recording_id text NOT NULL REFERENCES %1$I.%3$I(recording_id) ON DELETE CASCADE,
    start_sample bigint NOT NULL CHECK(start_sample>=0),
    end_sample bigint NOT NULL CHECK(end_sample>start_sample),
    label text NOT NULL CHECK(label<>''''), note text NOT NULL DEFAULT '''',
    author text NOT NULL DEFAULT current_user, created_at timestamptz NOT NULL DEFAULT now(),
    retracted boolean NOT NULL DEFAULT false)',s,c||'_labels',c);
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
  RETURN n;
END $$;

-- Appends a revision of an active row (changes may set value, start_sample,
-- end_sample) or a retraction. Segments built from the old row become stale.
CREATE FUNCTION public.morlet_revise_context(collection regclass,ctx_id bigint,changes jsonb DEFAULT '{}'::jsonb,
  retract boolean DEFAULT false)
RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE rel record; old record; new_id bigint;
BEGIN
  SELECT * INTO rel FROM public.morlet_context_relations(collection);
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
  RETURN new_id;
END $$;

-- One segment per maximal intersection of the active intervals of all `keys`,
-- within committed samples. occurrence holds the values of `occurrence_keys`
-- intervals that cover the whole segment (for example the part). Stale segments
-- of this recording and spec are replaced. Returns the number of new segments.
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
        JOIN %3$s r ON r.recording_id=s.recording_id
      WHERE s.segment_id<>$1 AND NOT s.stale%6$s AND r.sample_rate_hz=$3 AND r.channel=$4 AND r.unit=$5
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
-- shifts into large calibrated deviations. NULL where no context qualifies. Population
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
  EXECUTE 'SELECT count(*) FROM ('||pop||') h' INTO n
    USING q.segment_id,q.context,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels;
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
    SELECT array_agg(greatest((SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY r.rel) FROM r WHERE r.ord=k.ord),$9)
      ORDER BY k.ord) FROM k'
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
  EXECUTE 'SELECT count(*) FROM ('||history||') h' INTO candidates
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels;
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
  EXECUTE 'SELECT coalesce(array_agg(x ORDER BY i,j),''{}''::float8[]) FROM (SELECT row_number() OVER () AS i,h.vals FROM ('
    ||history||') h) t, LATERAL unnest(t.vals) WITH ORDINALITY u(x,j)' INTO flat
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels;
  n:=cardinality(flat)/width;
  IF n>max_candidates THEN
    RAISE EXCEPTION 'history exceeds % segments; narrow match_keys or the metadata filter',max_candidates;
  END IF;
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
DECLARE rel record; existing record;
BEGIN
  IF collection IS NULL OR acquisition_id IS NULL OR recording_id IS NULL OR role IS NULL OR offset_ns IS NULL
    OR clock_source IS NULL THEN
    RAISE EXCEPTION 'only offset_uncertainty_ns may be NULL';
  END IF;
  SELECT * INTO rel FROM public.morlet_acquisition_relations(collection);
  EXECUTE format('SELECT * FROM %s WHERE recording_id=$1',rel.channels) INTO existing USING recording_id;
  IF existing.recording_id IS NOT NULL THEN
    IF existing.acquisition_id=acquisition_id AND existing.role=role AND existing.offset_ns=offset_ns
      AND existing.clock_source=clock_source AND existing.offset_uncertainty_ns IS NOT DISTINCT FROM offset_uncertainty_ns THEN
      RETURN false;
    END IF;
    RAISE EXCEPTION 'recording % already belongs to an acquisition with different attributes',recording_id;
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
  EXECUTE 'SELECT count(*) FROM ('||history||') h' INTO n
    USING q.segment_id,match,qr.sample_rate_hz,qr.channel,qr.unit,metadata_filter,q.occurrence,exclude_labels;
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
