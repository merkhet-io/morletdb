-- Collections belong to the caller, outside extension membership: pg_dump includes
-- their schema and data normally. All functions execute with caller privileges.
CREATE FUNCTION public.morlet_create_collection(schema_name text, collection_name text)
RETURNS regclass LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER
SET search_path = pg_catalog AS $$
DECLARE collection regclass;
BEGIN
  EXECUTE format('CREATE TABLE %I.%I (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sample_rate_hz float8 NOT NULL,
    channel text NOT NULL,
    unit text NOT NULL,
    metadata jsonb NOT NULL DEFAULT ''{}''::jsonb CHECK (jsonb_typeof(metadata) = ''object''),
    samples float8[] NOT NULL CHECK (array_ndims(samples)=1 AND array_lower(samples,1)=1 AND public.morlet_valid_recording(samples, sample_rate_hz))
  )', schema_name, collection_name);
  collection := format('%I.%I', schema_name, collection_name)::regclass;
  RETURN collection;
END $$;

CREATE FUNCTION public.morlet_search(
  collection regclass, query_samples float8[], query_rate_hz float8,
  query_channel text, query_unit text, metadata_filter jsonb DEFAULT '{}'::jsonb,
  normalization text DEFAULT 'zscore', stride integer DEFAULT 256,
  top_k integer DEFAULT 10)
