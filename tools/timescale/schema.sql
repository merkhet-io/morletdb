-- Optional adapter schema v1, owned by the trusted integration writer.
-- One database per trust domain. No SECURITY DEFINER functions or PUBLIC grants.
CREATE SCHEMA morlet_metrics;
REVOKE ALL ON SCHEMA morlet_metrics FROM PUBLIC;
CREATE TABLE morlet_metrics.config (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    schema_version integer NOT NULL CHECK (schema_version = 1),
    pruned_before_ns bigint NOT NULL DEFAULT '-9223372036854775808',
    worker_cursor bigint NOT NULL DEFAULT 0
);
INSERT INTO morlet_metrics.config(schema_version) VALUES (1);
CREATE TABLE morlet_metrics.sources (
    source_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collection text NOT NULL,
    recording_id text NOT NULL,
    asset text NOT NULL,
    window_samples integer NOT NULL CHECK (window_samples BETWEEN 1 AND 32768),
    calculation_version text NOT NULL CHECK (calculation_version = 'interval-stats-v1'),
    specification jsonb NOT NULL,
    time_axis_version text NOT NULL,
    processed_end bigint NOT NULL DEFAULT 0 CHECK (processed_end >= 0),
    UNIQUE (collection, recording_id, calculation_version)
);
-- This small ledger survives metric expiry. Its uniqueness is independent of time;
-- changing an anchor cannot evade retry verification by moving to another chunk.
CREATE TABLE morlet_metrics.evidence (
    evidence_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id bigint NOT NULL REFERENCES morlet_metrics.sources,
    start_sample bigint NOT NULL CHECK (start_sample >= 0),
    end_sample bigint NOT NULL CHECK (end_sample > start_sample),
    time_ns bigint NOT NULL,
    end_time_ns bigint NOT NULL CHECK (end_time_ns > time_ns),
    source_sha256 text NOT NULL CHECK (length(source_sha256) = 64),
    payload_sha256 text NOT NULL CHECK (length(payload_sha256) = 64),
    time_provenance jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (source_id, start_sample, end_sample)
);
CREATE TABLE morlet_metrics.metrics (
    time_ns bigint NOT NULL,
    evidence_id bigint NOT NULL REFERENCES morlet_metrics.evidence,
    source_id bigint NOT NULL REFERENCES morlet_metrics.sources,
    sample_count bigint NOT NULL CHECK (sample_count > 0),
    minimum float8 NOT NULL, maximum float8 NOT NULL, mean float8 NOT NULL, rms float8 NOT NULL,
    PRIMARY KEY (time_ns, evidence_id)
);
SELECT public.create_hypertable('morlet_metrics.metrics', 'time_ns',
    chunk_time_interval => 86400000000000::bigint);
CREATE FUNCTION morlet_metrics.now_ns() RETURNS bigint
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
    SELECT (extract(epoch FROM statement_timestamp()) * 1000000000)::bigint
$$;
SELECT public.set_integer_now_func('morlet_metrics.metrics','morlet_metrics.now_ns');
CREATE MATERIALIZED VIEW morlet_metrics.minute
WITH (timescaledb.continuous, timescaledb.materialized_only=true) AS
SELECT public.time_bucket(60000000000::bigint, time_ns) AS bucket_ns, source_id,
    count(*) AS intervals, sum(sample_count) AS samples,
    min(minimum) AS minimum, max(maximum) AS maximum,
    sum(mean * sample_count) / sum(sample_count) AS mean,
    sqrt(sum(rms * rms * sample_count) / sum(sample_count)) AS rms
FROM morlet_metrics.metrics GROUP BY 1,2 WITH NO DATA;
CREATE TABLE morlet_metrics.refreshes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    start_ns bigint NOT NULL, end_ns bigint NOT NULL,
    finished_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor text NOT NULL DEFAULT current_user
);
CREATE TABLE morlet_metrics.retention_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan jsonb NOT NULL, applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor text NOT NULL DEFAULT current_user
);
CREATE TABLE morlet_metrics.worker_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    state text NOT NULL CHECK (state IN ('started','complete','failed')),
    details jsonb NOT NULL DEFAULT '{}'
);
-- Telemetry is independent of waveform recording constraints. Exact integer time
-- and typed columns; a temperature value is never silently cast to waveform data.
CREATE TABLE morlet_metrics.telemetry (
    time_ns bigint NOT NULL, asset text NOT NULL,
    temperature float8, energy_counter bigint, state text,
    PRIMARY KEY(time_ns, asset)
);
SELECT public.create_hypertable('morlet_metrics.telemetry','time_ns',
    chunk_time_interval => 86400000000000::bigint);
REVOKE ALL ON ALL TABLES IN SCHEMA morlet_metrics FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA morlet_metrics FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA morlet_metrics FROM PUBLIC;
