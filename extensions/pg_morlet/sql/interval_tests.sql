SELECT morlet_create_stream_collection('public','interval_streams');
SELECT morlet_start_recording('interval_streams','A',4096,'x','g','{"asset":"one"}');
SELECT morlet_start_recording('interval_streams','B',4096,'x','g','{"asset":"two"}');
SELECT morlet_append_chunk('interval_streams','A',0,array_fill(0.::float8,ARRAY[137]));
SELECT morlet_append_chunk('interval_streams','A',137,array_fill(0.::float8,ARRAY[1399]));
SELECT morlet_append_chunk('interval_streams','B',0,array_fill(0.::float8,ARRAY[1536]));
DO $$
DECLARE raw float8[]; starts bigint[]; n bigint; m jsonb;
BEGIN
  SELECT samples INTO raw FROM morlet_read_interval('interval_streams','A',100,700);
  ASSERT cardinality(raw)=600 AND raw=array_fill(0.::float8,ARRAY[600]), 'cross-chunk interval reconstruction';
  SELECT metadata INTO m FROM morlet_read_interval('interval_streams','B',0,512);
  ASSERT m='{"asset":"two"}'::jsonb, 'read provenance';
  SELECT array_agg(start_sample ORDER BY start_sample) INTO starts
    FROM morlet_search_interval('interval_streams','A',0,512) WHERE recording_id='A';
  ASSERT starts=ARRAY[512,1024]::bigint[], 'source exclusion and adjacent nonoverlap';
  SELECT count(*) INTO n FROM morlet_search_interval('interval_streams','A',0,512,'{}',10,false,false);
  ASSERT n=10, 'explicit inclusion returns all windows';
  SELECT array_agg(start_sample ORDER BY start_sample) INTO starts
    FROM morlet_search_interval('interval_streams','A',0,512,'{"asset":"two"}',2);
  ASSERT starts=ARRAY[0,512]::bigint[], 'overlap suppression must happen before top-k';
  SELECT count(*) INTO n FROM morlet_search_interval('interval_streams','A',0,512,'{"asset":"missing"}');
  ASSERT n=0, 'metadata filter';
  BEGIN
    PERFORM morlet_read_interval('interval_streams','A',0,1537);
    RAISE EXCEPTION 'expected bounds rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'source interval exceeds recording length' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM morlet_read_interval('interval_streams','A',-1,512);
    RAISE EXCEPTION 'expected negative coordinate rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'read interval must contain 1..32768 samples at nonnegative coordinates' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM morlet_search_interval('interval_streams','A',0,768);
    RAISE EXCEPTION 'expected window mismatch rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'query interval must match the stored representation window' THEN RAISE; END IF;
  END;
END $$;

-- A nonzero ramp checks slicing and order independently of constant-signal tests.
SELECT morlet_start_recording('interval_streams','ramp',4096,'ramp','a.u.');
SELECT morlet_append_chunk('interval_streams','ramp',0,ARRAY(SELECT i::float8 FROM generate_series(0,136) i));
SELECT morlet_append_chunk('interval_streams','ramp',137,ARRAY(SELECT i::float8 FROM generate_series(137,999) i));
DO $$ BEGIN
  ASSERT (SELECT samples FROM morlet_read_interval('interval_streams','ramp',120,777))=
    ARRAY(SELECT i::float8 FROM generate_series(120,776) i), 'slice ordering';
END $$;

-- A visible parent must not bypass chunk RLS; an invisible parent must not be
-- discoverable through permissive child policies. Run as a non-owner role.
ALTER TABLE interval_streams ENABLE ROW LEVEL SECURITY;
ALTER TABLE interval_streams_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE interval_streams_segments ENABLE ROW LEVEL SECURITY;
CREATE POLICY visible_parent ON interval_streams USING(recording_id='A');
CREATE POLICY visible_chunks ON interval_streams_chunks USING(recording_id='A' AND start_sample=137);
CREATE POLICY visible_segments ON interval_streams_segments USING(true);
CREATE ROLE morlet_stream_interval_reader;
GRANT SELECT ON interval_streams,interval_streams_chunks,interval_streams_segments TO morlet_stream_interval_reader;
SET LOCAL ROLE morlet_stream_interval_reader;
DO $$ BEGIN
  BEGIN
    PERFORM morlet_read_interval('interval_streams','B',0,512);
    RAISE EXCEPTION 'expected hidden parent rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'source recording does not exist or is not visible' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM morlet_read_interval('interval_streams','A',0,512);
    RAISE EXCEPTION 'expected hidden chunk rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'source interval is incomplete or not fully visible' THEN RAISE; END IF;
  END;
  ASSERT (SELECT count(*) FROM morlet_search_interval('interval_streams','A',512,1024,'{}',10,false,false))=5,
    'search cannot reveal hidden parents';
END $$;
RESET ROLE;
REVOKE SELECT ON interval_streams_chunks FROM morlet_stream_interval_reader;
SET LOCAL ROLE morlet_stream_interval_reader;
DO $$ BEGIN
  BEGIN
    PERFORM morlet_read_interval('interval_streams','A',512,1024);
    RAISE EXCEPTION 'expected table privilege rejection';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
RESET ROLE;