RETURNS TABLE(recording_id bigint, start_sample integer, end_sample integer, distance float8)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE window_size integer; candidate_count bigint;
BEGIN
  IF array_ndims(query_samples) IS DISTINCT FROM 1 OR array_lower(query_samples,1) IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'query must be a one-dimensional array starting at 1';
  END IF;
  window_size := cardinality(query_samples);
  IF window_size < 512 OR window_size > 4096 THEN
    RAISE EXCEPTION 'query must contain 512..4096 samples';
  END IF;
  IF stride < 1 OR top_k < 1 OR top_k > 100 THEN
    RAISE EXCEPTION 'stride must be positive and top_k must be 1..100';
  END IF;
  IF jsonb_typeof(metadata_filter) <> 'object' THEN
    RAISE EXCEPTION 'metadata_filter must be an object';
  END IF;
  PERFORM public.morlet_valid_recording(query_samples, query_rate_hz);
  PERFORM public.morlet_normalize(query_samples, normalization);
  EXECUTE format('SELECT coalesce(sum(1 + (cardinality(samples)-$1)/$2),0)::bigint
    FROM %s WHERE sample_rate_hz=$3 AND channel=$4 AND unit=$5
    AND metadata @> $6 AND cardinality(samples)>=$1', collection)
    INTO candidate_count USING window_size, stride, query_rate_hz, query_channel, query_unit, metadata_filter;
  IF candidate_count > 4096 THEN
    RAISE EXCEPTION 'search exceeds 4096 candidate windows; narrow the metadata filter or increase stride';
  END IF;
  RETURN QUERY EXECUTE format('SELECT r.id, w.start_index, w.start_index+$1,
    public.morlet_distance(r.samples[w.start_index+1:w.start_index+$1],$2,$3) AS distance
    FROM %s r CROSS JOIN LATERAL generate_series(0,cardinality(r.samples)-$1,$4) AS w(start_index)
    WHERE r.sample_rate_hz=$5 AND r.channel=$6 AND r.unit=$7 AND r.metadata @> $8
    ORDER BY distance, r.id, w.start_index LIMIT $9', collection)
    USING window_size, query_samples, normalization, stride, query_rate_hz,
      query_channel, query_unit, metadata_filter, top_k;
END $$;

-- Adds persisted features to an existing collection. ALTER TABLE evaluates all
-- existing recordings atomically; failed extraction rolls the whole change back.
CREATE FUNCTION public.morlet_enable_segments(collection regclass,
  window_size integer DEFAULT 512, stride integer DEFAULT 256,
  normalization text DEFAULT 'zscore')
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER
SET search_path = pg_catalog AS $$
BEGIN
  PERFORM public.morlet_segment_grid(array_fill(0.::float8,ARRAY[512]),4096,window_size,stride,normalization);
  EXECUTE format('ALTER TABLE %s ADD COLUMN morlet_grid jsonb GENERATED ALWAYS AS
    (public.morlet_segment_grid(samples,sample_rate_hz,%s,%s,%L)) STORED NOT NULL',
    collection,window_size,stride,normalization);
END $$;

-- Typed binary feature vectors, preserving caller-owned generated-column semantics.
CREATE TYPE public.morlet_segment AS (start_sample integer, features float8[]);

CREATE FUNCTION public.morlet_segment_array(samples float8[], sample_rate_hz float8,
  window_size integer, stride integer, normalization text)
RETURNS public.morlet_segment[] LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog AS $$
  SELECT ARRAY(SELECT ROW((s->>'start_sample')::integer,
    ARRAY(SELECT jsonb_array_elements_text(s->'features')::float8))::public.morlet_segment
    FROM jsonb_array_elements(public.morlet_segment_grid(samples,sample_rate_hz,window_size,stride,normalization)->'segments') s
    ORDER BY (s->>'start_sample')::integer)
$$;

CREATE FUNCTION public.morlet_typed_spec(window_size integer,stride integer,normalization text)
RETURNS jsonb LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SET search_path = pg_catalog AS $$
  SELECT jsonb_build_object('version',public.morlet_extractor_version(),
    'window_size',window_size,'stride',stride,'normalization',normalization)
$$;

CREATE FUNCTION public.morlet_enable_typed_segments(collection regclass,
  window_size integer DEFAULT 512,stride integer DEFAULT 256,normalization text DEFAULT 'zscore')
RETURNS void LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER SET search_path = pg_catalog AS $$
BEGIN
  PERFORM public.morlet_segment_grid(array_fill(0.::float8,ARRAY[512]),4096,window_size,stride,normalization);
  EXECUTE format('ALTER TABLE %s ADD COLUMN morlet_segments public.morlet_segment[] GENERATED ALWAYS AS
    (public.morlet_segment_array(samples,sample_rate_hz,%s,%s,%L)) STORED NOT NULL,
    ADD COLUMN morlet_segment_spec jsonb GENERATED ALWAYS AS
    (public.morlet_typed_spec(%s,%s,%L)) STORED NOT NULL',
    collection,window_size,stride,normalization,window_size,stride,normalization);
END $$;

CREATE FUNCTION public.morlet_search_features(
  collection regclass, query_samples float8[], query_rate_hz float8,
  query_channel text, query_unit text, metadata_filter jsonb DEFAULT '{}'::jsonb,
  normalization text DEFAULT 'zscore', top_k integer DEFAULT 10,
  source_recording_id bigint DEFAULT NULL, source_start_sample integer DEFAULT NULL,
  source_end_sample integer DEFAULT NULL, suppress_overlaps boolean DEFAULT true)
RETURNS TABLE(recording_id bigint, start_sample integer, end_sample integer,
  distance float8, extractor_version text)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE window_size integer; query_features float8[]; candidate_count bigint; incompatible boolean;
  segment_count text; representation text; segment_start text; segment_features text; segment_source text;
  candidate record; selected_ids bigint[] := '{}'; selected_starts integer[] := '{}';
  selected_ends integer[] := '{}'; selected_count integer := 0; hit_overlap boolean; i integer;
BEGIN
  -- Optional source fields require a non-STRICT wrapper; preserve NULL behavior
  -- for required arguments explicitly.
  IF collection IS NULL OR query_samples IS NULL OR query_rate_hz IS NULL
    OR query_channel IS NULL OR query_unit IS NULL OR metadata_filter IS NULL
    OR normalization IS NULL OR top_k IS NULL OR suppress_overlaps IS NULL THEN
    RETURN;
  END IF;
  IF NOT ((source_recording_id IS NULL AND source_start_sample IS NULL AND source_end_sample IS NULL)
    OR (source_recording_id IS NOT NULL AND source_start_sample IS NOT NULL
      AND source_end_sample IS NOT NULL AND source_start_sample >= 0 AND source_end_sample > source_start_sample)) THEN
    RAISE EXCEPTION 'source exclusion requires recording ID and a valid half-open sample interval';
  END IF;
  IF array_ndims(query_samples) IS DISTINCT FROM 1 OR array_lower(query_samples,1) IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'query must be a one-dimensional array starting at 1';
  END IF;
  window_size := cardinality(query_samples);
  IF window_size < 512 OR window_size > 4096 OR top_k < 1 OR top_k > 100 THEN
    RAISE EXCEPTION 'query must contain 512..4096 samples and top_k must be 1..100';
  END IF;
  IF jsonb_typeof(metadata_filter) <> 'object' THEN
    RAISE EXCEPTION 'metadata_filter must be an object';
  END IF;
  query_features := public.morlet_features(public.morlet_normalize(query_samples,normalization),query_rate_hz);
  IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=collection AND attname='morlet_segments' AND NOT attisdropped) THEN
    segment_count := 'cardinality(morlet_segments)'; representation := 'morlet_segment_spec';
    segment_start := 's.start_sample'; segment_features := 's.features';
    segment_source := 'unnest(r.morlet_segments) s';
  ELSE
    segment_count := 'jsonb_array_length(morlet_grid->''segments'')'; representation := 'morlet_grid';
    segment_start := '(s->>''start_sample'')::integer';
    segment_features := 'ARRAY(SELECT jsonb_array_elements_text(s->''features'')::float8)';
    segment_source := 'jsonb_array_elements(r.morlet_grid->''segments'') s';
  END IF;
  EXECUTE format('SELECT coalesce(sum(%1$s),0)::bigint,
    coalesce(bool_or((%2$I->>''version'') IS DISTINCT FROM public.morlet_extractor_version()
      OR (%2$I->>''window_size'')::integer IS DISTINCT FROM $1
      OR (%2$I->>''normalization'') IS DISTINCT FROM $2),false)
    FROM %3$s WHERE sample_rate_hz=$3 AND channel=$4 AND unit=$5 AND metadata @> $6',
    segment_count,representation,collection)
    INTO candidate_count,incompatible USING window_size,normalization,query_rate_hz,query_channel,query_unit,metadata_filter;
  IF incompatible THEN
    RAISE EXCEPTION 'incompatible segment representation: match window and normalization, or rebuild for the current extractor';
  END IF;
  IF candidate_count > 4096 THEN
    RAISE EXCEPTION 'feature search exceeds 4096 candidate segments; narrow the metadata filter';
  END IF;
  FOR candidate IN EXECUTE format('SELECT r.id AS recording_id,%1$s AS start_sample,
      %1$s+$1 AS end_sample,
      public.morlet_feature_vector_distance(%2$s,$2) AS distance,
      r.%3$I->>''version'' AS extractor_version
    FROM %4$s r CROSS JOIN LATERAL %5$s
    WHERE r.sample_rate_hz=$3 AND r.channel=$4 AND r.unit=$5 AND r.metadata @> $6
    AND NOT (r.id IS NOT DISTINCT FROM $7 AND %1$s < $9 AND %1$s+$1 > $8)
    ORDER BY distance,r.id,%1$s',segment_start,segment_features,representation,collection,segment_source)
    USING window_size,query_features,query_rate_hz,query_channel,query_unit,metadata_filter,source_recording_id,source_start_sample,source_end_sample
  LOOP
    hit_overlap := false;
    IF suppress_overlaps AND selected_count > 0 THEN
      FOR i IN 1..selected_count LOOP
        IF selected_ids[i] = candidate.recording_id
          AND candidate.start_sample < selected_ends[i] AND candidate.end_sample > selected_starts[i] THEN
          hit_overlap := true;
          EXIT;
        END IF;
      END LOOP;
    END IF;
    IF hit_overlap THEN CONTINUE; END IF;
    recording_id := candidate.recording_id; start_sample := candidate.start_sample;
    end_sample := candidate.end_sample; distance := candidate.distance;
    extractor_version := candidate.extractor_version;
    RETURN NEXT;
    selected_ids := array_append(selected_ids,recording_id);
    selected_starts := array_append(selected_starts,start_sample);
    selected_ends := array_append(selected_ends,end_sample);
    selected_count := selected_count + 1;
    EXIT WHEN selected_count >= top_k;
  END LOOP;
END $$;


-- Query an existing interval without transporting samples through the client.
CREATE FUNCTION public.morlet_search_recording(collection regclass,
  source_recording_id bigint, source_start_sample integer, source_end_sample integer,
  metadata_filter jsonb DEFAULT '{}'::jsonb, normalization text DEFAULT 'zscore',
  top_k integer DEFAULT 10, suppress_overlaps boolean DEFAULT true)
RETURNS TABLE(recording_id bigint,start_sample integer,end_sample integer,distance float8,extractor_version text)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE source record;
BEGIN
  IF source_start_sample < 0 OR source_end_sample <= source_start_sample
    OR source_end_sample::bigint-source_start_sample::bigint NOT BETWEEN 512 AND 4096 THEN
    RAISE EXCEPTION 'source query must be a valid half-open interval of 512..4096 samples';
  END IF;
  EXECUTE format('SELECT samples,sample_rate_hz,channel,unit FROM %s WHERE id=$1',collection)
    INTO source USING source_recording_id;
  IF source.samples IS NULL THEN
    RAISE EXCEPTION 'source recording does not exist or is not visible';
  END IF;
  IF source_end_sample > cardinality(source.samples) THEN
    RAISE EXCEPTION 'source interval exceeds recording length';
  END IF;
  RETURN QUERY SELECT * FROM public.morlet_search_features(collection,
    source.samples[source_start_sample+1:source_end_sample],source.sample_rate_hz,source.channel,source.unit,
    metadata_filter,normalization,top_k,source_recording_id,source_start_sample,source_end_sample,suppress_overlaps);
END $$;
