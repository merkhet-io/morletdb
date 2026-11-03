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

-- Chunk summaries: interval statistics and overviews are bitwise the same with and without them.
SELECT morlet_create_stream_collection('public','summary_streams');
CREATE TABLE summary_legacy(recording_id text PRIMARY KEY);
CREATE TABLE summary_legacy_chunks(recording_id text,start_sample bigint,samples float8[]);
CREATE TABLE summary_legacy_segments(recording_id text,start_sample bigint,features float8[]);
SELECT morlet_start_recording('summary_streams','s',4096,'x','g','{}',512,256,'none','morlet-energy-v1',0.001);
SELECT morlet_append_chunk('summary_streams','s',k*3000,
  ARRAY(SELECT round(1000*(sin(i/37.)*(1+i/20000.)+0.3*cos(i/3.)+((i*7919)%101)/500.))/1000.
        FROM generate_series(k*3000,k*3000+2999) i))
  FROM generate_series(0,19) k ORDER BY k;
CREATE TEMP TABLE summary_cases(a bigint,b bigint,buckets integer);
INSERT INTO summary_cases VALUES (0,60000,1),(1,59999,1),(2999,3001,1),(3000,6000,1),(100,57000,1),(0,60000,7),(450,58000,40),(0,60000,1000);
CREATE TEMP TABLE summary_before AS
  SELECT c.*,o.* FROM summary_cases c,LATERAL morlet_interval_overview('summary_streams','s',c.a,c.b,c.buckets) o;
CREATE TEMP TABLE stats_before AS
  SELECT c.a,c.b,st.* FROM summary_cases c,LATERAL morlet_interval_stats('summary_streams','s',c.a,c.b) st WHERE c.buckets=1;
DO $$
DECLARE n bigint;
BEGIN
  BEGIN
    PERFORM morlet_build_chunk_summaries('summary_streams','s');
    RAISE EXCEPTION 'expected summaries-not-enabled rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'chunk summaries are not enabled%' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM morlet_interval_stats('summary_streams','s',0,16777217);
    RAISE EXCEPTION 'expected length rejection without summaries';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'statistics interval must contain 1..16777216 samples at nonnegative coordinates' THEN RAISE; END IF;
  END;
  PERFORM morlet_enable_chunk_summaries('summary_streams');
  -- appends after enabling write summaries; chunks stored before are covered by the builder
  PERFORM morlet_append_chunk('summary_streams','s',60000,
    ARRAY(SELECT round(1000*sin(i/37.))/1000. FROM generate_series(60000,62999) i));
  ASSERT (SELECT count(summary) FROM summary_streams_chunks)=1, 'append writes the summary';
  n:=morlet_build_chunk_summaries('summary_streams','s',5);
  ASSERT n=5, 'builder respects max_chunks';
  n:=morlet_build_chunk_summaries('summary_streams','s');
  ASSERT n=15 AND morlet_build_chunk_summaries('summary_streams','s')=0, 'builder covers the rest, then is idempotent';
  ASSERT NOT EXISTS(SELECT 1 FROM summary_streams_chunks c WHERE c.summary IS DISTINCT FROM morlet_encoded_chunk_summary(c.encoded)),
    'appended and built summaries agree';
  ASSERT (SELECT bool_and((morlet_chunk_summary_values(summary))[1]=3000) FROM summary_streams_chunks), 'summary counts';
  ASSERT morlet_enable_chunk_summaries('summary_streams')='summary_streams_chunks'::regclass, 'enabling again is a no-op';
  BEGIN
    PERFORM morlet_interval_stats('summary_streams','s',0,16777217);
    RAISE EXCEPTION 'expected recording-length rejection with summaries';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'source interval exceeds recording length' THEN RAISE; END IF;
  END;
END $$;
DO $$
BEGIN
  ASSERT NOT EXISTS(
    (SELECT a,b,buckets,bucket_start,bucket_end,sample_count,float8send(min),float8send(max),float8send(mean),float8send(rms) FROM summary_before)
    EXCEPT ALL
    (SELECT c.a,c.b,c.buckets,o.bucket_start,o.bucket_end,o.sample_count,float8send(o.min),float8send(o.max),float8send(o.mean),float8send(o.rms)
     FROM summary_cases c,LATERAL morlet_interval_overview('summary_streams','s',c.a,c.b,c.buckets) o)), 'overviews bitwise unchanged by summaries';
  ASSERT NOT EXISTS(
    (SELECT a,b,sample_count,float8send(min),float8send(max),float8send(mean),float8send(rms) FROM stats_before)
    EXCEPT ALL
    (SELECT c.a,c.b,st.sample_count,float8send(st.min),float8send(st.max),float8send(st.mean),float8send(st.rms)
     FROM summary_cases c,LATERAL morlet_interval_stats('summary_streams','s',c.a,c.b) st WHERE c.buckets=1)), 'statistics bitwise unchanged by summaries';
  -- overviews partition the interval; one bucket equals the statistics
  ASSERT (SELECT bool_and(t.total=c.b-c.a) FROM summary_cases c,LATERAL (SELECT sum(o.sample_count) total
    FROM morlet_interval_overview('summary_streams','s',c.a,c.b,c.buckets) o) t), 'buckets cover the interval';
  ASSERT (SELECT count(*) FROM morlet_interval_overview('summary_streams','s',0,60000,7))=7, 'seven buckets';
  ASSERT (SELECT count(*) FROM morlet_interval_overview('summary_streams','s',0,60000,1000))=20, 'at most one bucket per chunk';
  ASSERT (SELECT abs(st.mean-x.m)<1e-12 AND st.min=x.lo AND st.max=x.hi
    FROM morlet_interval_stats('summary_streams','s',100,57000) st,
      (SELECT avg(v) m,min(v) lo,max(v) hi FROM summary_streams_chunks c,
         LATERAL unnest(morlet_decode_chunk(c.encoded)) WITH ORDINALITY u(v,j)
       WHERE c.start_sample+j-1>=100 AND c.start_sample+j-1<57000) x), 'statistics against the samples';
  ASSERT (SELECT bucket_start=0 AND bucket_end=63000 FROM morlet_interval_overview('summary_streams','s',0,63000,1)), 'bucket bounds';
  -- legacy float8[] collections cannot have summaries
  BEGIN
    PERFORM morlet_enable_chunk_summaries('summary_legacy');
    RAISE EXCEPTION 'expected legacy rejection';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM<>'chunk summaries need a collection with encoded chunks' THEN RAISE; END IF;
  END;
END $$;
