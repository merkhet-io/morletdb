# TC1: MorletDB and TimescaleDB in one database

Frozen before workload execution, 2026-11-28. Development compatibility experiment,
not a throughput benchmark or production certification. Synthetic data only.

## Question and scope

Can one PostgreSQL 17 database store authoritative waveform chunks in pg_morlet,
ordinary telemetry and waveform-derived metrics in TimescaleDB, and preserve
atomicity, provenance and permissions? Keep MorletDB managed tables ordinary tables.
Use TimescaleDB 2.31.0 (the version used by B3) and record the actual MorletDB build,
SQL version, image IDs, architecture, source revision and protocol digest.
Installation/build diagnostics may precede execution and are recorded separately.

## Fixed cases

1. Create extensions in both orders in fresh databases. Exercise raw append/read
   and a hypertable in both. No pgvector requirement for this first experiment.
2. Deterministic 1024-sample waveform: repeating [-0.0, 1.0, -1.0, 2.0], 1024 Hz.
   Expected count=1024, min=-1, max=2, mean=0.5, RMS=sqrt(1.5). Compare binary
   float64 read-back, including signed zero. Store one derived row with recording
   and half-open interval references, asset, count and calculation version.
3. Commit both writes together; abort after both writes and check neither persists.
   Identical retry must not duplicate either side. Altered waveform retry must
   fail and leave both unchanged. Kill a writer with an uncommitted paired write,
   check rollback, then retry and commit on a new connection.
4. Insert irregular scalar readings out of timestamp order. Build a minute
   continuous aggregate; observe before/after explicit refresh of a late reading.
   Use fixed timestamps; no automatic scheduler timing claim.
5. Join a metric to its waveform interval and read it exactly. Compress the metric
   hypertable and verify values and drill-down again. Drop old telemetry chunks;
   verify independent MorletDB waveform remains. Observe aggregate behavior after
   refreshing a source range removed by retention (separate disposable fixture).
6. Two tenants and a non-owner role: direct MorletDB parent/chunk reads and metric
   reads must obey explicitly configured RLS. Probe continuous aggregate creation
   with RLS before/after creation, and compression with RLS, recording rejection
   or behavior. Do not interpret an unsupported combination as secure support.
   If an unexpected vulnerability is suspected, follow SECURITY.md and do not
   publish exploit details in issues.
7. Restart the container and verify committed samples and metrics. Logical
   pg_dump/pg_restore to a fresh database using Timescale pre/post-restore hooks;
   verify samples, metrics and aggregate results. Record restore failures verbatim.

## Verdicts and publication

Each case records observed values or SQLSTATE/error and assertions. Preserve failed
attempts; harness corrections are deviations, not new confirmations. No timing or
storage superiority claim. No live Grafana UI, failover, upgrades, scheduled policy,
multi-node, long soak or concurrent-load validation is claimed. Issues distinguish
working prototypes, product gaps and unsupported combinations, and link related
existing issues rather than duplicating them. Credentials and private data are
never included in artifacts. No changes to shipped schema or codec are planned.
