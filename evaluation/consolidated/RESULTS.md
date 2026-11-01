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
