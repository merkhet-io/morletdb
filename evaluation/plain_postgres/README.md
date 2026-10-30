# Plain PostgreSQL baselines for pg_morlet storage and search

These are engineering benchmarks, not method evaluations. They ask whether a feature of
pg_morlet could be had **without the extension** at the same cost. Each plan is written
here before its run; results are added below it.

## P1: envelopes in pgvector `halfvec` (plan, written before running)

**Question.** pg_morlet stores search envelopes as float16 (`xc-envelope-f16-v1`, 821
bytes per 4096-sample window) and ranks them by the maximum cross-correlation over time
shifts, scored in Rust inside the database. Plain PostgreSQL with pgvector 0.8.0 has
`halfvec`, which also stores 2 bytes per value. Can it match storage, ranking and latency?

**Data.**
- The 1,826 CX 2022 seismic windows already loaded in `morlet_envelope_check` (R1b's SQL
  check).
- They are 4096 samples at 100 Hz, from 10 stations.
- The run uses a copy, `morlet_halfvec_bench`.
- This period was already used by R1b, so this is a benchmark, not a held-out test.

**Queries.** Every window once, restricted to the same station, itself excluded, top 10.

**Arms.**

| Arm | What it is |
|---|---|
| A | pg_morlet `morlet_search_envelope_interval`, lags up to ±30 envelope values. **Reference.** |
| B | Plain `halfvec(410)` column, exact cosine order (no time shift) |
| C | As B, through an HNSW index (`halfvec_cosine_ops`, m 16, ef_construction 64, ef_search 100, iterative scan `strict_order`) |
| D | Plain `halfvec` storage; the client fetches the station's envelopes and computes the same lagged cross-correlation in numpy |

For B–D the envelopes are computed in the client from the raw samples, using the R1
reference definition. A representation check counts how many are bit-identical to
pg_morlet's stored values.

**Measures.**
- **Storage:** total table bytes, including TOAST and primary key; the HNSW index
  separately.
- **Ranking:** recall@10 against A and the share of queries with an identical order.
- **Latency:** client wall-clock per query (p50, p95), after 20 warm-up queries, on one
  local connection.

**Expectations, written down to be checked:**
- Storage roughly equal.
- B and C lose some top-10 agreement, because they do not tolerate time shifts.
- D reproduces A's ranking but is slower, because it ships candidates to the client.
- None of this measures relevance; A is the reference only because it is the evaluated
  method.

### P1 results (run 2026-10-30, `results/halfvec.json`)

| | A: pg_morlet | B: halfvec exact cosine | C: halfvec HNSW | D: halfvec + client lag scoring |
|---|---|---|---|---|
| Storage (table, TOAST, keys) | 1,794,048 B | 1,794,048 B | + 2,146,304 B index | same as B |
| Bytes per stored envelope | 825 | 828 | | 828 |
| Recall@10 vs A | 1 | **0.647** | 0.647 | **1.000** |
| Identical top-10 order vs A | 1 | 0.001 | 0.001 | 1.000 |
| Latency p50 / p95 | 9.4 / 11.0 ms | **0.48 / 0.55 ms** | 0.48 / 0.51 ms | 20.0 / 27.0 ms |

- **Representation:** the client-computed float16 envelopes are bit-identical to
  pg_morlet's stored values for 1826/1826 windows. Both tables use 203 heap pages.
- **Storage is a tie.** pg_morlet's envelope format has no size advantage over
  `halfvec`.
- **The time-shift tolerance is the difference.**
  - Without shifts, plain cosine shares only 65% of the top 10 and almost never the
    order.
  - Re-scoring with shifts in the client reproduces A exactly, at about 2× A's latency,
    because the client fetches about 180 envelopes per query.
- **pgvector is 20× faster when shifts are not needed.** A scores about 180 candidates in
  about 9 ms. That is slower than a zero-lag halfvec scan and leaves room to optimise A,
  for example with an FFT cross-correlation or a halfvec pre-filter followed by a lagged
  re-rank.
- **Deviation from plan:** C did not exercise HNSW. With a station filter on about 180
  rows per station, the planner chose the B-tree index on `station` and an exact sort, so
  C equals B. The index was not forced after the fact. At this gallery size the exact
  scan is already sub-millisecond, and HNSW only matters for much larger galleries
  (VR1).
- **Numerics:** macOS Accelerate printed spurious matmul warnings (as in S1). D's recall
  of 1.000 against A shows the scores are finite and correct.

## P2: TimescaleDB compression of raw samples

Not run. TimescaleDB is not installed, and installing it needs a download approval.
