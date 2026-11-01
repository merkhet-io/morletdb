# E1 results: consolidated retrieval comparison on one frozen implementation

Protocol: [PROTOCOL.md](PROTOCOL.md) (frozen 34904f6; deviations 1–3 logged before the run).
Build: `main` 68594d7, library SHA-256 `3c786684…` (recorded in the JSON), pg_morlet 0.3.1, pgvector
0.8.0, PostgreSQL 17.11 with **default settings** (`shared_buffers` 128 MB, `work_mem` 4 MB,
`max_parallel_workers_per_gather` 2; EX-8 sets 8 and the planner used 3 workers; no asynchronous I/O in
PostgreSQL 17). Apple M5 Max, 18 cores, 128 GB; **no other CPU-intensive job** (load average 3.8–4.7
during the runs, mostly E1 itself); warm caches; one client. 100 queries per database, two rounds,
200 timings per arm. Results: `results/e1_dev.json`, `results/e1_heldout.json`.

**Verdict: H1 and H2 hold on both databases.** No arm returned fewer than 10 rows; round 2 returned
the same lists as round 1 for every arm and query; EX-2 and EX-8 returned identical lists.

## Main table (held-out, 234,622 windows)

| Arm | Recall@10 mean | min | < 0.9 | p50 / p95 / p99 (ms) | Work per query |
|---|---|---|---|---|---|
| EX-2, product exact, 2 workers | 1 | 1 | 0 | 958 / 971 / 1,016 | score all 234,622 |
| **EX-8**, product exact, 8 allowed (3 used) | 1 | 1 | 0 | 728 / **740** / 814 | score all |
| **PR**, product probe function | **0.977** | 0.7 | 5 | 39 / **57** / 64 | 13 HNSW scans (`ef_search` 100, 50 rows each), re-rank ≈ 350 |
| Z-650, zero-lag HNSW, same index | 0.639 | 0.1 | 69 | 29 / 41 / 52 | 1 scan (`ef_search` 650), re-rank 650 |
| Z-1000 | 0.658 | 0.1 | 67 | 34 / 50 / 57 | 1 scan (`ef_search` 1000), re-rank 1000 |

Development database (234,762 windows): EX-8 p95 783 ms; PR 0.998 (min 0.9, none below 0.9),
p95 53 ms; Z-650 0.717; Z-1000 0.744 (R2-D's 0.744 reproduced).

The product probe function was **13× faster than the strongest exact arm at p95** on held-out data
(57 vs 740 ms) and 15× on development data. Z-650 returns as many index rows as PR's 13 scans
together but with one traversal; equal candidate counts are not equal work.

## Error sources (offline controls, same stored envelopes, same re-ranking)

| Candidates | dev | held-out |
|---|---|---|
| all 61 lags, exact, 50 each (PX-61) | 1.000 | 1.000 |
| 13-lag grid, exact (PX-13) | 1.000 | 0.999 |
| 13-lag grid, probes rounded to float16, exact (PQ-13) | 1.000 | 0.999 |
| 7-lag grid, exact (PX-7) | 0.986 | 0.987 |
| product probes (HNSW) | 0.998 | 0.977 |
| zero-lag exact, same count as PX-13 (EZ) | 0.681 | 0.613 |

- **Grid sparsity** (spacing 5) cost at most 0.001; spacing 10 cost 0.013–0.014.
- **Probe quantization** to float16 cost nothing measurable (PQ-13 = PX-13).
- **Index approximation** cost 0.002 (dev) and 0.022 (held-out). All six hardest held-out queries
  (recall 0.7–0.9) are high-energy windows of station PB18; for each, PX-13 and PQ-13 reach 1.0.
- **Zero-lag candidates** recovered 0.99 of items whose best lag is within ±5, 0.57–0.84 at 6–15 and
  0.00–0.10 beyond 15; probes recovered 0.973–1.0 in every band.

**Grid check (deviation 1).** For each of the 1,000 reference items per database, the best of the 13
grid probes kept a median 0.999 of the item's best-lag correlation (5th percentile 0.978–0.981,
minimum 0.84–0.91), and ranked the item at median 2 (95th percentile 7–8) among all windows in that
probe's exact ordering; one held-out item ranked 324th, beyond the 50-row budget (the single PX-13 miss).
High kept correlation does not by itself guarantee rank: the rank is what decides recall.

## Storage and build

| Structure | dev | held-out |
|---|---|---|
| raw chunks (table and key) | 371 MB | 514 MB |
| envelopes (table and key) | 223 MB | 223 MB |
| probe table (table and key) | 223 MB | 223 MB |
| HNSW index (m 16, `ef_construction` 64) | 275 MB | 275 MB |
| probe structures / raw | 1.34× | 0.97× |
| HNSW rebuild, `maintenance_work_mem` 4 GB | 40.6 s | 38.4 s |

