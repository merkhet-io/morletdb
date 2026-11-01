# Pilot Stage 2: load, recovery and operations — test plan

Status: **FROZEN** in the commit that adds this file, before any Stage 2 run. Deviations are
appended at the end and committed before the run they affect.

## Pilot operating range under test (defaults; to be confirmed or replaced by the owner)

| Dimension | Target |
|---|---|
| Ingest | 16 channels × 25.6 kHz = 409,600 samples/s sustained (vibration-monitoring scale); 4,096-sample chunks; envelopes 4,096 / stride 2,048 (200 windows/s) built **in the append transaction** (synchronous mode, Stage B's recommendation) |
| Archive per collection | up to about **1,000,000 windows** (4× the largest gallery measured so far) |
| Search | 4 concurrent analysts, one query every 2 s each: half filtered to one recording group (≤ 2% of windows, exact path), half unfiltered (probe path); plus one unfiltered exact search per minute |
| Access | single tenant; row-level security tested separately in Stage 1 |
| Human use | results are reviewed by people; no automatic decisions |

Anything outside this range (higher rates, archives beyond 1 M windows, cold caches,
concurrent tenants under load) is **not tested** and stays outside the pilot's stated range.

## Environment and its limits

- One Apple M5 Max (18 cores, 128 GB), macOS, PostgreSQL 17.11 (pgrx build), pgvector 0.8.0,
  `main` at e31e1a3.
- **Another CPU-intensive job runs on this machine throughout** (load average about 2–7). Every
  timed run records the machine's load average and the CPU time of non-PostgreSQL processes.
  Correctness results are unaffected; latency and throughput are reported as indicative and
  compared only within a run. The final pilot numbers should be re-measured on quiet or
  dedicated hardware before they are quoted as limits.
- Cold-cache measurements need root (OS page cache) and are **not** made.
- Data: CX seismic counts already on disk (2025 R2 slice and 2021 BL1-C slice, 0.96 G samples),
  replayed as 25.6 kHz channels. To reach about 1 M windows the second pass replays each segment
  time-reversed (so windows differ; a sign flip would give identical envelopes). Seismic counts
  compress better than typical vibration data (about 6–9 vs 9–12 bits/sample), so byte
  figures are optimistic for vibration.

## Part A — operations surface (built first; the tests read it)

`morlet_collection_status(collection)`: one row per window size with recordings, raw samples,
complete windows on the stride grid, stored envelopes, envelope backlog, probe rows and probe
backlog (if enabled), whether row security is applied, and bytes per structure (recordings,
chunks, segments, envelopes, probe table, probe index). Tested for correctness on a fixture.

## Part B — recovery and lifecycle (isolated database `morlet_pilot_rec`, ~50 k windows)

Each test states its expected end state first and checks: every recording's samples hash
identical to before (SHA-256 over `morlet_read_interval` of the whole recording), every
complete window has exactly one envelope and one probe row, and 20 searches return the same
lists as before (exact) or recall ≥ 0.95 (probe).

| Test | Action | Expected |
|---|---|---|
| B1 backup / restore | `pg_dump -Fc` of the whole database → restore into a fresh database; separately, dump of recordings + chunks only → restore → rebuild envelopes, enable probes | Both restores pass the checks. Report dump/restore/rebuild time and sizes; state which structures must be backed up (raw, recordings, labels, context) and which can be rebuilt (envelopes, probe table, index) |
| B2 abort during append | terminate the backend (`pg_terminate_backend`) inside an append + build transaction, 20 times at random points | No partial chunk, envelope or probe row; checkpoint equals stored samples; replaying the aborted chunk succeeds |
| B3 abort during envelope build / probe enable | terminate during a large `morlet_build_envelopes` and during `morlet_enable_envelope_probes` | Build resumes and completes; enable leaves no table on abort and succeeds on retry |
| B4 worker abort and duplicates | external worker (CW1) killed mid-run; two workers on the same recordings; batched in-database builder concurrently with a worker | Exactly one row per window; bytes identical |
| B5 deletion and index maintenance | delete 20% of recordings; `VACUUM`; search | Cascades remove chunks, envelopes and probe rows; no deleted recording appears in results; probe recall vs exact ≥ 0.95; report vacuum time and index size before/after |
| B6 index rebuild under load | `REINDEX INDEX CONCURRENTLY` on the probe index while 4 clients search | Searches keep working (latency reported); report rebuild duration, lock waits and temporary size |
| B7 upgrade and failed upgrade | 0.3.0 → 0.3.1 with populated data (existing scripts); then an update forced to fail midway (a conflicting object created before `ALTER EXTENSION UPDATE`) | Successful path passes the existing checks; the failed update rolls back completely, the extension stays at 0.3.0 and data reads back identical; after removing the conflict the update succeeds |

## Part C — load

- **C1 bulk fill:** load the archive to about 900 k–1 M windows (4 sessions, batched builder,
  probe index enabled after the fill with `maintenance_work_mem = 2GB`). Throughput and sizes
  only.
- **C2 fault-finding run (15 min):** target ingest + target query load on top of the filled
  archive. Abort on any of the criteria below.
- **C3 overload (10 min at 2× target, then 10 min at 1×):** does append keep up; if not, how far
  does ingest fall behind real time and does it recover.
- **C4 stability run (2 h)** at target, only if C2 and C3 pass.

**Measured:** ingest lag (stream time vs wall time), append-transaction latency p50/p95/max,
search latency per class p50/p95/p99, probe recall spot check (every 10th probe query is also
run exactly in the same snapshot), PostgreSQL CPU, backend memory peak, WAL bytes/s, database
size growth, errors, machine load average.

**Abort criteria (any):** an append error; ingest lag above 60 s and still growing after 5 min;
probe spot-check recall mean below 0.9 over 50 queries; search p95 above 10 s for 5 min; free disk
below 200 GB; database above 60 GB.

## Budget

At most about 4 hours of measurement wall time in total (C4 is 2 h of it), at most 60 GB of
disk, isolated databases only (`morlet_pilot_*`), no changes to existing databases.

## Deliverables

Results per part with reproducible commands; `docs/operations.md` (installation,
configuration, resource limits, backup/restore, upgrade, diagnostics, known limits); a stated
pilot operating range consisting only of what passed; a verdict per part: continue, narrow,
or stop.

## Deviations

1. **Recovery harness setup (before any B result):** the first run stopped because the installed
   build lacked `morlet_collection_status` (#74, not yet merged); #74's build was installed and
   the run repeated from the start.
2. **B5 deleted 2 of 13 recordings (15%),** not 20%: the selection rule (every fifth recording,
   excluding the abort-test recording) gave 2.
3. **Load harness `ps` parsing (before any C2 measurement):** process names with spaces broke
   the CPU parser; writers and clients started for a few seconds and were stopped. Their few
   seconds of live data stay in the live recordings, and the next run resumes from the stored
   checkpoints.

## Amendment 1 — pilot configuration (after C3, before C2p)

C2 wrote 13.8 GB of WAL for 0.76 GB of growth with the server's defaults: `shared_buffers`
128 MB (the probe index alone is 1.2 GB), `max_wal_size` 1 GB, `checkpoint_timeout` 5 min,
`wal_compression` off. **C2p** repeats C2 unchanged (15 min, target rate, same clients) with a
pilot configuration: `shared_buffers` 8 GB, `max_wal_size` 16 GB, `checkpoint_timeout` 15 min,
`wal_compression` on (pglz; this build has no lz4/zstd). The four settings change together,
so C2p shows whether a sensible configuration makes WAL manageable, not which setting matters.
**C4** (2 h at the target rate, same configuration) runs if C2p passes C2's criteria. Criteria
and abort rules are unchanged. The server's defaults are restored afterwards.
