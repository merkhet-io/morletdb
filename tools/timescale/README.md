# Optional TimescaleDB adapter (experimental)

This adapter publishes small waveform-derived metrics to TimescaleDB while the
original samples stay in MorletDB. It is a separate Python tool and SQL schema;
MorletDB itself and its portable crates do not depend on TimescaleDB.

The initial supported topology is **one PostgreSQL database per trust domain**.
Use separate databases and separate login roles for tenants that must not share
raw recordings, metrics or aggregates. PostgreSQL processes and backups can still
be shared. This implementation does not support mixed-tenant RLS sources: registration
and publication refuse them, including when RLS is enabled after registration.

This is an experimental implementation of [#631](https://github.com/merkhet-io/morletdb/issues/631)
through [#635](https://github.com/merkhet-io/morletdb/issues/635). It is not production
capacity certification or an Influx-compatible API. The optional CI tests the pinned
MorletDB 0.4.0-beta.3 / SQL 0.3.14 + TimescaleDB 2.31.0 pair on PostgreSQL 17.
The adapter's schema version is 1; install only into a fresh `morlet_metrics` schema.
There is no automatic upgrade or destructive reset command.

## Install and publish

Install both extensions as an administrator first. Then run the adapter as the
trusted integration writer, with access to the source tables and permission to
create its own schema. Python needs `psycopg[binary]==3.2.10`; the demo viewer also
needs `numpy==2.2.6`. Supply the connection through `DATABASE_URL`.

```sh
python tools/timescale/cli.py install
python tools/timescale/cli.py register public.signals recording-1 pump-A --window-samples 4096
python tools/timescale/cli.py drain 1 --max-windows 100
python tools/timescale/cli.py status 1
```

Unanchored recordings must have an exact integer `metadata.start_ns`. Anchored
recordings use `morlet_sample_utc` and store the time-axis revision and uncertainty.
Endpoints must be exactly representable as increasing bigint nanoseconds; fractional
endpoints, ambiguous mappings and windows crossing clock breaks are refused. No
samples or timestamps are rounded or resampled. Unanchored mapping uses the stable
`morlet_sample_time_ns` API, avoiding the beta.3 image's experimental no-anchor-table
UTC-mapping defect already fixed by #562 on main.

For atomic append and publication, use the same psycopg connection:

```python
# tools/timescale on PYTHONPATH
import psycopg
from bridge import Bridge

with psycopg.connect(dsn, autocommit=True,
                     options='-c statement_timeout=30000 -c lock_timeout=5000') as conn:
    bridge = Bridge(conn)
    with bridge.transaction():
        conn.execute("SELECT morlet_append_chunk('signals', %s, %s, %s::float8[])",
                     (recording_id, start_sample, samples))
        source_id = bridge.register("public.signals", recording_id, "pump-A", 4096)
        evidence_id = bridge.publish(source_id, start_sample, start_sample + 4096)
```

The window must be complete and aligned to the registered window size. Allowing
overlapping publications would double-count samples in continuous aggregates, so
this version refuses them. Partial trailing windows remain explicitly pending as a
partial tail. Source metadata/specification, window size, asset or time-axis changes
are conflicts: old metrics remain inspectable, and the writer must create an
explicitly versioned recording for a corrected interpretation. Future interpretation
revision support belongs to #600; this adapter does not invent that contract.

The identity ledger is independent of time partitions. A SHA-256 of exact raw bytes
(including signed zero), calculation identity, source specification, time provenance
and computed statistics verifies each retry. The writer computes metrics from the
source; clients cannot substitute an RMS value. The adapter compares stored metric
values as well, so `ON CONFLICT DO NOTHING` cannot mask a conflicting payload.
Concurrent publications may raise PostgreSQL `serialization_failure`: retry the
**whole** transaction, including append. Do not retry only its final statement.

## Background work and freshness

```sh
python tools/timescale/cli.py tick --max-windows 10 --max-sources 100 --refresh-minutes 60
```

Each window and its progress marker commit together. A killed worker resumes at
that commit boundary. Tick uses a persisted round-robin source cursor, bounded
source/window budgets and a recent refresh window. `worker_runs` records started,
complete and failed runs; a killed run can remain `started`, never falsely complete.
A successful tick means its selected work finished, not that the whole archive is
fully materialized: inspect `more_sources` and each source's backlog and partial tail.

The [systemd service](deploy/morlet-timescale.service) and
[timer](deploy/morlet-timescale.timer) provide an optional ten-second schedule. Adjust
paths and the dedicated service account; put `DATABASE_URL` in the protected
`/etc/morlet-timescale.env`. Install/enable them explicitly. The timer is not installed
by the adapter. Failures remain visible in the service log and database audit.

Publication progress, source sample availability and aggregate refresh are separate.
`refreshes` records only successful refresh ranges and times. Recent materialized
buckets can be stale until the next refresh; the current partial minute is not
materialized by tick. Historical backfill outside that window requires an explicit
whole-minute refresh, using bigint nanoseconds:

```sh
python tools/timescale/cli.py refresh 1767225600000000000 1767225660000000000
```

Each whole interval is assigned to the minute containing its start timestamp; a
window crossing a minute boundary is not split. These are summaries of intervals
starting in that bucket, not exact per-minute raw-sample statistics.
Statistics are sample-weighted within each source. The minute RMS combines squared
RMS and counts; it is not a bit-exact floating-point reduction or a time-weighted
average of irregular telemetry. Native Timescale telemetry remains separate.

## Retention

```sh
python tools/timescale/cli.py retention-plan 1767312000000000000 > metric-plan.json
# Inspect the exact databases/chunks/effect before applying the saved plan.
python tools/timescale/cli.py apply-retention metric-plan.json
```

A cutoff is a UTC day boundary and must advance the recorded floor. The plan is
hashed, tied to its database, previous floor and current chunk list, and revalidated
on application. All complete source windows must be published first. The adapter
refreshes the expiring range, then atomically drops metric chunks, advances the
refresh/publication floor and records the plan. A failure after refresh can be
retried without deleting anything prematurely. Later refresh and publication below
that floor are refused, protecting the retained aggregate.

Lifecycle maintenance takes an exclusive advisory lock; publication acquires its
shared lock **before** taking the repeatable-read snapshot. This prevents a waiting
publisher from reading an obsolete retention floor. Direct owner SQL and independent
Timescale policies do not take these locks: do not attach competing retention or
refresh policies to the adapter's metrics table. The trusted maintenance owner can
always bypass the contract with direct SQL; dashboard roles cannot.

Append-and-publish writers must use `Bridge.transaction()` to participate in this
coordination. Pause independent raw-only ingestion/backfill before applying metric
retention if its historical windows still need summaries. An uncoordinated append
can arrive after the completeness check; its raw samples remain safe, but publication
below the new floor will fail explicitly.

Only derived waveform metrics expire. The evidence ledger, source registration,
audit and historical aggregates remain; they need a separate long-term metadata
lifecycle before large deployments. This adapter never deletes, compacts or changes
waveform samples, and does not approve raw retention. Generic telemetry retention
is independently configured; Morlet raw retention continues to follow its existing
approval/dependency rules.

## Permissions, evidence and recovery

The installer grants nothing to PUBLIC and uses no SECURITY DEFINER functions.
Restrict CONNECT on every tenant database (PostgreSQL otherwise grants it to PUBLIC),
and grant each reader only its own database, schema and necessary SELECT rights.
Do not grant aggregate tables from other tenants. Source grants do not propagate
to copied metrics: revoking a person's access means revoking their domain/database
access too. Changing a database into a mixed-RLS tenant store is unsupported; existing
materializations would need a separately reviewed authorization migration.

The writer is trusted to maintain adapter/source tables. It is not a database-wide
administrator requirement, but this first adapter does not promise to protect its
provenance ledger from a malicious owner. Read-only Grafana/viewer roles have no
INSERT, UPDATE, DELETE or maintenance ownership.

`Bridge.resolve(evidence_id)` checks source specification and raw SHA-256 in one
snapshot. It returns `raw_available`, `source_changed`, `raw_unavailable` or a generic
`unavailable` without exposing hidden sources. Operational database errors propagate;
they are not converted to an empty/healthy result. The CLI excludes raw bytes and
returns a URL-encoded viewer link only when raw evidence verifies:

```sh
python tools/timescale/cli.py resolve 1
```

Store qualified collection names rather than PostgreSQL OIDs, so references survive
logical restore. Follow Timescale's `timescaledb_pre_restore()` / `timescaledb_post_restore()`
procedure and restore required roles/grants. Native tests exercise same-server
logical restore with compressed metrics and a **nonempty** aggregate. Cross-host
roles/global objects, extension upgrades and replica failover remain separate gates.

## Verification

```sh
docker build -t morlet-tc1:experiment -f evaluation/timescale_coexistence/Dockerfile evaluation/timescale_coexistence
python -m unittest discover -s tools/timescale/tests -v
```

The suite always starts a disposable native PostgreSQL/Timescale server; it does not
skip integration tests when Docker is absent. It tests exact bytes/statistics,
atomic rollback, altered payloads/specifications, exact/fractional time boundaries,
anchors/revisions, concurrent retries, a terminated worker, resume, retention,
retention/publication locking, non-owner grants, database isolation, compression and
logical restore. The [Grafana example](../../examples/timescale/README.md) adds API
and browser checks. CI is optional and scoped to integration paths; no core SQL/API
inventory, extension migration or persisted waveform-format change is introduced.
