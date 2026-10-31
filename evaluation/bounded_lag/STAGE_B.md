# BL1 Stage B: keeping shifted-probe search usable during ingestion

Status: **FROZEN** in the commit that adds this file, before any Stage B measurement.
Development data only (R2's recordings). Amendments are appended and committed first.

## Question

Stage A measured P-G13-50 on a static gallery. When recordings are appended continuously,
can the envelopes, the `halfvec` side table and the inner-product HNSW index be maintained
so that (a) appends stay fast, (b) new windows become searchable quickly, and (c) probe
search keeps its recall and latency against the same visible snapshot?

## What exists, and what is maintained

- The raw append contract is unchanged: `morlet_append_chunk` at global offsets; identical
  replays are no-ops; gaps, overlaps and changed retries fail. Nothing out of order is
  accepted silently.
- Each envelope depends only on its own complete 4096-sample window, read back from raw
  chunks (`morlet_build_envelopes`: z-score, |Hilbert| by full FFT, 50-sample mean, every
  10th value, z-score). There is no incremental shortcut and none is needed: per-recording
  extraction state is the raw tail not yet covered by a complete window (< 4096 + 2048
  samples), already persisted as raw chunks.
- Maintenance step for one recording: `morlet_build_envelopes(.., 4096, 2048, B)` (resumes
  after the last stored window), then insert the new windows' `halfvec` rows; pgvector
  inserts them into the HNSW index in the same transaction.

**Progress, defined separately:**
- *raw progress:* the recording's checkpoint end (committed samples);
- *representation progress:* the last stored envelope window;
- *index visibility:* rows in the side table (same transaction as the envelope, so equal to
  representation progress here).
"Exactly once" is not claimed: idempotent replay plus primary keys give at-most-one row per
window; completeness is checked by counting.

## Modes

- **S (synchronous):** each append transaction also runs the maintenance step for its
  recording. Visibility = the append's commit.
- **W (background worker):** appends write raw only. One worker process loops over
  recordings with backlog, runs the maintenance step with B = 64 windows per transaction,
  commits, and sleeps 50 ms when idle. Its checkpoint is the stored envelope table.

## Replay

New database `morlet_bl1s`, same layout as R2 (collection `r2`, side table, inner-product
HNSW index created empty, m 16, ef_construction 64). Source: R2's day-1 recordings of the
four stations (34.5 M samples, ~16.9k windows), 4096-sample chunks, one appender thread per
station.

| Run | Mode | Pace |
|---|---|---|
| S-max, W-max | S, W | unpaced (as fast as appends commit) |
| S-paced, W-paced | S, W | 20,000 samples/s per station (200× real time) |

Each run starts from empty tables. During the paced runs a query thread issues one search
every 250 ms against a random already-visible window: P-G13-50 and, in the same
`REPEATABLE READ` snapshot, the exact parallel SQL scan (custom plan, up to 8 workers).

## Measures

- **Durable raw-append throughput** (samples/s committed) — max runs; S-max against W-max
  shows the cost of synchronous maintenance.
- **Delays per window** (paced runs): *completion delay* = commit of the chunk that completes
  the window − the paced arrival time of its last sample (inherent chunking + append);
  *processing delay* = time it becomes visible − that commit (0 in S by construction).
- **Query p50/p95 during ingestion**, P and exact; **recall@10** of P against exact on the
  same snapshot.
- **Backlog** (W): completed-but-not-visible windows, sampled every 0.5 s; peak and drain
  time after appends stop.
- **Memory:** worker peak RSS at mid-run and at the end (must not grow with recording
  length beyond noise).
- **Bytes:** WAL generated per run; final sizes of raw, envelopes, side table, index.

## Drills (W mode, after W-paced)

1. **Duplicate replay:** re-append an already committed chunk; counts unchanged.
2. **Restart:** kill the worker during W-max, restart it; at the end every completed window
   has exactly one envelope and one side row.
3. **Backpressure:** W-max itself (backlog growth and drain).
4. **Gap:** append a chunk that skips samples; must fail with no state change. Gapped or
   late input belongs in a staging layer (collector spool), not in the archive.

## Decision (pre-registered)

**Usable during ingestion** if, in at least one mode's paced run:
1. P recall@10 against the same-snapshot exact search ≥ 0.95 (mean);
2. P p95 during ingestion < exact p95 during ingestion;
3. processing delay p95 ≤ 5 s (W) — S is 0 by construction;
4. and, for S only: S-max throughput ≥ 50% of W-max throughput.
The recommended mode is the one satisfying all, preferring S if both do. Otherwise
**NOT USABLE AS IS**, with the measurements.

## Not in Stage B

No change to pg_morlet, no Kafka/RisingWave/distributed parts, no tuning after results, no
deletes or index rebuilds, one machine.
