# Operating MorletDB in a pilot

A short guide for running pg_morlet in a bounded pilot: people review search results; the
system makes no automatic decisions. Numbers come from pilot Stage 2
(`evaluation/pilot/`) on one Apple M5 Max under a concurrent unrelated workload (load
average 3–20); treat latencies as indicative until re-measured on the pilot's own hardware.

## Tested operating range

| Dimension | Tested | Notes |
|---|---|---|
| Ingest | 16 channels × 25.6 kHz (409,600 samples/s), 4 writer connections, envelopes and probe rows built in the append transaction | sustained for 15 min with 4 concurrent analysts; peak lag 22 s of signal, recovered to 0.1 s. Capacity about 500–570 k samples/s (1.3–1.4×); at 2× the writers fell behind without recovering. No multi-hour run yet |
| Archive | about 960,000 windows (4096 / 2048) in one collection, 4.8 GB database | larger archives are untested |
| Search | 4 analysts, one query every 2 s, half filtered (exact path), half unfiltered (probe path), plus one unfiltered exact search per minute | filtered p95 167 ms; probe p95 337 ms, recall 0.998; unfiltered exact p95 9.7 s |
| Recovery | backup/restore, aborted appends/builds, worker kills, deletions, online index rebuild, failed upgrade | all checks passed (see below) |

Outside this range — higher rates, larger archives, cold caches, many tenants under load,
other machines — nothing is claimed.

## Installation

```sh
cd extensions/pg_morlet
cargo pgrx install --release --pg-config /path/to/pg_config     # PostgreSQL 17
```

```sql
CREATE EXTENSION pg_morlet;
CREATE EXTENSION vector;          -- optional: only for probe search (pgvector 0.8 or later)
```

pg_morlet does not depend on pgvector; CI tests both with and without it.

## Configuration

| Setting | Recommendation | Why (measured) |
|---|---|---|
| `maintenance_work_mem` | 2–4 GB for the session that runs `morlet_enable_envelope_probes` or rebuilds the probe index | with 64 MB the HNSW build for 235 k windows took ~25 min instead of ~1 min; 939 k windows took 4.6 min with 4 GB |
| `max_parallel_workers_per_gather` | default (2) or higher | exact search runs as one parallel statement |
| `max_wal_size`, `checkpoint_timeout`, `wal_compression` | raise `max_wal_size` well above the 1 GB default and enable `wal_compression` (to be confirmed by measurement) | sustained ingest with probe maintenance wrote 13.8 GB of WAL in 15 min for 0.76 GB of database growth (default `max_wal_size` 1 GB): HNSW updates touch pages across the index and every checkpoint forces full-page images |
| WAL archiving / disk | size WAL storage for ~1 TB/day at the tested ingest rate unless the settings above reduce it | as above |

## Ingest and maintenance

- Call `morlet_build_envelopes` in the append transaction to make windows searchable at
  commit (see the [search contract](search-contract.md)). It builds in batches of up to 256
  windows, each raw chunk decoded once.
- An **external worker** that writes envelopes directly (CW1) does not write probe rows:
  `morlet_collection_status` then shows a `probe_backlog`. Fill it with the same statement
  `morlet_build_envelopes` uses, or rebuild probe rows for the affected recordings.
- **After deleting recordings**, prefer `REINDEX INDEX CONCURRENTLY` on the probe index to
  `VACUUM` of the probe table: on 53 k windows the vacuum took 113 s and left the index size
  unchanged; an online rebuild took 10 s (index storage doubles during the rebuild).

## Diagnostics

```sql
SELECT * FROM morlet_collection_status('signals');
```

One row per window size: recordings, raw samples, stored envelopes, recordings without
envelopes, raw samples not yet covered by an envelope (`unbuilt_samples`, includes partial
tails), probe rows and `probe_backlog`, whether row security is applied, and bytes per
structure. Healthy steady state: `probe_backlog = 0` and `unbuilt_samples` bounded by about one
stride per recording plus the current ingest delay.

Audit envelopes written by an external process against the raw data with the query in
[access control](access-control.md#the-worker-write-path).

## Backup and restore

| Option | What is dumped | Measured (50,604 windows, 104 M samples) |
|---|---|---|
| Full | everything (`pg_dump -Fc`) | dump 3 s, 459 MB; restore 18 s |
| Raw only | the recordings table, chunks, segments (`pg_dump -Fc -t <c> -t <c>_chunks -t <c>_segments`, plus context and label tables if used) | dump 1.4 s, 146 MB; restore 3 s, then rebuild envelopes 12.5 s and the probe index 10.6 s |

Both restores were byte-identical in raw chunks and envelopes and returned identical exact
search results; probe recall stayed 1.0. Envelopes, probe tables and indexes are derived and
can always be rebuilt; recordings, chunks, context and labels cannot. Before a raw-only
restore, create the extensions in the target database.

## Failure behaviour (tested)

- An append transaction aborted mid-statement or before `COMMIT` (20 trials) leaves no partial
  chunk, envelope or probe row; the checkpoint equals the stored samples; replaying the same
  chunks succeeds.
- An aborted `morlet_build_envelopes` leaves nothing from that call and resumes on the next
  call; an aborted `morlet_enable_envelope_probes` leaves no probe table and can be retried.
- Killed or duplicated external workers and a concurrent in-database builder still produce
  exactly one byte-identical envelope per window.
- `REINDEX INDEX CONCURRENTLY` on the probe index during 4 concurrent searches: 10 s, no
  errors, search p95 23 → 31 ms while it ran.

## Upgrades

`ALTER EXTENSION pg_morlet UPDATE` is transactional. CI tests updates from 0.1.0-alpha.3,
0.2.0 and 0.3.0 with populated data (identical reads and searches; catalog equal to a fresh
install), and that a **failed** update rolls back completely and leaves the old version
working. Take a backup before updating anyway.

## Known limits

- **Not yet pilot-ready** by the Stage 2 plan's own criteria: the 2-hour stability run and the
  WAL settings are still open (`evaluation/pilot/RESULTS.md`).

- Timings are from one shared machine; cold-cache behaviour is not measured.
- Unfiltered exact search over ~1 M windows takes several seconds (p95 9.7 s here); use probe
  search or filters for interactive work.
- Probe search is approximate (recall 0.977–1.0 in the evaluations); returned distances are
  exact.
- Row-level security for multi-tenant use: see [access control](access-control.md); policy
  cost is untested with thousands of visible recordings.
- WAL volume under sustained ingest with probe maintenance is high with default checkpoint
  settings (above).
