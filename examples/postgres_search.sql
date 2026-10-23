-- psql -v ON_ERROR_STOP=1 -f examples/postgres_search.sql DATABASE
-- Requires pg_morlet installed. The demo rolls back its schema and data.
\set ON_ERROR_STOP on
BEGIN;
CREATE SCHEMA morlet_demo;
SELECT morlet_create_collection('morlet_demo','recordings');
SELECT morlet_enable_segments('morlet_demo.recordings',512,256,'zscore');
INSERT INTO morlet_demo.recordings(sample_rate_hz,channel,unit,metadata,samples)
VALUES
  (4096,'vibration','g','{"asset":"A"}',
    ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,1535) i)),
  (4096,'vibration','g','{"asset":"B"}',
    ARRAY(SELECT 2*sin(i::float8/8)+3 FROM generate_series(0,1023) i));
-- Query recording 1's first 512 samples. Its overlapping windows are excluded;
-- other intervals and recordings remain searchable. zscore removes gain/DC.
SELECT * FROM morlet_search_recording('morlet_demo.recordings',1,0,512);
-- Compare with all ranked windows, including overlapping hits.
SELECT * FROM morlet_search_recording('morlet_demo.recordings',1,0,512,'{}','zscore',10,false);
ROLLBACK;
