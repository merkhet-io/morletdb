# E3: optimized exact baseline — results

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at 686a7d4 (2026-11-01 19:49). Harness and correctness gate
c237457 (19:55). Timed runs 19:56–20:07, development data only (`morlet_r2`, 234,762 windows, E1's 100
development queries). Build: `main` 68594d7, library `3c786684…`, pg_morlet 0.3.1, pgvector 0.8.0,
PostgreSQL 17.11; numpy 2.2.6 with Accelerate. Raw results: `results/gate.json`, `results/run_{full,matched}.json`;
summary: `results/summary.json` (`analyze.py`).

## Verdict

**No latency advantage for probes over materialized exact search was demonstrated at this gallery size.**
At the request boundary, the fastest contract-valid matrix arm (X-MAT32R: float32 screen with a rigorous
error bound, float64 re-scoring) had a p95 of 20.9 ms with recall 1.0. Probe search (P-PG) had a p95 of
44.8–68.7 ms with recall 0.998. Under the frozen decision rule, R = p95(P-PG) / p95(X-MAT32R) is 3.30
[3.01, 3.62] in the primary run and 2.15 [1.96, 2.24] in the secondary run. Probes would have needed
R ≤ 0.5.

## A. Does the optimized exact baseline reproduce the reference contract?

Yes, on everything checked (`results/gate.json`):

- Selection (eligibility, source exclusion, ordering by distance, recording and start, greedy suppression,
  4× pool refetch) against a brute-force full sort on 3,000 synthetic galleries with exact ties, overlaps,
  recording boundaries and forced refetches (pools of 1, 2, 3 and 200): 0 failures.
- Decoded gallery values equal `morlet_envelope_decode` bit for bit (20 rows checked). Rust's sequential
  z-score sums are reproduced column by column.
- Distances against `morlet_envelope_distance` on 500 query–window pairs and on 100 pairs built so that the
  best lag lies at ±29 or ±30 (94 of them did): max absolute difference 1.6e-15.
- Lists: X-MAT64 and X-MAT32R return the same ten windows in the same order as the in-database exact search
  for all 100 queries, with distances within 3.3e-16. No near-tie reorderings occurred.
- In the timed runs both matrix arms kept recall 1.0 against X-PG on every query and both rounds. No arm's
  list changed between rounds.

X-MAT32R's bound (δ ≤ 2.5e-5 in distance) is tight enough that it re-scored about 200 windows per query
(median 200, max 203) in float64.

## B. Which arm wins under each timing boundary?

Request latency, ms, over 200 timings per arm (100 queries × 2 rounds):

| Arm | p50 | p95 | p99 | recall@10 vs X-PG |
|---|---|---|---|---|
| X-PG (in-database exact, 8 workers allowed) | 721–722 | 736–748 | 750–1,161 | 1.000 |
| P-PG (probe search) | 37–40 | 45–69 | 58–93 | 0.998 (min 0.9) |
| X-MAT64 (materialized, float64) | 45.0–45.2 | 46.6–46.7 | 47–58 | 1.000 |
| **X-MAT32R** (materialized, float32 screen + float64 re-score) | 19.6 | **20.9** | 21–68 | **1.000** |

Ranges span the two runs. Parts of the matrix arms, p50 / p95 ms (both runs alike):

| Boundary | X-MAT64 | X-MAT32R |
|---|---|---|
| query preparation (one SQL call: source interval and query envelope) | 1.7 / 1.9 | 1.8 / 2.0 |
| kernel only (GEMM over 61 lags, row max) | 40.6–41.0 / 41.6–41.7 | 14.5 / 15.5–15.7 |
| search (kernel plus eligibility, exclusion, ordering, suppression) | 43.3–43.5 / 44.9–45.0 | 17.8 / 18.9–19.1 |

- **Kernel and search:** both matrix arms beat P-PG's request latency, even though the matrix numbers
  exclude query preparation (about 2 ms).
- **Request:** X-MAT32R wins at p50, p95 and p99. X-MAT64 is level with P-PG at p50 and faster at p95.
- By query group, P-PG's p95 was 52 ms (high-energy) and 76 ms (random) in the primary run, and 44–45 ms in the
  secondary run. The matrix arms do the same work for every query (p95 20.6–22.2 ms for X-MAT32R).
