\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS pg_morlet;
SELECT morlet_version();
SELECT morlet_rms(ARRAY[3.,4.]::float8[]);
SELECT morlet_normalize(ARRAY[1.,2.,3.]::float8[], 'zscore');
SELECT morlet_distance(ARRAY[1.,2.,3.]::float8[], ARRAY[12.,14.,16.]::float8[], 'zscore');
SELECT morlet_extractor_version();
SELECT cardinality(morlet_features(array_fill(0.::float8,ARRAY[512]),4096.));
SELECT morlet_signature(array_fill(0.::float8,ARRAY[512]),4096.)->>'version';
-- Functions only: this does not create waveform storage or an index.
