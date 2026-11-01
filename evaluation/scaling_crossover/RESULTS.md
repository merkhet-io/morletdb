# E4: scaling crossover between probe search and materialized exact search — results

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at 7009d80 (2026-11-01). Data checksums e2ed76b, loads 077e4f8.
Gates and timed runs 21:58–22:57 on 2026-11-01, development data only (CX HHZ, four R2 stations, days 1–61 of
2025-03-01..04-30; E1's 100 development queries). Build: `main` 68594d7, library `3c786684…`, pg_morlet 0.3.1,
PostgreSQL 17.11; numpy 2.2.6 with Accelerate, `VECLIB_MAXIMUM_THREADS` unset. Raw results:
`results/{gate,run}_N{1,2,3}.json`, `results/load_N{2,3}.json`; summary: `results/summary.json` (`analyze_e4.py`).

## Verdict

**Up to about one million windows, probe search did not become faster than materialized exact search at p95.**
Under the frozen rule, R(N) = p95(P-PG) / p95(X-MAT32R) was above 1 with its whole 95% interval at every
gallery size ("exact faster"). There is no measured crossover. Extrapolating fits to the three galleries puts it
at about 34 million windows. That figure is an extrapolation, not a measurement, and it lies far outside what
this machine could hold in memory for the matrix arm.

| Gallery | Windows | R(N) point | 95% interval | Rule (threshold 1) | E3 threshold 0.5 met |
|---|---|---|---|---|---|
| N1 (days 1–14) | 234,762 | 3.25 | [3.00, 3.72] | exact faster | no |
| N2 (days 1–30) | 504,650 | 2.61 | [2.29, 3.64] | exact faster | no |
| N3 (days 1–61) | 1,026,010 | 2.52 | [1.94, 3.51] | exact faster | no |

R falls as N grows, but slowly. Probe recall stayed at or above 0.99 (no flag).

## Correctness gate

E3's gate passed on every gallery before its timing (`results/gate_N*.json`):
- synthetic selection tests: 3,000 trials, 0 failures;
- decode bit-identical;
- scorer within 1.6e-15 of `morlet_envelope_distance`, including 93–96 of 100 pairs per gallery whose best lag
  is at ±29/±30;
- X-MAT64 and X-MAT32R returned the same ten windows in the same order as the in-database exact search for
  100/100 queries at N1, N2 and N3.

In the timed runs both matrix arms kept recall 1.0 against X-PG on every query. No arm's list changed between
rounds.

## Latency by gallery size

Request latency, ms, 200 timings per arm and gallery (100 queries × 2 rounds):

| Arm | N1 p50 / p95 / p99 | N2 p50 / p95 / p99 | N3 p50 / p95 / p99 |
|---|---|---|---|
| X-PG (in-database exact, 8 workers) | 724 / 735 / 743 | 1,228 / 1,248 / 1,267 | 2,174 / 2,403 / 2,755 |
| P-PG (probe search) | 41 / 68 / 98 | 58 / 108 / 222 | 78 / 202 / 385 |
| X-MAT64 (materialized, float64) | 45 / 46 / 48 | 94 / 96 / 98 | 185 / 199 / 258 |
| **X-MAT32R** (float32 screen + float64 re-score) | 20 / **21** / 22 | 39 / **42** / 44 | 75 / **80** / 117 |

- **X-MAT32R** grows linearly, as a full scan must: 21 → 42 → 80 ms at p95 (fit: 3.7 ms + 74.4 ms per million
  windows). Its kernel alone was 15 → 33 → 67 ms at p95.
- **P-PG** grows sublinearly but not slowly: 68 → 108 → 202 ms at p95 (fit: p95 ∝ N^0.73). The growth is in
  the tail. P-PG's p50 rose only 41 → 58 → 78 ms. Its p95 for E1's random queries was 78 → 152 → 282 ms, and
  for its high-energy queries 54 → 75 → 103 ms.
- At **p50**, the two arms were nearly level at N3: 78 ms (P-PG) against 75 ms (X-MAT32R). This is
  descriptive only; the decision rule is on p95.
- **X-PG** grows about linearly: 735 → 1,248 → 2,403 ms at p95. Probe search stays 11–12 times faster than
  the in-database exact path at p95, and X-MAT64 stays 12–16 times faster than it.

Load averages during the timed runs were 3.5–8.7. No load, index build or other study ran at the same time, but
the machine was not otherwise idle. Both rounds gave similar numbers at every size.

## Crossover (rule 2)

No measured gallery had "probes faster". The extrapolation in `results/summary.json` uses the protocol's fits:
- X-MAT32R p95 = 3.73 ms + 74.4 ms per million windows;
- P-PG p95 = 0.00765 · N^0.733 ms.

The two curves cross at about **3.4 × 10⁷ windows**. Caveats:
- It rests on three points, and the P-PG exponent is sensitive to the tail of one gallery.
- It assumes nothing else changes. P-PG keeps ef_search = 100 and 13 × 50 candidates at every N, and its
  recall at that size is unknown.
- At that size the matrix arm as built here would need about 56 GB (float32) plus 112 GB (float64) in memory.

The crossover therefore lies beyond where this materialized arm can run on this machine. It is not a
demonstrated property of probe search.

## Recall (rule 3)

P-PG mean recall@10 against the exact lists: 0.998 (N1), 0.990 (N2), 0.993 (N3). Minimum per query: 0.9, 0.7
and 0.8; one query was below 0.9 at N2 and one at N3. No size fell below 0.95.

## Memory, materialization and index

| Gallery | Materialize (read + decode/build) | Matrices (f64 + f32) | Peak RSS | Probe HNSW index | Probe enable time |
|---|---|---|---|---|---|
| N1 | 1.2 + 1.1 s | 0.77 + 0.39 GB | 3.6 GB | 275 MB (E1 sizes) | not re-measured |
| N2 | 2.7 + 2.9 s | 1.66 + 0.83 GB | 7.6 GB | 591 MB | 133 s* |
| N3 | 5.3 + 6.9 s | 3.37 + 1.68 GB | 15.3 GB | 1.20 GB | 283 s* |

\*N2 and N3 were loaded at the same time, so their index build times include contention with each other.

Memory for the matrix arms grows linearly. On this 128 GB machine, the arm as built (both matrices plus fetched
rows) would reach its limit at a few million windows; a float32-only screen with re-scoring from disk would
reach further. The limits listed in E3 still apply: no metadata filters, row-level security, snapshot
consistency or freshness for the matrix arms.

## Deviations

1. **Exact-search candidate cap at N3.** The harness of E1 and E3 calls the in-database exact search with
   `max_candidates = 1000000`. N3 has 1,026,010 eligible windows, so the function refused and N3's first gate
   attempt stopped in the list check at 22:10. `run_e4.py` gained an optional cap argument, and N3's gate and
   run used 2,000,000. The cap only decides whether the function refuses; it does not change the list or its
   timing method. N1 and N2 ran with the original cap. This deviation was recorded after the N3 run, not before
   it as the protocol asks, and was appended to PROTOCOL.md with these results. It is also a usage finding: an
   unfiltered in-database exact search over more than a million windows needs a caller-raised cap (the
   function's default is 4,096).
2. **Concurrent loads.** N2 and N3 were loaded in parallel. This affects only the recorded load and index
   build times, not any timed measurement.
3. A first attempt at the sequential run failed before measuring anything (a shell word-splitting error in
   the driver loop); it was rerun unchanged.

## Limitations

- Development data from four stations, one machine (Apple M5 Max, 128 GB), warm caches, one client, unfiltered
  top ten. Three gallery sizes. The held-out database was not used.
- P-PG parameters were fixed at E1's values at every N. Other ef_search or probe counts would move both its
  latency and its recall.
- Extrapolation beyond 1M windows is a fit, not a measurement.

## What this means for the claims (not changed here)

E3's qualifications stand, and E4 extends them: up to a million windows, the speed claim for probe search holds
only against the in-database exact path, which it beats by 11–12 times at p95. A materialized exact copy was
2.5–3.3 times faster than probes at p95 at every measured size, at the cost of memory and a copy kept consistent
outside the database. "Probes win beyond N windows" cannot be stated from measurement. The extrapolated N
(about 34M) is beyond what the matrix arm can hold here. A defensible claim is the in-database one, plus the
practical one: probe search needs no copy and keeps PostgreSQL's filters, row-level security and snapshot
semantics.
