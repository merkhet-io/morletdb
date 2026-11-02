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
DO $$ DECLARE st record; BEGIN
  ASSERT (SELECT samples FROM morlet_read_interval('interval_streams','ramp',120,777))=
    ARRAY(SELECT i::float8 FROM generate_series(120,776) i), 'slice ordering';
  -- raw reads: the same samples as little-endian float64 bytes (float8send is big-endian)
  ASSERT morlet_read_interval_raw('interval_streams','ramp',120,777)=
    (SELECT string_agg(b,''::bytea ORDER BY i) FROM generate_series(120,776) i,
       LATERAL (SELECT string_agg(substring(float8send(i::float8) FROM 9-j FOR 1),''::bytea ORDER BY j) b
                FROM generate_series(1,8) j) x), 'raw read bytes';
  SELECT * INTO st FROM morlet_interval_stats('interval_streams','ramp',120,777);
  ASSERT st.sample_count=657 AND st.min=120 AND st.max=776 AND st.mean=448, 'interval statistics';
  ASSERT abs(st.rms/(SELECT sqrt(avg((i*i)::float8)) FROM generate_series(120,776) i)-1)<1e-12, 'interval rms';
  BEGIN
    PERFORM morlet_read_interval_raw('interval_streams','ramp',0,1001);
    RAISE EXCEPTION 'expected raw bounds rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'source interval exceeds recording length' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM morlet_interval_stats('interval_streams','ramp',-1,10);
    RAISE EXCEPTION 'expected statistics coordinate rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'statistics interval must contain 1..16777216 samples at nonnegative coordinates' THEN RAISE; END IF;
  END;
END $$;

-- Raw appends: little-endian float64 bytes go through morlet_append_chunk unchanged.
SELECT morlet_start_recording('interval_streams','rawapp',4096,'raw','a.u.');
CREATE TEMP TABLE raw_le AS SELECT (SELECT string_agg(b,''::bytea ORDER BY i) FROM generate_series(0,299) i,
  LATERAL (SELECT string_agg(substring(float8send((i*0.25-7)::float8) FROM 9-j FOR 1),''::bytea ORDER BY j) b
           FROM generate_series(1,8) j) x) AS bytes;
DO $$ DECLARE raw bytea:=(SELECT bytes FROM raw_le); BEGIN
  ASSERT morlet_append_chunk_raw('interval_streams','rawapp',0,raw)=300, 'raw append returns the new end';
  ASSERT (SELECT samples FROM morlet_read_interval('interval_streams','rawapp',0,300))=
    ARRAY(SELECT (i*0.25-7)::float8 FROM generate_series(0,299) i), 'raw append stores the samples';
  ASSERT morlet_append_chunk_raw('interval_streams','rawapp',0,raw)=300, 'identical raw replay';
  BEGIN
    PERFORM morlet_append_chunk_raw('interval_streams','rawapp',0,overlay(raw PLACING '\x01'::bytea FROM 9));
    RAISE EXCEPTION 'expected conflicting raw replay rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'conflicting replay%' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM morlet_append_chunk_raw('interval_streams','rawapp',300,'\x0102'::bytea);
    RAISE EXCEPTION 'expected raw length rejection';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM<>'raw samples must be a multiple of 8 bytes (little-endian float64)' THEN RAISE; END IF;
  END;
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
  BEGIN
    PERFORM morlet_read_interval_raw('interval_streams','A',0,512);
    RAISE EXCEPTION 'expected hidden chunk rejection (raw)';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'source interval is incomplete or not fully visible' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM morlet_interval_stats('interval_streams','A',0,512);
    RAISE EXCEPTION 'expected hidden chunk rejection (statistics)';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'source interval is incomplete or not fully visible' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM morlet_interval_stats('interval_streams','B',0,512);
    RAISE EXCEPTION 'expected hidden parent rejection (statistics)';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'source recording does not exist or is not visible' THEN RAISE; END IF;
  END;
  ASSERT (SELECT sample_count FROM morlet_interval_stats('interval_streams','A',137,512))=375,
    'statistics over visible chunks';
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

-- Isolated feature population to exercise budgets without expensive extraction.
-- Direct segment fixtures are not valid raw-recording ingestion examples.
SELECT morlet_create_stream_collection('public','budget_streams');
SELECT morlet_start_recording('budget_streams','population',4096,'x','g');
INSERT INTO budget_streams_segments(recording_id,start_sample,features)
  SELECT 'population',i*512::bigint,array_fill(0.::float8,ARRAY[15]) FROM generate_series(0,4096) i;
DO $$ BEGIN
  BEGIN
    PERFORM morlet_search_streams('budget_streams',array_fill(0.::float8,ARRAY[512]),4096,'x','g');
    RAISE EXCEPTION 'expected default budget rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'stream search exceeds 4096 segments; narrow the metadata filter' THEN RAISE; END IF;
  END;
  ASSERT (SELECT count(*) FROM morlet_search_streams('budget_streams',array_fill(0.::float8,ARRAY[512]),4096,'x','g','{}','zscore',10,5000))=10,
    'explicit exact-search budget';
  BEGIN
    PERFORM morlet_search_streams('budget_streams',array_fill(0.::float8,ARRAY[512]),4096,'x','g','{}','zscore',10,1000001);
    RAISE EXCEPTION 'expected maximum budget rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'candidate budget must be 1..1000000' THEN RAISE; END IF;
  END;
END $$;
