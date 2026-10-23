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

CREATE FUNCTION public.morlet_search_features(
  collection regclass, query_samples float8[], query_rate_hz float8,
  query_channel text, query_unit text, metadata_filter jsonb DEFAULT '{}'::jsonb,
  normalization text DEFAULT 'zscore', top_k integer DEFAULT 10)
RETURNS TABLE(recording_id bigint, start_sample integer, end_sample integer,
  distance float8, extractor_version text)
LANGUAGE plpgsql STABLE STRICT SECURITY INVOKER SET search_path = pg_catalog AS $$
DECLARE window_size integer; query_features float8[]; candidate_count bigint; incompatible boolean;
BEGIN
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
  EXECUTE format('SELECT coalesce(sum(jsonb_array_length(morlet_grid->''segments'')),0)::bigint,
    coalesce(bool_or((morlet_grid->>''version'') IS DISTINCT FROM public.morlet_extractor_version()
      OR (morlet_grid->>''window_size'')::integer IS DISTINCT FROM $1
      OR (morlet_grid->>''normalization'') IS DISTINCT FROM $2),false)
    FROM %s WHERE sample_rate_hz=$3 AND channel=$4 AND unit=$5 AND metadata @> $6',collection)
    INTO candidate_count,incompatible USING window_size,normalization,query_rate_hz,query_channel,query_unit,metadata_filter;
  IF incompatible THEN
    RAISE EXCEPTION 'incompatible segment representation: match window and normalization, or rebuild for the current extractor';
  END IF;
  IF candidate_count > 4096 THEN
    RAISE EXCEPTION 'feature search exceeds 4096 candidate segments; narrow the metadata filter';
  END IF;
  RETURN QUERY EXECUTE format('SELECT r.id,(s->>''start_sample'')::integer,
      (s->>''start_sample'')::integer+$1,
      public.morlet_feature_vector_distance(ARRAY(SELECT jsonb_array_elements_text(s->''features'')::float8),$2) AS distance,
      r.morlet_grid->>''version''
    FROM %s r CROSS JOIN LATERAL jsonb_array_elements(r.morlet_grid->''segments'') s
    WHERE r.sample_rate_hz=$3 AND r.channel=$4 AND r.unit=$5 AND r.metadata @> $6
    ORDER BY distance,r.id,(s->>''start_sample'')::integer LIMIT $7',collection)
    USING window_size,query_features,query_rate_hz,query_channel,query_unit,metadata_filter,top_k;
END $$;
