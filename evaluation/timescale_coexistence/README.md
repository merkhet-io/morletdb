# TC1: same-database TimescaleDB coexistence

**Result:** the tested release pair can share one PostgreSQL database for waveform
storage, telemetry, exact search and SQL drill-down. This is a small synthetic
development experiment, not a supported integration or capacity measurement.

## Reproduce

```sh
docker build -t morlet-tc1:experiment -f evaluation/timescale_coexistence/Dockerfile evaluation/timescale_coexistence
.venv-b3/bin/python evaluation/timescale_coexistence/run.py
```

The runner requires Python with psycopg 3.2.10 (the existing B3 environment). It
creates a uniquely named container, random credentials, a localhost-only random
port, 2 CPU / 1 GiB limits, and no host data mounts. It removes its container and
anonymous volumes in `finally`. It never prints the password. Results are written
incrementally and retained by UTC run timestamp. Do not run against a real database.

The build uses TimescaleDB's full source distribution, including its TSL features;
this is not an Apache-only combined image. It was not published. The final recipe
pins the observed MorletDB digest and checks the observed Timescale release commit.
Apt build dependencies are not snapshot-pinned. The tested image was built before
those two provenance guards were added, from the identical digest and commit.

## Provenance

- Host execution: local Docker, Linux arm64 guest, other host workloads present.
- PostgreSQL 17.12, Debian bookworm.
- MorletDB release image 0.4.0-beta.3, SQL extension 0.3.14, release revision
  `c358bebe8cc24a69eebef0a482d04df597a3194d`.
- TimescaleDB 2.31.0, source `4fa2a7c6b579171a1a8966fd87a0d4189ebab43b`.
- Combined local image ID `sha256:994f2c53751f573ae6132a2f1eaa9b7541f91e911433238aec17ce23f12de4a2`.
- Harness checkout revision `f141ccc5c2f675a5bbeddec7a0e12adcc732e0d3` differs from
  the tested released binary; this is deliberately a release integration test.
- [Protocol](PROTOCOL.md) SHA-256
  `ec272c94003669a1b27eaca0106a6878079b69937465c285cd6bec99048fb225`.
- [Supplemental protocol](ADDENDUM.md) was frozen after the successful core run.
  Each report records runner and protocol hashes.

## Results

| Case | Observation |
|---|---|
| Both extension installation orders | Fresh database append/read and hypertable creation passed |
| Raw correctness | 1024 float64 samples read back bit-for-bit, including negative zero; count 1024, min -1, max 2, mean 0.5, RMS sqrt(1.5) |
| Atomicity | Intentional error after paired writes removed both; identical retry left one metric; conflicting waveform retry failed and changed neither |
| Terminated writer | Uncommitted waveform and metric absent; fresh connection retried and committed both |
| Late telemetry | Two irregular rows gave count 2 / mean 3; late value 12 left the materialized result unchanged until explicit refresh, then count 3 / mean 6 |
| Compressed metric drill-down | Two metric rows survived Timescale compression; joined waveform intervals read back bit-exactly |
| Direct non-owner reads | Explicit RLS on both ordinary waveform and metric tables restricted reads to tenant a; Morlet RLS helper before/after summary/roll-up enablement checked |
| RLS and aggregates | Creating a continuous aggregate on an RLS hypertable rejected with SQLSTATE 0A000; enabling RLS after aggregate creation accepted. No aggregate was granted to the tenant; this does not establish aggregate authorization support |
| RLS and columnstore | Enabling compression on the RLS hypertable rejected with SQLSTATE 0A000 |
| Retention | Dropping telemetry chunks left Morlet raw data intact. Materialized history initially survived; refreshing the deleted source range removed that aggregate row |
| Restart | Committed samples and metric rows preserved |
| Logical restore | pg_dump/pg_restore with Timescale pre/post-restore hooks preserved samples, compressed metrics, and the expected empty aggregate after the retention probe |
| D1: changed metric retry | Prototype ON CONFLICT DO NOTHING silently discarded an RMS=99 retry under the original key. This is a prototype gap, not a shipped MorletDB bug |
| D1: timestamp identity | Values separated by 3 ns remained two distinct bigint timestamps but became one distinct timestamptz display value |
| D1: exact search | Two 8192-sample recordings with varying envelopes: query 0..4096 returned match 0..4096, distance 0, exact source bytes; same result after restart and restore |

The latest report has **12 passing case groups**, including diagnostics that
successfully observed unsupported combinations. It does not mean every desired
capability is supported. RLS checks use a non-owner `SET LOCAL ROLE`; no aggregate
was exposed to a tenant. The additional roll-up tables were empty: this test checks
enablement interaction, not tenant visibility of populated roll-up summaries.

### Attempts and deviations

1. [Initial run](results/20261128T121204Z.json): eight core groups passed. Restart
   readiness timed out because Docker changed the random published host port from
   51787 to 51807; server logs showed it ready. Restore then failed to connect to
   the old port. These are retained harness failures, not database recovery failures.
2. [Corrected core run](results/20261128T121322Z.json): rediscover the published port
   after restart; all ten core groups passed. No protocol expectations changed.
3. [Supplemental run](results/20261128T121444Z.json): fixed ADDENDUM.md before adding
   its probes; all twelve groups passed, including repeated core checks and search
   verification after recovery. This was additional development work, not an
   independent held-out confirmation.

## Implications

Keep waveform samples in MorletDB and small metrics in TimescaleDB. The bridge
needs canonical interval/calculation identity, exact time, conflict verification,
completion state and source-availability reporting. An idempotency key alone is
not sufficient. Metric grants and retention do not inherit recording policies.

For multi-tenant deployments choose and test an explicit isolation architecture:
RLS-protected rowstore, isolated tenant schemas/databases, or a reviewed aggregate
serving layer. Do not treat grouping by tenant as authorization. Timescale's RLS
combination restrictions must be documented, including the different enablement orders.

Coordinate refresh windows with raw retention: refreshing a deleted source range
can erase previously retained aggregates. Read-only dashboards should distinguish
raw available, derived pending, expired evidence and inaccessible evidence without
revealing hidden tenants. These extend existing #159, #197, #204 and #287 work.

## Limits

No changes to shipped SQL, codec, SDK or stored formats. No live Grafana rendering,
pgvector, historical migration, extension upgrade, replica failover, automatic job
scheduling, crash/power-loss durability, concurrent-load, long-soak, storage-ratio
or latency result. Restart was orderly; writer termination was a backend kill.
Logical restore used a second database in the same server and existing role;
cross-host roles/global objects and a nonempty retained aggregate remain untested.
Only 2 CPU / 1 GiB container settings were used; this does not establish capacity.

See [issue manifest](issues.json) for the published follow-up work.
