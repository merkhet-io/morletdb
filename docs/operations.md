# Operating MorletDB in a pilot

A short guide for running pg_morlet in a bounded pilot: people review search results; the
system makes no automatic decisions. Numbers come from pilot Stage 2
(`evaluation/pilot/`) on one Apple M5 Max under a concurrent unrelated workload (load
average 3–20); treat latencies as indicative until re-measured on the pilot's own hardware.

## Tested operating range

| Dimension | Tested | Notes |
|---|---|---|
| Ingest | 16 channels × 25.6 kHz (409,600 samples/s), 4 writer connections, envelopes and probe rows built in the append transaction | held for 15 min with default settings (peak lag 22 s) and for 111 min with the configuration below (lag ≤ 24 s, ≤ 3.4 s after the first hour), with 4 concurrent analysts; the planned 2-hour run stopped at 112.5 min after an unexplained pause of all client processes. Capacity about 500–570 k samples/s (1.3–1.4×); at 2× the writers fell behind without recovering |
| Archive | about 960,000 windows (4096 / 2048) in one collection, 4.8 GB database | larger archives are untested |
| Search | 4 analysts, one query every 2 s, half filtered (exact path), half unfiltered (probe path), plus one unfiltered exact search per minute defaults: filtered p95 167 ms, probe p95 337 ms (recall 0.998), exact p95 9.7 s; configuration below, 111 min: filtered p95 118 ms, probe p95 138 ms (recall 0.993), exact p95 14.5 s on a growing archive |
| Recovery | backup/restore, aborted appends/builds, worker kills, deletions, online index rebuild, failed upgrade | all checks passed (see below) |

Outside this range — higher rates, larger archives, cold caches, many tenants under load,
other machines — nothing is claimed.

## Installation

```sh
cd extensions/pg_morlet
cargo pgrx install --release --pg-config /path/to/pg_config     # PostgreSQL 17, 18 or 19
```

```sql
CREATE EXTENSION pg_morlet;
CREATE EXTENSION vector;          -- optional: only for probe search (pgvector 0.8 or later)
```

pg_morlet does not depend on pgvector; CI tests both with and without it, on PostgreSQL 17, 18 and 19
([compatibility](compatibility.md); PostgreSQL 18 needs pgvector 0.8.1 or later; 19 is tested with 0.8.7). The measurements on
this page are from PostgreSQL 17.

## Configuration

| Setting | Recommendation | Why (measured) |
|---|---|---|
| `maintenance_work_mem` | 2–4 GB for the session that runs `morlet_enable_envelope_probes` or rebuilds the probe index | with 64 MB the HNSW build for 235 k windows took ~25 min instead of ~1 min; 939 k windows took 4.6 min with 4 GB |
| `max_parallel_workers_per_gather` | default (2) or higher | exact search runs as one parallel statement |
| `shared_buffers`, `max_wal_size`, `checkpoint_timeout`, `wal_compression` | tested together: 8 GB, 16 GB, 15 min, on (pglz) | with defaults, 15 min of target ingest wrote 13.8 GB of WAL for 0.76 GB of growth; with these four settings, 1.91 GB for 0.65 GB (15 min) and 11.96 GB for 4.68 GB (111 min). They were changed together, so no single setting is credited |
| WAL archiving / disk | with the settings above, about 6.5 GB of WAL per hour at the tested rate (about 2.6× the database growth); with defaults, about 55 GB per hour | as above |

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

### Commit notifications (SQL 0.3.6)