Both databases also contain an expression index on the envelopes left from study SS1 (262 MB); the
product does not use it, and it is excluded above. The probe index was rebuilt after all searches;
diagnostics D1 and D2 run on the rebuilt development index.

## Historic values

Earlier tables reported the BL1-C experiment arm (p95 131 ms, recall 0.976) against the harness's
parallel exact statement (713 ms), on an earlier implementation state and a machine shared with another
job. E1 supersedes them for the paper; they remain in `evaluation/bounded_lag/` unchanged.

## Reproduce

```sh
cargo pgrx install --release          # in a checkout of 68594d7, extensions/pg_morlet
.venv-seismic/bin/python -I evaluation/consolidated/e1.py morlet_r2 evaluation/bounded_lag/dev_queries.json \
    evaluation/consolidated/results/e1_dev.json <checkout>
.venv-seismic/bin/python -I evaluation/consolidated/e1.py morlet_bl1c evaluation/bounded_lag/confirm/results/queries.json \
    evaluation/consolidated/results/e1_heldout.json <checkout>
```

Each run takes about 8 minutes and needs the two evaluation databases (R2's and BL1-C's loads).

# Diagnostics D1 and D2 (development database only)

Protocol: [DIAGNOSTICS_PROTOCOL.md](DIAGNOSTICS_PROTOCOL.md) (frozen fa367ed). Same build and
settings as E1; run after E1 on the rebuilt development index; quiet machine (load average 2.2–3.9).

## D1: where probe-search time goes (100 queries × 2 passes)

| Stage (instrumented copy, `d1_profile.sql`) | p50 ms | share |
|---|---|---|
| read source interval | 1.2 | 3% |
| routing (eligible ids, bounded count) | 1.3 | 3% |
| query envelope | 0.2 | 1% |
| **13 index statements** | **25.5** (1.9 per probe) | **65%** |
| re-rank (materialized in a temporary table) | 9.9 | 26% |
| suppression | 0.5 | 1% |
| total | 38.9 | |

- The product function took p50 28.7 ms on the same queries, so instrumentation added about 13 ms,
  mostly by materializing the re-rank; the re-rank share above is therefore an upper bound, and the
  13 index statements account for most of the product's time. **Hypothesis (index statements > half):
  holds.**
- One probe statement executed in 0.79–0.82 ms under `EXPLAIN ANALYZE` (1,737 shared-buffer hits),
  against about 1.9 ms per probe inside the function, which issues each probe through `EXECUTE` and
  plans it every time. The `recording_id = ANY(eligible)` predicate cost about 0.03 ms. ANN traversal,
  heap fetches and visibility checks happen inside one index-scan node and were not separated.
- Candidates: every probe returned 50 rows; the union held 335 unique windows (p50; max 453); one
  window (the source) was excluded; suppression removed none; the loop read 11 rows to return 10.
  **No short results** (hypothesis holds).

## D2: filter–neighbourhood correlation at equal selectivity (synthetic predicate choice)

For each query, the day (≈ 7% of windows, 16.7–16.9 k) or station (25%) filter holding the most
(positive), fewest (negative) or a random share (neutral) of the query's unfiltered exact top 100.

| Filter | top-100 neighbours inside | recall mean / min | probe p50 / p95 ms | exact p95 ms |
|---|---|---|---|---|
| day, positive | 16.4 | 0.999 / 0.9 | 76 / 121 | 209 |
| day, neutral | 6.6 | 0.999 / 0.9 | 108 / 178 | 210 |
| day, negative | 1.5 | 1.000 / 1.0 | 146 / 227 | 210 |
| station, positive | 35.9 | 0.997 / 0.9 | 40 / 58 | 264 |
| station, neutral | 23.7 | 0.999 / 0.9 | 41 / 63 | 264 |
| station, negative | 14.5 | 1.000 / 1.0 | 49 / 63 | 264 |

- **Recall did not depend on correlation** (difference < 0.003); **hypothesis holds**.
- **Latency did:** with negatively correlated day filters, the index scans must pass more ineligible
  rows, and p95 rose 1.9× (just under the 2× bound). At this selectivity the probe path was no faster
  than exact search on this quiet machine (p95 178–227 vs 210 ms), although the router sends it to
  probes: its threshold (5,000 windows) was set where exact search reached 100 ms on a loaded machine.
  At 25% selectivity probes were 4–5× faster than exact search regardless of correlation.
- Decision: routing by eligible-window count keeps recall in the tested workload; its latency
  crossover depends on correlation and machine load and is documented as a limitation. Recalibrating
  the threshold is engineering work on development data, not done here.

## Reproduce

```sh
.venv-seismic/bin/python -I evaluation/consolidated/diag.py d1 morlet_r2 evaluation/bounded_lag/dev_queries.json evaluation/consolidated/results/d1_dev.json
.venv-seismic/bin/python -I evaluation/consolidated/diag.py d2 morlet_r2 evaluation/bounded_lag/dev_queries.json evaluation/consolidated/results/d2_dev.json
```
