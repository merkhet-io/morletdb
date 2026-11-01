-- Functional check of an installed pg_morlet build (with pgvector) on synthetic data, in an empty
-- database (artifact step 4; no downloads). Fails with an exception on any mismatch; prints PASS.
--   createdb morlet_smoke && psql -v ON_ERROR_STOP=1 -d morlet_smoke -f evaluation/consolidated/smoke_check.sql
CREATE EXTENSION pg_morlet;
CREATE EXTENSION vector;
SELECT morlet_create_stream_collection('public', 'smoke');
SELECT morlet_enable_envelopes('smoke');
-- three recordings: the same burst centred at 4000 in A and at 10100 in B, noise only in C
SELECT morlet_start_recording('smoke', r, 100, 'HHZ', 'counts', jsonb_build_object('site', s), 512, 256, 'zscore', 'none')
FROM (VALUES ('A', 'north'), ('B', 'north'), ('C', 'south')) v(r, s);
-- a burst a few hundred samples wide, so that it shapes a 4096-sample envelope
CREATE TEMP TABLE burst AS
  SELECT i, round(800 * exp(-power((i - 3000) / 400., 2)) * sin(i / 2.))::float8 AS v FROM generate_series(0, 5999) i;
SELECT morlet_append_chunk('smoke', r, 0, ARRAY(
  SELECT (round(20 * sin(s.i * 1.7 + off) + ((s.i * 7919 + off * 13) % 23)) + coalesce(b.v, 0))::float8
  FROM generate_series(0, 32767) s(i) LEFT JOIN burst b ON b.i = s.i - at ORDER BY s.i))
FROM (VALUES ('A', 1000, 1), ('B', 7100, 2), ('C', -100000, 3)) x(r, at, off);
SELECT morlet_build_envelopes('smoke', r, 4096, 2048) FROM unnest(ARRAY['A', 'B', 'C']) r;
SELECT morlet_enable_envelope_probes('smoke', 4096);

DO $$
DECLARE exact text; probe text; st record; n_read integer;
BEGIN
  SELECT * INTO st FROM morlet_collection_status('smoke') WHERE window_size = 4096;
  IF st.envelopes <> 45 OR st.probe_backlog <> 0 THEN
    RAISE EXCEPTION 'unexpected status: % envelopes, backlog %', st.envelopes, st.probe_backlog;
  END IF;
  SELECT string_agg(recording_id || ':' || start_sample || ':' || distance, ',' ORDER BY distance, recording_id, start_sample)
    INTO exact FROM morlet_search_envelope_interval('smoke', 'A', 2048, 6144, '{}', 5);
  -- probe path forced (exact_threshold 0); 13 probes x 100 rows cover this 45-window gallery
  SELECT string_agg(recording_id || ':' || start_sample || ':' || distance, ',' ORDER BY distance, recording_id, start_sample)
    INTO probe FROM morlet_search_envelope_probes('smoke', 'A', 2048, 6144, '{}', 5, 0.075, true, true, 100, 5, 0);
  IF exact IS DISTINCT FROM probe THEN
    RAISE EXCEPTION 'probe and exact results differ: % vs %', probe, exact;
  END IF;
  IF split_part(exact, ':', 1) <> 'B' THEN
    RAISE EXCEPTION 'the burst in B should be the best match, got %', exact;
  END IF;
  SELECT cardinality(samples) INTO n_read FROM morlet_read_interval('smoke', 'B', 8192, 12288);
  IF n_read <> 4096 THEN RAISE EXCEPTION 'interval read returned % samples', n_read; END IF;
  RAISE NOTICE 'PASS: % envelopes, backlog 0, probe = exact (%), interval read 4096 samples', st.envelopes, split_part(exact, ',', 1);
END $$;
