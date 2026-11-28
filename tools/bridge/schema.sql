-- MorletDB metric bridge, schema version 1 (#639). Experimental.
-- Owned by the trusted bridge writer; one database per trust domain (sources with row security are refused).
-- No SECURITY DEFINER functions and no PUBLIC grants: readers of metrics get explicit SELECT grants.
CREATE SCHEMA morlet_bridge;
REVOKE ALL ON SCHEMA morlet_bridge FROM PUBLIC;

CREATE TABLE morlet_bridge.config (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    schema_version integer NOT NULL CHECK (schema_version = 1)
);
INSERT INTO morlet_bridge.config(schema_version) VALUES (1);

-- A registered recording and the calculation published from it. The specification, time axis and
-- interpretation revision are fixed at registration: if any of them changes, publication stops until the
-- metrics are reviewed (a calibration correction changes what the samples mean, #600).
CREATE TABLE morlet_bridge.sources (
    source_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collection text NOT NULL,
    recording_id text NOT NULL,
    asset text NOT NULL CHECK (asset <> ''),
    channel text NOT NULL CHECK (channel <> ''),
    window_samples integer NOT NULL CHECK (window_samples BETWEEN 1 AND 1048576),
    calculation text NOT NULL CHECK (calculation = 'interval-stats-v1'),
    specification jsonb NOT NULL,
    time_axis_version text NOT NULL,
    interpretation_revision integer,          -- NULL: the collection tracks no interpretation history
    processed_end bigint NOT NULL DEFAULT 0 CHECK (processed_end >= 0),
    registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (collection, recording_id, calculation)
);

-- The ledger: one row per published interval, the source of truth for every sink.
CREATE TABLE morlet_bridge.ledger (
    ledger_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id bigint NOT NULL REFERENCES morlet_bridge.sources,
    start_sample bigint NOT NULL CHECK (start_sample >= 0),
    end_sample bigint NOT NULL CHECK (end_sample > start_sample),
    time_ns bigint NOT NULL,
    end_time_ns bigint NOT NULL CHECK (end_time_ns > time_ns),
    sample_count bigint NOT NULL CHECK (sample_count = end_sample - start_sample),
    minimum float8 NOT NULL, maximum float8 NOT NULL, mean float8 NOT NULL, rms float8 NOT NULL,
    source_sha256 text NOT NULL CHECK (length(source_sha256) = 64),
    payload_sha256 text NOT NULL CHECK (length(payload_sha256) = 64),
    provenance jsonb NOT NULL,
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (source_id, start_sample, end_sample)
);

-- Sinks and what each has been given. A delivery row is written in the same transaction as a PostgreSQL sink's
-- row (exact), or after a line-protocol batch was written out (at least once).
CREATE TABLE morlet_bridge.sinks (
    sink_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE CHECK (name ~ '^[a-z][a-z0-9_-]{0,62}$'),
    kind text NOT NULL CHECK (kind IN ('postgres', 'line')),
    target text,                              -- postgres: the target table; line: NULL
    options jsonb NOT NULL DEFAULT '{}',
    last_delivery_at timestamptz,
    last_error text,
    last_error_at timestamptz,
    CHECK ((kind = 'postgres') = (target IS NOT NULL))
);
CREATE TABLE morlet_bridge.deliveries (
    sink_id bigint NOT NULL REFERENCES morlet_bridge.sinks,
    ledger_id bigint NOT NULL REFERENCES morlet_bridge.ledger,
    delivered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (sink_id, ledger_id)
);

-- The default PostgreSQL sink target: an ordinary table. Its key includes time_ns so that it can be turned into a
-- TimescaleDB hypertable on time_ns (bigint nanoseconds; not yet tested). The bridge never requires TimescaleDB.
CREATE TABLE morlet_bridge.metrics (
    ledger_id bigint NOT NULL,
    time_ns bigint NOT NULL,
    end_time_ns bigint NOT NULL,
    asset text NOT NULL,
    channel text NOT NULL,
    calculation text NOT NULL,
    collection text NOT NULL,
    recording_id text NOT NULL,
    start_sample bigint NOT NULL,
    end_sample bigint NOT NULL,
    sample_count bigint NOT NULL,
    minimum float8 NOT NULL, maximum float8 NOT NULL, mean float8 NOT NULL, rms float8 NOT NULL,
    interpretation_revision integer,
    PRIMARY KEY (time_ns, ledger_id)
);
CREATE INDEX ON morlet_bridge.metrics (ledger_id);
CREATE INDEX ON morlet_bridge.metrics (asset, channel, time_ns);

REVOKE ALL ON ALL TABLES IN SCHEMA morlet_bridge FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA morlet_bridge FROM PUBLIC;
