# E6: a fast in-database exact scorer against probe search — protocol (FROZEN)

Frozen 2026-11-02, before any timed E6 run. Development pilots on `morlet_r2` (one query, best of three,
listed below) were run while building the scorer; they are not results. Deviations are appended below.

## Question

E3 and E4 found in-database exact search about 16× slower than a materialized matrix scan doing the same
arithmetic, and a reviewer argued that probe search may solve a problem a better exact scorer would remove.
**With the in-database exact path made as fast as we can make it without changing its results, is it faster
than probe search at p95 on galleries of 0.23, 0.50 and 1.03 M windows?** Secondary: how much of E3's gap to
the materialized scan remains.

## The change under test (library only; SQL objects unchanged)

`morlet_envelope_distance` keeps its SQL signature, and its distances are **bitwise identical** to the
measured build:

1. float16 widening by bit manipulation instead of `powi` (identical for all 65,536 codes, unit test);
2. a shifted-query matrix (`LagQuery`) built once per query and backend and cached, so each stored envelope is
   scored by one pass with independent accumulators across lags; every lag's products are still added in
   ascending order from the same neutral element (unit test over window sizes 512–8192 and lags 0..n−1);
3. the query array is read without copying, and stored envelopes are decoded into a reused buffer with the
   same arithmetic.

Build: `68594d7` (the build E1–E4 measured) plus exactly this patch (`e6-scorer.patch`, recorded in the
results), library SHA-256 `e242b744…`. The measured library `3c786684…` is kept and restored after the study.
Database objects are those E4 used; nothing is rebuilt.

## Data and queries

E4's three nested development galleries: N1 `morlet_r2` (234,762 windows), N2 `morlet_e4_30` (504,650),
N3 `morlet_e4_61` (1,026,010); E1's 100 development queries. No held-out data.

## Arms (E3's harness, `evaluation/strong_exact/e3.py`, unchanged in method)

All in-database arms run on the E6 library.

| Arm | What |
|---|---|
| X-PG | product exact search (`morlet_search_envelope_interval`), `max_parallel_workers_per_gather = 8` as in E1–E4 |
| P-PG | product probe search (13 probes × 50, HNSW, re-ranked with the same scorer) |
| X-MAT32R | E3's materialized float32-screen / float64 re-score scan, outside the database (reference) |

Two configurations per gallery, each a separate process:
- **default**: the envelope table's planner-chosen worker count (3 at N1);
- **w8**: the envelope table's `parallel_workers` storage parameter set to 8 for the run and reset after it
  (`max_parallel_workers` is 8 by default, so 8 is the most PostgreSQL's defaults allow).

N3 uses the harness's `max_candidates = 2000000`, as in E4.

## Correctness gate (per gallery, before its timing)

1. E3's gate on the E6 library (synthetic selection, scorer and decode checks, X-MAT64 and X-MAT32R lists
   identical to in-database exact search for 100/100 queries).
2. **Identity with the measured build:** for all 100 queries, the in-database exact list on the E6 library
   equals E4's stored list (`evaluation/scaling_crossover/results/gate_N*.json`) in recording, start and
   distance, bit for bit.

A failure stops that gallery's timing until explained.

## Procedure

Order: N1 default, N1 w8, N2 default, N2 w8, N3 default, N3 w8, each directly after its gallery's gate, with
no load, index build, fuzzing or other study running. `caffeinate` keeps the machine awake. E3's warm-up and
two interleaved rounds per run.

## Decision rules

1. **Primary, per gallery (w8 run):** R(N) = p95(X-PG) / p95(P-PG), 95% bootstrap interval over queries
   (10,000 resamples, seed 20261101). "Exact faster" if the whole interval is < 1, "probes faster" if > 1,
   else "tie".
2. **Secondary:** the same for the default run; p95(X-PG) / p95(X-MAT32R) (remaining gap to the
   materialized scan).
3. **Descriptive:** speed-up of in-database exact search over the measured build, p95 of E4's X-PG (same
   machine, harness and galleries, measured 2026-11-01) over E6's; labelled as across sessions.

## What follows

If exact is faster at some N, exact search becomes the default there and probes the path for larger
archives; if probes stay faster, the paper states the speed claim against this faster exact path. Product
changes (the scorer, and whether `morlet_enable_envelopes` should set `parallel_workers`) go into a separate
PR; this study changes no product default by itself.

## Budget and stop

About two hours of measurement, no new data, no disk beyond results. Stop after E6.

## Development pilots (before freezing; one query, best of three, N1)

| Library | Workers | Score all rows | Product exact search |
|---|---|---|---|
| measured `3c786684` | 3 | 692 ms | 729 ms |
| kernel only | 3 | 394 ms | 441 ms |
| E6 `e242b744` | 3 | 298 ms | 337 ms |
| E6 `e242b744` | 8 | 214 ms | 246 ms |

## Deviations

(none yet)