Consumers such as dashboards, stream processors and agent hosts can wait for new data with PostgreSQL's
`LISTEN` instead of polling (#205):

```sql
SELECT morlet_enable_notifications('line');                 -- channel morlet_line; returns the channel
LISTEN morlet_line;                                         -- in the consumer's session
```

- **Delivery:** every committed transaction that stored new chunks in the collection sends exactly one
  notification, after its samples are readable.
  - Rolled-back transactions, rolled-back savepoints, identical replays and transactions without new chunks send
    nothing.
  - Several appends and recordings in one transaction are coalesced into one notification.
- **Payload** (JSON, version 1):
  `{"v":1,"collection":"public.line","recordings":[{"recording_id":"p1","first_sample":0,"end_sample":32768,"sample_rate_hz":1000,"start_s":0,"end_s":32.768}]}`.
  - Each range is half-open, in samples, and in seconds from the recording's first sample.
  - A list that would not fit PostgreSQL's 8000-byte limit becomes `"recording_count": n, "truncated": true`.
- **Privacy:** any role that can connect may `LISTEN` on any channel.
  - `morlet_enable_notifications(collection, detail => false)` sends only `{"v":1,"collection":...,"recording_count":n}`.
  - Row security requires `detail => false`, and detail is dropped automatically if row security is enabled later.
- **Not a queue:** notifications are not stored, so commits made while no consumer listens are not replayed. A
  consumer that starts or reconnects catches up with `morlet_latest` and then listens.
- **Cost:** a statement trigger and a deferred row trigger on the chunk table. Appends write one chunk row per
  call, so the overhead is one small JSON update per statement and one cheap check per row at commit.
- **Other switches:**
  - `morlet_enable_notifications(collection, channel)` changes the channel.
  - `morlet_disable_notifications(collection)` turns notifications off.
  - `morlet_notification_channel(collection)` shows the current setting.
  - Python SDK: `enable_notifications`, and `notifications(collection, timeout=, stop_after=)` on an autocommit
    connection.
- **Tests:** `commit_notifications_collect_ranges_once_per_transaction` (ranges, switches, refusals) and
  `tests/notifications.sh` (a real listener session: one per commit, none for rollbacks, savepoints, replays).

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

### Integrity scrub (SQL 0.3.5)

```sql
SELECT * FROM morlet_verify('signals');                    -- first 100 recordings
SELECT * FROM morlet_verify('signals', 'last-id-seen', 100); -- the next batch
```

One row per recording: chunks and samples read, and every problem found, without changing anything.
It decodes every chunk (structure and CRC-32C) and checks it against its `sample_count`, compares
chunk summaries (where enabled) with the decoded samples, and checks that chunks cover the recording
from sample 0 to its checkpoint without gaps or overlaps. A bad chunk is reported, not raised, so one
scrub lists every problem (at most 100 per recording, then their number). Run it as a role that sees
every row: rows hidden by row security show up as gaps. The command-line wrapper runs it in batches,
one read-only transaction each, with an optional pause, and exits with status 1 if anything was found:

```sh
python3 tools/verify/morlet_verify.py --dsn "$DATABASE_URL" public.signals --batch 50 --sleep 0.2
```

Two layers protect the data. PostgreSQL's data checksums (`initdb --data-checksums`, or
`pg_checksums --enable` on a stopped cluster) detect corruption of any page when it is read, for
every table and index. MorletDB's own checks know what a chunk means: every chunk carries a CRC-32C
over its encoded samples, every read verifies it, and the scrub reads everything on purpose, including
data nobody has read for months, and also finds logical problems such as a missing chunk or a summary
that no longer matches. Enable both. Envelopes, probe rows and context segments are derived data and
are not checked by the scrub yet; they can always be rebuilt from the chunks.

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

- **Not yet pilot-ready** by the Stage 2 plan's own criteria: the 2-hour stability run stopped at
  112.5 minutes after an unexplained pause (`evaluation/pilot/RESULTS.md`). A measured stability
  duration is not a production release.

- Timings are from one shared machine; cold-cache behaviour is not measured.
- Unfiltered exact search over ~1 M windows takes several seconds (p95 9.7 s here); use probe
  search or filters for interactive work.
- Probe search is approximate (recall 0.977–1.0 in the evaluations); returned distances are
  exact.
- Row-level security for multi-tenant use: see [access control](access-control.md); policy
  cost is untested with thousands of visible recordings.
- WAL volume under sustained ingest with probe maintenance is high with default checkpoint
  settings (above).
- Enable probe search (`morlet_enable_envelope_probes`) while no envelopes are being built: a build
  that starts before the enable commits writes envelopes without probe rows, which then show as
  `probe_backlog` in `morlet_collection_status` and are invisible to probe search.
- Probe search needs pgvector 0.8 or later (`hnsw.iterative_scan`).
