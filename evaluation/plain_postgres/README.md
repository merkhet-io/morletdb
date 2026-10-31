# Plain PostgreSQL baselines for pg_morlet storage and search

These are engineering benchmarks, not method evaluations.

**Summary.** P1: envelope storage in pgvector `halfvec` ties pg_morlet byte for byte, but zero-lag search agrees on only 65% of the top 10. Lag-tolerant scoring reproduces it exactly, at 2× the latency, when done in the client. P2: TimescaleDB compression is 24–35% larger than pg_morlet on integer columns and 2.3–6.3× larger on float columns.
 They ask whether a feature of
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
- **pgvector is 20× faster when shifts are not needed** (this run, before #47's
  search speed-up; see the re-run below). A scores about 180 candidates in
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

### P1 re-run after the search speed-up

#47 made pg_morlet's reads and envelope search faster with identical results, so the
20× above is historical. The same script (`halfvec_bench.py`, unchanged) was rerun
once on 2026-10-31, with all four arms in **one run**.

**Setup:**
- **Database:** `morlet_speed_after`, a fresh pg_morlet 0.3.1 install created with
  #47's SQL, holding the same 1,826 CX 2022 windows.
- **Extension build:** `main` at 31bd134 plus the chunk-codec change under review
  (offset grids). That change does not touch reading encoded version-2 chunks or
  envelope search.
- **Results:** `results/rerun-main/halfvec.json`.

| | A: pg_morlet | B: halfvec exact cosine | C: halfvec HNSW | D: halfvec + client lag scoring |
|---|---|---|---|---|
| Recall@10 vs A | 1 | 0.647 | 0.647 | 1.000 |
| Latency p50 / p95 | **6.85 / 8.06 ms** | **0.49 / 0.53 ms** | 0.50 / 0.55 ms | 20.3 / 27.7 ms |

- **Same-run ratio:** A to B is **14×** at p50 (15× at p95). This is the figure the
  root README quotes.
- **Unchanged:** storage (1,794,048 bytes each), representation identity (1826/1826)
  and the recall figures.
- **C** again did not use its HNSW index.

## P2: TimescaleDB compression of raw samples (plan, written before running)

**Question.** pg_morlet stores raw chunks with a lossless integer codec. TimescaleDB's
native compression (Gorilla for floats; delta-of-delta and simple-8b for integers) is the
standard way to store time series compactly in PostgreSQL. How do they compare on the
same samples?

**Software.**
- `timescale/timescaledb:2.30.2-pg17`: the full build, since compression is
  Timescale-License only. Run as a throwaway Docker container bound to 127.0.0.1; download
  approved in chat on 2026-10-30.
- pg_morlet 0.3.1 on the local pgrx PostgreSQL 17.11.

**Data.** The same inputs as the encoded-chunk check ([compression/extension](../compression/extension/README.md)):
- the first 1,024 chunks of 4,096 samples (4,194,304 samples) of the held-out streams;
- CX seismic counts, IMS three-decimal values, Paderborn ADC counts, and Paderborn's
  physical floats;
- regenerated with the same loaders and chunking as `dump_chunks.py`.

**Layouts.**

| Id | Layout |
|---|---|
| M | pg_morlet encoded stream collection, appended in 4,096-sample chunks (extractor `none`, raw only) |
| T-f | Hypertable `(rec int, t bigint, v float8)`, one row per sample, `t` = sample index; compressed with `segmentby rec`, `orderby t`; 1,048,576 samples per Timescale chunk |
| T-i | Same, `v bigint`, holding integer counts. For IMS the integer is value × 1000. Paderborn physical floats have no exact integer form, so this layout is skipped for them |
| T-a | One row per 4,096-sample chunk, `samples float8[]`, compressed the same way. This is the array layout a MorletDB-style user would have without the extension |

**Measures.**
- **Size:** bits per sample = 8 × total bytes / samples, with all compressed chunks,
  indexes and TOAST included. For M: the chunk table plus the recordings table. For T:
  `hypertable_size` after `compress_chunk` on every chunk and `VACUUM`.
- **Uncompressed hypertable size,** for reference.
- **Ingest time** (one local connection, COPY for T; batched `morlet_append_chunk` for M).
- **Correctness:** read back, M via `morlet_read_interval`, T via `ORDER BY t`, checked
  bitwise for 4 intervals.

**Expectations:**
- T-i close to M on integer data; T-f clearly worse than M on noisy floats.
- T-a hardly compresses (an array column gets only generic compression).

### P2 results (run 2026-10-30, `results/timescale.json`)

Bits per sample, total on-disk size including indexes, TOAST and metadata tables.
4,194,304 samples per dataset. Every layout read back bit-exact.

| Dataset | **M: pg_morlet** | T-i: Timescale, integer column | T-f: Timescale, float8 column | T-a: Timescale, float8[] per chunk |
|---|---|---|---|---|
| CX seismic counts | **6.12** | 8.25 (1.35×) | 14.34 (2.3×) | 25.0 (4.1×) |
| IMS three-decimal values | **11.36** | 14.06 (1.24×) | 71.95 (6.3×) | 28.1 (2.5×) |
| Paderborn ADC counts | **9.95** | 12.89 (1.30×) | 24.58 (2.5×) | 26.4 (2.6×) |
| Paderborn physical floats | **11.53** | – (no exact integer form) | 70.12 (6.1×) | 29.5 (2.6×) |

Multiples are relative to M.

- **Reproduces the earlier check:** M's chunk table is 5.97 / 11.20 / 9.80 / 11.38
  bits/sample, matching [compression/extension](../compression/extension/README.md)
  (5.94 / 11.20 / 9.80 / 11.38).
- **TimescaleDB on integers comes closest, but is still 24–35% larger.** That requires
  the user to convert to integer counts first, which is the same knowledge pg_morlet asks
  for as `sample_quantum`.
- **TimescaleDB on floats compresses poorly.**
  - Gorilla XOR coding suits slowly changing values. It **expands** noisy decimal values
    beyond 64 bits/sample (IMS 72, Paderborn physical 70).
  - pg_morlet stores the same floats losslessly at about 11.5 bits by fitting the value
    grid, with ±1 ULP corrections where needed.
- **An array per chunk gets only generic compression** (25–30 bits/sample). That is
  worse even than plain PostgreSQL `float8[]` with TOAST (17–22 bits in the earlier
  check). The cause was not investigated.
- **Uncompressed, one row per sample** costs about 743 bits/sample (93 bytes), so the
  row-per-sample layout only makes sense compressed.
- **Ingest times are not compared.** M ran natively, TimescaleDB in a Docker VM: M took
  4.4–6.9 s, T row COPY 5.9–8.8 s, T arrays 1.7–3.6 s, and compression took 0.2–0.7 s.
- **Not tested:** other `segmentby` and `orderby` choices, larger compressed batches, and
  TimescaleDB's newer column formats. A different layout might narrow the integer gap.
  The float result follows from the algorithm.
