# E1: consolidated retrieval comparison on one frozen implementation — protocol

Status: **FROZEN** in the commit that adds this file, before any E1 measurement. Deviations are
appended at the end and committed before the run they affect. Plan: `research/paper/NEXT_EXPERIMENTS.md`.

## Question and hypotheses

Earlier retrieval numbers come from successive implementation states (BL1, BL1-C, SS1, SS3, product
checks). E1 measures all main arms **on one build, one configuration, the same queries, in the same
session**, and becomes the source of the paper's main retrieval tables.

- **H1 (benefit):** the product probe function reaches mean recall@10 ≥ 0.95 against the product exact
  function, at p95 ≤ 1/3 of the p95 of the strongest exact arm, unfiltered, on both databases.
- **H2 (mechanism):** on both databases, recall orders as exact-candidate probes (PX-13) ≥ product
  probes ≥ zero-lag HNSW at both budgets, with the zero-lag arms at least 0.2 below product probes.

Both are reported as pass/fail; a failure is reported, not tuned away.

## Frozen implementation and environment

- **Code:** `main` at 68594d7 (pg_morlet SQL 0.3.1, PRs up to #75), built with
  `cargo pgrx install --release` against PostgreSQL 17.11 (pgrx); the harness records the git commit,
  the SHA-256 of the installed `pg_morlet.dylib` and the extension versions (pg_morlet, vector 0.8.0).
- **PostgreSQL configuration:** server defaults, restored with `ALTER SYSTEM RESET ALL` and a restart
  after the pilot run C4 (which used a pilot configuration). The harness records every setting whose
  source is not `default`.
- **Hardware:** Apple M5 Max, 18 cores, 128 GB, macOS; one client over localhost. **Another
  CPU-intensive, unrelated job runs on the machine throughout**; the harness records the load average
  at the start and end of every round. No resource limits are set.
- **Caches:** warm. Before timing, each arm runs once on the first five queries (untimed). Cold-cache
  measurements need root and are not made.

## Data

| Database | Role | Gallery | Queries |
|---|---|---|---|
| `morlet_r2` | development (R2, BL1) | CX HHZ, PB01, PB02, PB06, PSGCX, 1–14 March 2025; 234,762 windows of 4096 at stride 2048 | `evaluation/bounded_lag/dev_queries.json` (50 random, 50 high-energy) |
| `morlet_bl1c` | held-out (BL1-C), **consumed** | PB05, PB11, PB14, PB18, 1–14 March 2021; 234,622 windows | `evaluation/bounded_lag/confirm/results/queries.json` |

The held-out database was used by BL1-C and SS3. E1 **re-measures** it with configurations frozen
before BL1-C; nothing is chosen on it. Collection `public.r2`, window 4096, metadata filter `{}`
(every window eligible; the router therefore takes the probe path).

## Search contract and parameters (all arms)

Top 10; `max_lag_fraction` 0.075 (L = 30); source interval excluded; overlap suppression on; order
`(distance, recording_id, start_sample)`. **Reference:** the product exact function's result
(`morlet_search_envelope_interval`, `max_candidates` 1,000,000). Recall@10 = |arm ∩ reference| / 10.

## Arms

Timed (wall time per call in the client, including reading the query interval and computing its
envelope):

| Arm | What | Work per query |
|---|---|---|
| EX-2 | product exact function, `max_parallel_workers_per_gather` 2 (default) | score every eligible window |
| EX-8 | product exact function, `max_parallel_workers_per_gather` 8 | same, more workers |
| PR | product probe function, defaults: 13 probes (step 5), 50 per probe, `ef_search` 100, iterative scan relaxed, threshold 5,000 | 13 HNSW scans, re-rank of the union (≤ 650) |
| Z-650 | zero-lag probe (lag 0, float16) on the same index, `ef_search` 650, LIMIT 650; re-rank with the product scorer and suppression | 1 HNSW scan returning as many rows as PR's 13 scans together |
| Z-1000 | as Z-650 with 1000 (R2-D's budget) | 1 HNSW scan, re-rank of 1000 |

The strongest exact arm is the one of EX-2 and EX-8 with the lower p95. Equal candidate counts are
not equal work: the report gives index scans, `ef_search`, rows returned and windows re-ranked per arm.

Offline controls (not timed; candidates computed in the client from the stored float16 envelopes,
re-ranked by the product scorer in the database, same suppression):

| Control | Candidates |
|---|---|
| PX-61 | exact top 50 for each of the 61 lags, full-precision query |
| PX-13 | exact top 50 for the 13 grid lags, full-precision query |
| PX-7 | exact top 50 for the 7 lags at spacing 10 |
| PQ-13 | as PX-13, but each probe rounded to float16 first (isolates probe quantization from HNSW) |
| EZ | exact zero-lag inner-product top n, n = size of the PX-13 union |

## Procedure

1. Record environment; check plans: one EXPLAIN of the Z statement (must be an index scan on the probe
   index); for PR and EX, the per-call change of `idx_scan` on the probe index (PR: 13; EX: 0) and,
   with `auto_explain` on nested statements for one call, whether the exact statement ran a parallel
   sequential scan.
2. Warm-up (untimed).
3. **Two rounds.** In each round every query runs every timed arm once, in an arm order rotated per
   query; round 2 uses the reverse query order. Latency statistics use all 200 timings per arm and
   database; recall uses round 1 (results are deterministic for a fixed index; any difference between
   rounds is reported).
4. Offline controls once per query.
5. Storage: `pg_total_relation_size` of raw chunks, envelopes, probe table and probe index.
   Build cost: after all searches, `REINDEX INDEX` of the probe index with `maintenance_work_mem` 4 GB
   (time and load average). Envelope build cost is historic (CW1, batched builder) and labelled so.

## Reported

Per arm and database: recall mean, minimum, quartiles, number of queries below 0.9 and 0.8, recall by
best-lag band (0–5, 6–15, 16–30; best lag from the 61 exact lag terms), recall for random vs
high-energy queries; latency p50/p95/p99/max; the ten hardest queries for PR with their recall in every
arm; work counters; sizes and build time; load averages. H1, H2 verdicts.

## Abort criteria

Any SQL error; EX-2 and EX-8 returning different lists (correctness failure: stop and report); free
disk below 100 GB. Elevated background load is recorded, not a reason to abort.

## Budget

At most 2 hours of measurement in total; one run. No parameter is changed after seeing results.

## Deviations

(none yet)
