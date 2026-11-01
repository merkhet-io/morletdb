# E3: optimized exact baseline for bounded-lag search — protocol (FROZEN)

Frozen 2026-11-01, before the first timed measurement. Deviations are appended below and committed before the
run they affect. No result of this study existed when this file was committed.

## Question

Do shifted HNSW probes give a latency advantage over an optimized, materialized exact computation of the
same bounded-lag top ten, at the existing development-gallery size (234,762 windows)?

## Data and queries

- Database `morlet_r2` (development; CX 2025, PB01 PB02 PB06 PSGCX, 4096/2048 envelopes), as left by E1, D1 and
  the repeat check. Not modified by this study.
- E1's 100 development queries (`evaluation/bounded_lag/dev_queries.json`; 50 random, 50 high-energy).
- The consumed held-out database `morlet_bl1c` is not used. No download.

## Build

Both PostgreSQL arms run on the installed library `3c786684…`, built from `main` 68594d7, the build that
created this database's SQL objects (pg_morlet 0.3.1). Not `v0.3.1-alpha.1`: its SQL version string is also
0.3.1, so installing its library would leave this database's 68594d7 function bodies in place and mix two
builds. The exact and probe statements are identical in both (research/paper/ARTIFACT.md, section 9). New
timings are not mixed with E1's.

## Arms

| Arm | What |
|---|---|
| X-PG | `morlet_search_envelope_interval(..., top_k 10, 0.075, exclude_source true, suppress true, max_candidates 1e6)` with `max_parallel_workers_per_gather = 8` (E1's fastest development exact arm, EX-8) |
| P-PG | `morlet_search_envelope_probes(..., top_k 10)` with its defaults (13 probes × 50; it sets `hnsw.ef_search` to max(50, 100) = 100; recorded) |
| X-MAT64 | materialized float64 gallery (stored float16 values decoded and z-scored exactly as `morlet_core::envelope::decode`), shifted-query matrix for all 61 lags built from the query envelope exactly as `morlet_envelope_probe`, blocked GEMM (32,768 rows per block), row-wise max, distance `1 − max/n`; then eligibility, source exclusion, ordering and greedy suppression as below |
| X-MAT32R | float32 copy of the same gallery and query matrix; screen with a rigorous per-window error bound δ; re-score in float64 (X-MAT64's kernel) every window whose float32 distance is within 2δ of the m-th screened distance; then the same selection. Counts only if it passes the same correctness gate |

No FAISS, sparse-grid matrix arm, SIMD extension or new index.

**Error bound for X-MAT32R.** For each lag, float32 evaluation of an n-term dot product of values that are
exact in float32 (float16 gallery values) and float32-rounded query values satisfies
|fl32(s·g) − s·g| ≤ (γ_n + u)·Σ|s_t||g_t| with u = 2⁻²⁴, γ_n = n·u/(1 − n·u) (any summation order). With
Σ|s_t||g_t| ≤ ‖q‖·‖g‖ (Cauchy–Schwarz; ‖s_ℓ‖ ≤ ‖q‖), the distance error is at most
δ(g) = (γ_n + u)·‖q‖·‖g‖ / n plus the float64 rounding of z-scored values to float32 (bounded the same way
with u). δ is computed per window from stored ‖g‖.

## Contract replicated by the matrix arms

As `morlet_envelope_ranked` (streams.sql): eligible windows have the query's window size, and their recordings
the query's sample rate, channel and unit (metadata filter `{}`); windows of the source recording that overlap
[start, start + 4096) are excluded; order by (distance, recording_id, start_sample), with recording_id ordered
as PostgreSQL orders it in this database (checked against `ORDER BY` on the stored identifiers); candidate pool
m = 200, suppression walks the pool greedily, and if the pool is used up before ten survive the pool grows 4×,
exactly as the SQL loop. Ties at the pool boundary are included.

## Correctness gate (before any timing)

1. Synthetic: the selection code against a brute-force full sort on small synthetic galleries with exact
   ties, overlapping windows, windows at recording boundaries, a source interval, and a pool that is used up
   (forcing the 4× refetch).
2. Scorer: for 500 (query, window) pairs including the first and last lags and zero padding, the matrix
   distance equals `morlet_envelope_distance` within 1e-12 absolute; decoded values equal
   `morlet_envelope_decode` exactly.
3. Lists: for all 100 queries, X-MAT64 and X-MAT32R return the same ten (recording, start) in the same order
   as X-PG, with distances within 1e-12. Every ordering mismatch is investigated; a mismatch explained only by
   adjacent distances closer than 1e-12 is reported as a near tie, not hidden. Any other mismatch stops the
   study until explained.

## Timing boundaries (reported separately)

- **kernel**: matrix arms only; GEMM over all blocks and row-wise max, query matrix precomputed.
- **search**: matrix arms; kernel plus eligibility, exclusion, ordering and suppression, query envelope given.
- **request**: all arms. Matrix arms: one SQL call `SELECT morlet_envelope(samples) FROM
  morlet_read_interval(...)` (source interval and query envelope, as the PostgreSQL functions do internally)
  plus building the query matrix plus search. PostgreSQL arms: the full function call from the client. This
  is the boundary for the decision rule.
- **materialization** (once): reading all envelopes from PostgreSQL, decoding, building the matrices; time,
  resident memory of the matrices, and what a refresh would need.

## Configurations and threads

Matrix arms run in two configurations, each its own process with `VECLIB_MAXIMUM_THREADS` set before numpy is
loaded: **matched** = 4 threads (X-PG's leader plus the 3 workers it launched in E1) and **full** = 18 threads
(all cores). Effective threads are measured as process CPU time / wall time during the kernel. No other
configuration is tried. PostgreSQL arms run in both processes (same session settings).

## Procedure

Per configuration: materialize; untimed warm-up of all arms on 5 queries; two rounds over the 100 queries
(round 2 in reverse order), arms rotated per query as in E1; one client; warm caches. Record environment as E1
(commit, library SHA-256, extension versions, non-default settings, hardware), BLAS (numpy config), dtype,
layout (C-contiguous rows × 410), block size, thread settings, load average at the start and end of each round.

## Metrics

p50 / p95 / p99 per arm and boundary over 200 timings; recall@10 against X-PG; random and high-energy queries
separately and together; materialization time and memory.

## Decision rule (at the request boundary)

Primary comparison: P-PG against the faster contract-valid matrix arm in the **full** configuration; secondary:
the same in the **matched** configuration. Ratio R = p95(P-PG) / p95(matrix arm), 95% bootstrap interval from
10,000 resamples of queries (both timings of a query move together), seed 20261101.

- **Advantage for probes demonstrated** if the whole interval of R is ≤ 0.5.
- **No advantage demonstrated** if the whole interval is > 0.5.
- **Inconclusive** otherwise.

## Budget and stop

At most two hours of timed measurement and one working day in total. Stop after E3: no scaling,
task-relevance, codec or production-scorer work follows automatically. If the correctness gate cannot be met
within the day, report that and stop.

## Deviations

(none yet)