- The in-database exact path is about 16× slower than X-MAT64 at the request boundary: 15.8 [15.6, 15.9] in
  the primary run. This study does not separate how much of that comes from arithmetic (scalar Rust scorer
  versus GEMM), layout (per-row detoast, float16 decode and z-score per query versus one materialized matrix)
  or threading. It is not attributed to "PostgreSQL overhead".

P-PG's p95 differed between the two runs (68.7 versus 44.8 ms) at load averages of 2.1–5.1 and 3.8–5.4. The
matrix arms and X-PG were stable across runs. Both runs give the same verdict.

## C. Memory and materialization trade-off

- Materialized gallery: 770 MB (float64) and 385 MB (float32) for 234,762 windows × 410 values, kept in
  memory. Peak process RSS was 3.56 GB, including the fetched rows. In this database the envelopes table takes
  223 MB with its key, and probe search adds a 223 MB vector table plus a 275 MB HNSW index on disk
  (`evaluation/consolidated/results/sizes_dev.csv`).
- One-off materialization: 1.2 s to read the envelopes from PostgreSQL plus 1.1 s to decode and build both
  matrices.
- Refresh: the matrices are a copy. Every append, deletion or rebuild needs the copy updated, and nothing
  here keeps it consistent with the database.
- **What the matrix arms do not provide:** metadata filters beyond this unfiltered case, row-level security,
  snapshot consistency with concurrent writes, and visibility of new windows at commit. The PostgreSQL arms
  provide all four. The comparison is of latency for an unfiltered top ten on a static gallery, not of
  equivalent systems.
- Cost grows with N. The matrix arms scan every window, so their kernel time grows linearly with the
  gallery, while an HNSW scan grows much more slowly. That crossover was not measured.

## Thread configurations (finding, not a deviation)

The protocol ran the matrix arms with `VECLIB_MAXIMUM_THREADS` = 4 ("matched") and 18 ("full"). The setting
had no measurable effect:
- Kernel times were identical across runs (X-MAT64 41 ms, X-MAT32R 15 ms).
- Effective CPU during the kernel was 1.8–1.9 cores in both runs (process CPU time / wall time).

The float64 kernel ran at about 290 GFLOP/s, and float32 at about 810 GFLOP/s (11.7 GFLOP per query). That is
consistent with Accelerate running GEMM on the processor's matrix unit, whose work is not counted as process
CPU time, but this was not verified. The two runs therefore differ in time and machine load, not in thread
count, and no conclusion about matched threads can be drawn. The primary and secondary verdicts agree.

## Limitations

- Development data, one gallery size (234,762 windows), one machine (Apple M5 Max), warm caches, one client.
  The held-out database was not used.
- Matrix arms run outside the database with the contract limits listed under C.
- One BLAS (Accelerate). Its threading could not be controlled through the environment variable.
- The matrix arms' advantage is a property of this gallery size and this hardware's matrix throughput. It is
  not a statement about larger galleries or other machines.

## Statements this evidence would qualify (not changed here)

- Report abstract and conclusion: "at a p95 latency of 57 ms, against 740 ms for the fastest exact path" and
  "13 times lower than that of the fastest exact path". 740 ms is the fastest **in-database** exact path. A
  materialized exact computation outside the database reached p95 21 ms on development data with recall 1.0.
- Report §7 "Benefit", Table 2 and the ledger rows "fastest exact p95 740 ms" and "probes 0.998, p95 53 vs
  783 ms" (same qualification).
- Report §12 "When to use it": "probe search paid off for unfiltered or weakly filtered galleries of about a
  quarter of a million windows, where it was 4–15 times faster at p95 than exact search". That holds against
  the in-database exact path only.
- Root README "57 ms p95, against 740 ms for the fastest exact path", and the website's "740 ms — Fastest
  tested exact path · p95".
- `research/paper/README.md` "Main numbers": "Ratio of p95 latencies, fastest exact arm to probes: 13".

## Recommendation (D)

A bounded scaling experiment is justified, and it is the decision that matters for the paper. At this size the
method's advantage is over the in-database exact implementation, not over exact search as such.

The claim that can survive is either "probes win beyond N windows", or "in-database probe search is
16× faster than in-database exact search, and a materialized exact copy is faster still at a quarter-million
windows, at the cost of a consistent copy". Only a crossover measurement decides between them.

A minimal version: nested development galleries of 0.25M, 0.5M and 1M windows from existing or replayed
development data, with arms P-PG and X-MAT32R only, and the same gate. If the owner prefers to move to the
importer and the read-only MCP demo first, the paper's speed claims should be qualified as above before
anything else is published.
