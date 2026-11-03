# S1: per-chunk summaries — protocol (FROZEN)

Frozen 2026-11-03, before any S1 measurement. Development pilots on synthetic data (listed below) were run
while building the feature; they are not results. Deviations are appended below.

## Question

Interval statistics (`morlet_interval_stats`) decode every chunk of the interval: the time grows with the
number of samples, and intervals are limited to 16,777,216 samples. Per-chunk summaries store each chunk's
count, min, max and compensated sums when it is appended, so only the two chunks cut by the interval edges
are decoded. **With identical results, how much faster are statistics and overviews of long intervals, and
what do summaries cost at ingest and on disk?**

## The change under test (pg_morlet SQL 0.3.3, branch `feat/chunk-summaries`)

- `morlet_core::intervalstats`: `IntervalStats::merge`, and a 66-byte stored summary.
- **Opt-in:** `morlet_enable_chunk_summaries(collection)` adds a nullable `summary` column to the chunk table.
  This is a metadata-only change; the table is not rewritten.
  - Appends then store each chunk's summary in the same row.
  - `morlet_build_chunk_summaries(collection, recording, max_chunks)` covers chunks stored earlier.
- **One aggregate, `morlet_interval_stats_agg`, serves both functions:** `morlet_interval_stats` and the new
  `morlet_interval_overview(collection, recording, start, end, buckets)`.
  - Each chunk's part is accumulated on its own. The aggregate takes it from the stored summary when the chunk
    lies inside the interval, and decodes the chunk otherwise.
  - Parts are merged in start order. The results are therefore bitwise the same with and without summaries
    (pgrx SQL test `interval_tests.sql`, unit test in `intervalstats`).
- **Interval length:** with summaries enabled, intervals may be of any length; without them, the
  16,777,216-sample limit stays.

Build: the commit that freezes this protocol, installed with `cargo pgrx install --release`, on the local
PostgreSQL 17.11 (Apple M5 Max). The library SHA-256 is recorded in the results.

## Data

Statistics are a property of the stored samples; their speed depends on the sample count, the chunk count
and the codec path. Two archives, each stored twice in the same database cluster, **plain** (no summary
column) and **summarized**, so both configurations are measured on identical samples:

- **H (high rate):** one recording of 3,600 s at 64 kHz (230.4 M samples, 56,250 chunks of 4,096), loaded
  from real vibration data.
  - **Source:** channel `vibration_1` of B3's development set (`evaluation/tsdb_b3`, 124 Paderborn
    acquisitions of 4 s each).
  - **Assembly:** concatenated in development order, repeated as needed and cut at 230.4 M samples.
  - **Loading:** appended with `morlet_append_chunk_raw` in 4,096-sample chunks, with B3's declared
    quantum, 100/32768.
  - **Collections:** in database `morlet_s1`, `h_plain` and `h_sum`. `h_sum` has summaries enabled
    before loading, so its summaries come from the append path.
- **L (low rate, long):** E4's N1 gallery database `morlet_r2` (CX seismic, 100 Hz, encoded chunks; 48
  recordings of one day, 8.64 M samples).
  - **Copies:** two template copies, `morlet_s1_lp` (plain) and `morlet_s1_ls`.
  - **Summaries:** enabled on `morlet_s1_ls` and built with `morlet_build_chunk_summaries`, so its summaries
    come from the builder.

No held-out data is needed: the feature has no fitted parameters, and the gate is bitwise identity.

## Correctness gate (before timing)

1. **Identity:** for 300 intervals per archive, `morlet_interval_stats` and `morlet_interval_overview`
   (buckets 1, 10, 100, 1,000) must give bitwise identical results on the plain and summarized copies.
   - The intervals are 100 per length class: short (1 to 4,096 samples), medium (to 1 M) and long (to the
     smaller of 16,777,216 and the recording).
   - Each interval has a random start and end, drawn with seed 20261103.
2. **Against the samples, beyond the old limit:** for 20 intervals of H longer than 16,777,216 samples,
   the summarized statistics are checked against numpy over the same samples (the loaded array).
   - min and max must be equal.
   - count must be exact.
   - mean and RMS must be within 1e-12 relative.
3. **Summaries are consistent:** every stored summary in `h_sum` and `morlet_s1_ls` equals
   `morlet_encoded_chunk_summary(encoded)`.

A failure stops the timing until explained.

## Timing

- **Intervals:**
  - H: 1 s, 10 s, 60 s, 10 min and 3,600 s. Without summaries, 10 min and 3,600 s are not possible
    (over 16,777,216 samples). Instead, plain is timed at the limit itself, 16,777,216 samples (about
    262 s), next to summarized at the same length.
  - L: 1 h, 6 h and 24 h, at 100 Hz.
- **Rounds:** for each length, 20 random starts, each timed 5 times after one warm-up, plain and summarized
  alternating.
- **Overview:** `morlet_interval_overview` with 1,000 buckets over the full H recording (summarized only)
  and over 16,777,216 samples (both copies).
- **Ingest cost:** H's first 600 s are loaded 5 times per configuration, alternating, into a fresh
  collection each time. One connection, pipelined, as in B3.2.
- **Storage:** `pg_total_relation_size` of `h_plain_chunks` and `h_sum_chunks`.
- **Builder:** the time to build `morlet_s1_ls`'s summaries (117,554 chunks), and `h_plain`'s in
  batches of 100,000 at the end.

Each run uses AC power, `caffeinate`, and no other study; it starts when the 5-minute load is below 5.

## Decision rules

1. **Gate:** all three checks pass, or summaries do not ship.
2. **Speed (primary):** speed-up = p95(plain) / p95(summarized) at H 60 s.
   - **"Summaries faster"** if it is at least 10×.
   - For lengths beyond the old limit, report the time; "without" is not possible there.
3. **Ingest cost:** median throughput change.
   - **≤ 10% slower:** the Python SDK enables summaries in `create_collection` and the docs recommend them.
   - **More than 10%:** they stay opt-in only, and the docs state the cost.
4. **Storage:** the chunk table's growth, reported.

## Budget and stop

About an hour of loading and measurement and a few GB of disk in a scratch database, dropped afterwards.
Stop after S1.

## Development pilots (before freezing; synthetic grid data, not results)

| Measure | Without | With |
|---|---|---|
| Ingest, one connection (60 s at 64 kHz, 5 rounds, median) | 2.97 M samples/s | 2.82 M samples/s (−5%) |
| Statistics of 60 s at 64 kHz | 84 ms | 1.0 ms |
| Statistics of 10 min at 64 kHz | 361 ms (16.7 M samples, the limit) | 3.1 ms (38.4 M samples) |
| Statistics of 1 h at 64 kHz (230 M samples) | not possible | 14 ms |
| Overview of 1 h, 1,000 buckets | — | 16 ms |
| Chunk table size, 1 h | 264.5 MB | 268.3 MB (+1.4%) |

An earlier design, with summaries in a separate table with a foreign key, cost 12% at ingest. It was
replaced by the column before freezing.

## Deviations

1. **The load condition was applied to the timed steps only.** `run_all.sh` waited for the 5-minute load to
   fall below 5 before `time` and `ingest`, as the decision rules need. It did not wait before `load`,
   `gate` and `finish`.
   - `gate` is not timed.
   - `load` and `finish` record the builder's speed and the loading speed of the full archives. These are
     reported as descriptive and enter no decision rule.
   - The 5-minute load was 4.0–5.0 during `time` and between 4.5 and 5.3 around the run.
