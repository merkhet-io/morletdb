# B3 results, stage 1: MorletDB and QuestDB on dense waveforms

Protocol: [PROTOCOL.md](PROTOCOL.md) (frozen before any B3 number was measured; deviations listed at its end).
Raw results: [`results/`](results/) (one JSON per load, plus `summary.json`). Measured 2026-11-02 on one
machine (Apple M5 Max, Docker Desktop, each database alone in a container limited to 8 CPUs and 16 GB).

**Workload:** 124 Paderborn acquisitions (every 20th file), each about 4 s of three 64 kHz channels (vibration,
two motor currents), three 4 kHz channels (force, speed, torque) and one 1 Hz temperature: 101,586,735 samples,
775.0 MiB as float64. **Systems:** MorletDB 0.3.3-alpha.1 (PostgreSQL 17.6, pg_morlet SQL 0.3.2) and QuestDB
10.1.0 (open source), both from Python 3.13 clients on the same host.

This is a map of trade-offs on one dataset, these configurations and this machine; it is not a ranking of
the systems and nothing here extends beyond it.

## Correctness

Both systems passed every check, on the 3-acquisition subset first and after each of the 13 measured loads:
row/sample counts, every one of the 868 channels per load read back with the manifest's SHA-256 (bit-exact
float64), timestamps equal to start + n × period exactly (QuestDB `TIMESTAMP_NS`; MorletDB stores timing
implicitly as rate + offset), identity (channel, unit, acquisition), and 20 random intervals crossing
ingestion batches. The QuestDB Parquet arms were read back bit-exact after conversion.

Retry semantics differ, by design ([subset results](results/correctness_subset.json)):

| | Identical retry | Retry with one sample altered |
|---|---|---|
| QuestDB (`DEDUP UPSERT KEYS(ts, acquisition)`) | one copy kept | **accepted; the stored value is replaced** (last write wins) |
| MorletDB (append at sample offset) | one copy kept (identical replay) | **refused** ("conflicting replay at chunk offset"); stored samples unchanged |

## A. Storage

After a settled state (QuestDB: WAL applied, server stopped; MorletDB: `CHECKPOINT`, `VACUUM (ANALYZE)`),
allocated bytes. Measured on D2 load 1; load 2 gave identical QuestDB sizes and MorletDB within 0.01%.

| Arm | Data | Indexes, symbols, metadata | WAL / journal | Whole volume | Data, bytes per sample | Data / float64 |
|---|---:|---:|---:|---:|---:|---:|
| Q-native | 1,162.7 MiB | 0.2 MiB | 15.7 MiB | 1,192.4 MiB | 12.00 | 1.50 |
| Q-parquet-lz4 | 482.1 MiB | 0.3 MiB | 0.3 MiB | 496.6 MiB | 4.98 | 0.62 |
| Q-parquet-zstd9 | 232.7 MiB | 0.3 MiB | 0.3 MiB | 247.3 MiB | 2.40 | 0.30 |
| **M-base** | **156.7 MiB** | 4.6 MiB | 176.0 MiB | 367.6 MiB | **1.62** | **0.20** |
| M-search (features + envelopes; not compared) | 156.7 MiB | 195.0 MiB | 464.0 MiB | 846.2 MiB | 1.62 | 0.20 |
| Packed: MorletDB exchange file (Parquet, zstd) | 197.2 MiB | | | | 2.04 | 0.25 |
| Packed: Parquet per group, QuestDB columns, pyarrow zstd 9 | 294.0 MiB | | | | 3.04 | 0.38 |

Per group (data bytes per sample):

| Group | Q-native | Q-parquet-zstd9 | M-base | Packed Parquet zstd 9 |
|---|---:|---:|---:|---:|
| 64 kHz (95.6 M samples, values on a measured grid) | 12.00 | 2.20 | **1.28** | 2.76 |
| 4 kHz (6.0 M samples, no clean grid) | 12.00 | 5.57 | **3.72** | 7.49 |
| 1 Hz (615 samples) | – | – | 8.66 | 154 |

The 1 Hz group is too small for per-sample figures to mean much (fixed per-file and per-chunk overhead).

**Statements (≥ 10%, single storage measure):**
- MorletDB's sample data is 33% smaller than QuestDB's smallest arm (Parquet zstd 9), 67% smaller than Parquet
  LZ4 and 87% smaller than native columns; per group, smaller in both the 64 kHz and the 4 kHz group.
- **On the whole volume, Q-parquet-zstd9 is 33% smaller than M-base** (247 vs 368 MiB): PostgreSQL keeps
  176 MiB of recycled WAL segments (governed by `min_wal_size`/`max_wal_size`, not by the data) plus about
  30 MiB of an empty cluster. Data-only and whole-volume rankings disagree; both are reported.
- Parquet conversion is an extra, explicit step in QuestDB: 3.1 s (LZ4) and 17.4 s (zstd 9) until settled;
  the newest partition is never converted.

**Attribution:**
- *Implicit timing and identity (representation).* QuestDB stores 36 bytes per 64 kHz row natively: 3 × 8 for
  values, 8 for the timestamp, 4 for the acquisition key; one third of native storage is time and identity,
  which MorletDB derives from rate and offset. Parquet compresses the regular timestamps well, so this explains
  the native gap, not most of the gap to Parquet.
- *Lossless grid coding (codec, dataset-dependent).* Attribution run without the sample grid
  ([`load_morletdb-noquantum_D2_r1.json`](results/load_morletdb-noquantum_D2_r1.json), outside the frozen arms):
  MorletDB data grows from 156.7 to **621.6 MiB (0.80 of float64), larger than Q-parquet-zstd9**. For the
  64 kHz channels, the advantage comes from coding values as integers on their measured grid
  (vibration 100/32768, currents 0.000688781…); without it, MorletDB's float fallback is weaker than zstd on
  these values (6.05 vs 2.20 bytes per sample). Signals without a clean grid will not see this advantage.
- *4 kHz group, no grid (codec).* MorletDB's float coding is 33% smaller than QuestDB's Parquet zstd 9 here,
  with the same configuration in both the base and the no-grid run. Not further explained.
- *Packed Parquet (pyarrow, zstd 9) is larger than QuestDB's own Parquet zstd 9* (294 vs 233 MiB): encoding
  choices differ between the two writers; not further explained.
- *Search structures* (M-search) add 195 MiB of segment features and envelopes (124 MiB segment features with
  their indexes, 51 MiB envelopes at window 4,096, 18 MiB recording metadata) and more retained WAL: they
  roughly double the stored size, the price of similarity search, which QuestDB does not offer.

## B. Ingestion

Three fresh loads per durability class; whole dataset from arrays in memory (file parsing excluded); median
[range].

| System | Class | Samples/s | Wall s | Ack per acquisition p50 / p95 | Queryable after last ack | Server CPU mean | Peak memory |
|---|---|---:|---:|---:|---:|---:|---:|
| QuestDB | D1 (`commit.mode=sync`) | 6.57 M [6.55–6.86] | 15.5 | 120 / 147 ms | 0.07 s | 308% | 2,028 MiB [1,927–2,267] |
| QuestDB | D2 | 7.39 M [7.16–7.78] | 13.7 | 103 / 161 ms | 0.01 s | 319% | 2,223 MiB [2,213–2,304] |
| MorletDB | D1 (`synchronous_commit=on`) | 0.48 M [0.48–0.51] | 211.1 | 1,694 / 1,798 ms | < 0.01 s | 25% | 168 MiB [168–171] |
| MorletDB | D2 | 0.53 M [0.52–0.56] | 190.7 | 1,558 / 1,676 ms | < 0.01 s | 28% | 169 MiB [169–169] |
| M-search (1 load) | D2 | 0.30 M | 335.9 (+ 13.7 s envelopes) | 2,783 / 2,892 ms | < 0.01 s | 49% | 187 MiB |

No failures, rejected writes or retries in any load.

**Statements (≥ 10%, all three loads):** QuestDB ingests 13–15× faster than MorletDB in both classes; MorletDB's
server uses 11–14× less peak memory. Durable acknowledgement costs QuestDB about 11% and MorletDB about 9%.
Both make data queryable immediately after acknowledgement (QuestDB applies its WAL within 70 ms).

**Attribution:** MorletDB's server is mostly idle during a load (25–30% of one core of 8), so the bottleneck is
on the client side. Batching 16 chunks per call (importer option `rpc_chunks=16`, attribution runs
[1](results/load_morletdb-batched16_D2_r1.json), [2](results/load_morletdb-batched16_D2_r2.json)) raised
the rate only to 0.57–0.58 M samples/s (+8–10%): round trips are not the main cost. The remaining
candidate is the Python path: every sample becomes a Python float and is serialised element by element into
a `float8[]` parameter, while QuestDB's client hands numpy/Arrow column buffers to native code. This is
attributed by elimination and not measured directly; a binary ingest path (raw float64 bytes or `COPY`
binary) is the obvious test. QuestDB's server parallelises ingestion (about 3 cores busy).

## C. Exact interval reads

D2 load 1, warm cache, 50 repetitions of each of 20 intervals (R5: 4 threads, 10,000 queries), end to end in
the client into float64 arrays, every result checked against the manifest. p50 / p95 ms.

| Query | Samples | Q-native | Q-parquet-zstd9 (additional) | M-base | Statement (≥ 10% in p50 and p95) |
|---|---:|---:|---:|---:|---|
| R1 short | 4,096 | 1.21 / 2.83 | 2.73 / 5.64 | 1.46 / 1.95 | none vs Q-native (split); MorletDB faster than Q-parquet-zstd9 |
| R2 long | 128,000 | 8.38 / 14.00 | 10.74 / 16.74 | 23.55 / 29.69 | QuestDB faster (2.8× / 2.1×; both arms) |
| R3 3 channels | 192,000 | 13.28 / 18.47 | 17.73 / 23.48 | 39.17 / 48.51 | QuestDB faster (2.9× / 2.6×; both arms) |
| R4 recent | 4,096 | 1.03 / 1.41 | 2.20 / 2.99 | 1.64 / 2.50 | Q-native faster; MorletDB faster than Q-parquet-zstd9 |
| R4 older | 4,096 | 1.10 / 1.62 | 2.09 / 2.90 | 1.48 / 1.98 | Q-native faster; MorletDB faster than Q-parquet-zstd9 |
| R5 concurrent | 4,096 | 1.42 / 6.21, 1,774 q/s | 2.67 / 7.99, 1,113 q/s | 2.17 / 4.22, 1,586 q/s | none vs Q-native (split); MorletDB faster than Q-parquet-zstd9 |

Recent vs older: no difference for QuestDB; MorletDB's older reads were faster than its recent ones by 10%
(p50, at the threshold) and 21% (p95), unexplained; both are warm-cache figures, cold reads are not claimed.

**Attribution (R2, MorletDB, exploratory run on the 18 acquisitions R2 uses,
[`attribution_reads_morletdb.json`](results/attribution_reads_morletdb.json)):** of 20.1 ms end to end (p50),
the server reconstructs the 128,000 samples in **6.9 ms** (a query returning only their count); transfer and
psycopg's decoding of the `float8[]` arrays into Python floats take about 9 ms, and building the numpy array
from those floats 4.0 ms. About two thirds of the long-read time is client-side decoding of PostgreSQL arrays;
the reads are also split into pieces of at most 32,768 samples (the function's documented limit). QuestDB's
client receives Arrow columns that map directly to numpy. Short reads are dominated by per-query overhead in
both systems.

## D. Interval analytics

min, max, mean and RMS of `vibration_1` over the 20 R2 intervals, in the database, 20 repetitions each;
p50 / p95 ms; all results equal numpy's within the frozen tolerance (largest relative difference 8.1e-14).

| Q-native | Q-parquet-zstd9 (additional) | M-base |
|---:|---:|---:|
| 3.50 / 6.67 | 5.87 / 13.90 | 20.61 / 21.81 |

**Statement:** QuestDB is faster (5.9× / 3.3× native). **Attribution:** MorletDB computes the statistics in SQL by
unnesting reconstructed samples into rows (PostgreSQL's row-at-a-time aggregation, about 7 ms of which is the
reconstruction measured above); QuestDB aggregates columns with vectorised code. A native interval-statistics
function in the extension would be the corresponding design.

## Trade-off map

| Dimension | Advantage | Size | Main cause |
|---|---|---|---|
| Sample data on disk | MorletDB | 33% smaller than QuestDB's best (Parquet zstd 9); 87% smaller than native | lossless grid coding (64 kHz, dataset-dependent) and implicit timing; also smaller off-grid at 4 kHz |
| Whole volume after load | QuestDB Parquet zstd 9 | 33% smaller | PostgreSQL's retained WAL segments |
| Compression without a separate step | MorletDB | — | QuestDB OSS needs an explicit Parquet conversion (never the newest partition) |
| Altered retries | MorletDB refuses; QuestDB replaces | semantic | append-at-offset vs dedup upsert |
| Ingestion rate | QuestDB | 13–15× | client serialisation of Python floats in MorletDB's importer path (by elimination) |
| Server memory during ingestion | MorletDB | 11–14× less | — |
| Short exact reads (4,096) | none vs native; MorletDB vs Parquet arm | — | per-query overhead dominates |
| Long exact reads (128 k–192 k) | QuestDB | 2–3× | client-side decoding of PostgreSQL arrays; 32,768-sample pieces |
| Interval analytics | QuestDB | 3–6× | row-at-a-time SQL aggregation vs vectorised columns |
| Similarity search | MorletDB only | — | not compared (E1–E6) |

## Recommendation for the paper

- Report B3 as a trade-off table, not a ranking, with the correctness results and the retry semantics.
- The storage claim may be made only with its condition: "on signals sampled on a value grid, MorletDB stored
  the samples in 33% less space than QuestDB's Parquet zstd 9 arm and 87% less than its native columns; without
  the grid it was larger". State the whole-volume result next to it.
- State plainly that QuestDB ingests 13–15× faster and reads long intervals 2–3× faster here, with the
  attribution to MorletDB's client path; do not present the attribution as a fix.
- Do not claim read or ingestion advantages for MorletDB. Short reads are a tie under the frozen rule.
- Engineering follow-ups suggested by the attribution (each would need its own frozen measurement): a binary
  ingest path, a binary read function returning raw float64, and a native interval-statistics function.

## Reproduce

```sh
uv venv .venv-b3 --python 3.13
uv pip install --python .venv-b3 -r evaluation/tsdb_b3/requirements.txt
cd evaluation/tsdb_b3
../../.venv-b3/bin/python workload.py manifest          # needs the Paderborn copy under data/paderborn
../../.venv-b3/bin/python run.py subset                 # correctness and retry semantics, both systems
../../.venv-b3/bin/python run.py load questdb D2 1      # and so on: {questdb, morletdb} x {D1, D2} x {1, 2, 3}
../../.venv-b3/bin/python run.py load morletdb-search D2 1
../../.venv-b3/bin/python packed.py
../../.venv-b3/bin/python summarize.py                  # tables and results/summary.json
```

Attribution runs (not part of the comparison): `run.py load morletdb-batched16 D2 1|2`,
`run.py load morletdb-noquantum D2 1`, `diag_reads.py`. Docker volumes are created and removed per load;
every database password is generated per process and never written.

---

# B3.1: MorletDB client path and SQL 0.3.3

Amendment frozen in [PROTOCOL.md](PROTOCOL.md#amendment-b31-morletdb-client-path-and-sql-033-frozen-before-its-confirmation-run)
before this run; developed on the disjoint development set ([`results/dev/`](results/dev/)), confirmed once
on the benchmark set ([`results/b31/`](results/b31/)), with QuestDB rerun unchanged in the same session. Image
`morletdb:b31-dev` (local build of the amendment's commit, image id `sha256:82581eb0e221…`).

**What changed in MorletDB** (all lossless; existing SQL functions unchanged):
- **Client:** numpy chunks are sent in binary rather than formatted sample by sample in Python, and on SQL
  0.3.3 as raw float64 bytes (`morlet_append_chunk_raw`, which calls `morlet_append_chunk`).
- **Reads:** `morlet_read_interval_raw` returns little-endian float64 bytes in one call per interval.
- **Analytics:** `morlet_interval_stats` computes count, min, max, mean and RMS while decoding.
- **Codec:** a faster decoder bit reader, and a faster encoder search that writes byte-identical chunks
  (encode 154 → 61 ns per sample).

## Acceptance criteria (frozen; M-031, benchmark set)

| # | Criterion | Result | Met |
|---|---|---|---|
| 1 | every correctness check passes; analytics within 1e-12 | all 13 loads pass; largest difference 3.5e-16 | yes |
| 2 | sample-data storage equal to stage 1 within 0.1% | 156.75 vs 156.71 MiB; per-group encoded bytes identical | yes |
| 3 | ingestion D2 median ≥ 2× stage 1 (≥ 1.06 M samples/s) | 2.27 M samples/s (4.3×) | yes |
| 4 | R2 p50 ≤ 12 ms | 11.43 ms | yes (narrowly) |
| 5 | analytics p50 ≤ 2× QuestDB-native p50, same session | 3.93 vs 4.16 ms | yes |

**Verdict: adopt** (SQL 0.3.3 and the client path; PR separate from this benchmark).

## Same-session comparison (benchmark set)

Ingestion, three fresh loads per class, median [range]:

| System | Class | Samples/s | Ack per acquisition p50 | Server CPU mean | Peak memory |
|---|---|---:|---:|---:|---:|
| QuestDB | D1 | 6.56 M [6.11–6.68] | 119 ms | 277% | 2,095 MiB |
| QuestDB | D2 | 5.73 M [5.70–8.23] | 122 ms | 307% | 1,966 MiB |
| MorletDB (stage 1) | D2 | 0.53 M [0.52–0.56] | 1,558 ms | 28% | 169 MiB |
| **MorletDB B3.1** | D1 | 2.22 M [2.11–2.26] | 363 ms | 53% | 168 MiB |
| **MorletDB B3.1** | D2 | 2.27 M [2.24–2.31] | 354 ms | 54% | 168 MiB |
| MorletDB B3.1, 16,384-sample chunks (1 load) | D2 | 3.55 M | | | |

Reads (D2 load 1, p50 / p95 ms; R5 with throughput) and analytics:

| Query | QuestDB native | MorletDB B3.1 | Statement (≥ 10% in p50 and p95) | MorletDB B3.1, 16,384 chunks |
|---|---:|---:|---|---:|
| R1 4,096 | 1.93 / 5.89 | **0.98 / 1.51** | MorletDB faster | 0.88 / 1.29 |
| R2 128,000 | 10.73 / 20.11 | 11.43 / 14.36 | none (p50 within 10%) | 7.85 / 9.73 |
| R3 3 × 64,000 | **15.42** / 23.41 | 20.58 / 25.93 | none (QuestDB 25% faster at p50, within 10% at p95) | 13.68 / 17.21 |
| R4 recent | 1.25 / 3.18 | 1.42 / 2.71 | none (split) | 0.99 / 1.46 |
| R4 older | 1.14 / 2.13 | **0.91 / 1.52** | MorletDB faster | 0.94 / 1.39 |
| R5 4 threads | 1.81 / 9.22, 1,364 q/s | **1.42 / 3.06, 2,366 q/s** | MorletDB faster | 1.32 / 2.28, 2,701 q/s |
| Analytics (R2 intervals) | 4.16 / 8.02 | 3.93 / 4.80 | none (p50 within 10%) | 3.67 / 4.03 |

Storage is unchanged from stage 1 for the recommended configuration (byte-identical chunks); 16,384-sample
chunks store 7.5% less sample data (145.0 vs 156.8 MiB).

**Session variance.** This session's QuestDB numbers are slower and more variable than stage 1 (D2 ingestion
5.7–8.2 M vs 7.2–7.8 M samples/s; R1 p50 1.93 vs 1.21 ms; R2 10.7 vs 8.4 ms) on the same image and
configuration; the protocol compares within a session, so the statements above use this session only.
Against stage 1's QuestDB numbers the conclusions change in one place: R2 becomes "QuestDB faster at p50
(27%), equal at p95" (still no statement), and R1, R4-older and R5 remain MorletDB advantages. QuestDB's WAL
directories held 11–320 MiB after the settled-state check, depending on when its asynchronous purge ran;
its data, timestamps and metadata were byte-identical across sessions.

## Updated trade-off map

| Dimension | Stage 1 | B3.1 |
|---|---|---|
| Sample data on disk | MorletDB 33% smaller than Q-parquet-zstd9 | unchanged |
| Ingestion, one client connection | QuestDB 13–15× | **QuestDB 2.5–3.7×** (1.6–2.3× with 16,384-sample chunks, one load) |
| Server memory during ingestion | MorletDB 11–14× less | unchanged (168 vs 1,850–2,300 MiB) |
| Short exact reads | tie | **MorletDB faster** (R1, R4 older); R4 recent tie |
| Long exact reads | QuestDB 2–3× | **tie** (R2); QuestDB 25% at p50 for 3 × 64,000 (R3) |
| Concurrent short reads (R5) | tie | **MorletDB faster**, 73% more queries/s |
| Interval analytics | QuestDB 3–6× | **tie** |

**Attribution:**
- **Ingestion.** Most of the gain is the client: binary transfer removed Python's per-sample formatting
  (0.51 → 1.84 M samples/s on development). The rest is server-side: the faster encoder, and raw bytes that
  skip PostgreSQL's array parsing (→ 2.42 M on development).
- **Remaining ingestion cost.** About 2 ms per 4,096-sample call ([`diag_append.json`](results/dev/diag_append.json)).
  Of that, the old encoder took about 0.8 ms (now about 0.25 ms), parameter receive and parsing about 0.6 ms
  (raw bytes about 0.15 ms), and plpgsql, insert, checkpoint and commit about 0.9 ms. Larger chunks amortise
  that fixed part.
- **Reads.** They no longer decode PostgreSQL arrays in Python.
- **Server time for a 128,000-sample read.** About 4.7 ms, mostly decoding
  ([`diag_server_read.json`](results/dev/diag_server_read.json)).
- **Analytics.** These now run in one native pass.

**Recommendation.**
- **Default:** keep 4,096-sample chunks; they read short intervals fastest.
- **Bulk archives:** document 16,384-sample chunks as an option. It gives faster ingestion and 7.5% less
  storage. On development its reads were slower; in this session they were faster. That disagreement
  needs its own measurement before any claim.

---

# B3.2: pipelined appends, zero-copy array I/O, faster codec, parallel track

Amendment frozen in [PROTOCOL.md](PROTOCOL.md) before this run; development on the disjoint development set
([`results/dev/`](results/dev/)); confirmation once on the benchmark set ([`results/b32/`](results/b32/)),
QuestDB rerun unchanged in the same session. Image `morletdb:b32-dev` (local build of the amendment's commit,
image id `sha256:479987326f5f…`). 31 loads, every one passing full correctness.

## Acceptance criteria (frozen; M-032, benchmark set)

| # | Criterion | Result | Met |
|---|---|---|---|
| 1 | every correctness check passes; analytics within 1e-12 | all 31 loads pass; 3.5e-16 | yes |
| 2 | sample-data storage equal to stage 1 within 0.1% | 156.75 MiB; chunks byte-identical | yes |
| 3 | single-connection ingestion (D2 median) ≥ 3.4 M samples/s | **5.56 M** (2.4× B3.1, 10.5× stage 1) | yes |
| 4 | R1–R5 and analytics p50 each ≤ 1.1× B3.1 | all faster: R1 0.65 (0.98), R2 7.98 (11.43), R3 10.68 (20.58), R4 0.68/0.69 (1.42/0.91), R5 1.38 (1.42), analytics 2.70 (3.93) ms | yes |

**Verdict: adopt.**

## Track A: recommended configuration, one client connection (same session)

| Measure | MorletDB B3.2 | QuestDB 10.1 | Statement (≥ 10%) |
|---|---:|---:|---|
| Ingestion D2, median [range] | 5.56 M [5.45–5.84] samples/s | 8.29 M [7.85–8.78] | QuestDB 1.3–1.6× faster |
| Ingestion D1 | 5.69 M [5.32–5.72] | 7.01 M [6.94–7.40] | QuestDB 1.2–1.4× faster |
| Ack per acquisition, p50 (D2) | 144 ms | 95 ms | |
| Server memory, peak | 168 MiB | 2,138–2,306 MiB | MorletDB 13× less |
| R1 4,096 samples, p50 / p95 | **0.65 / 1.04 ms** | 1.16 / 2.66 | MorletDB faster (1.8× / 2.6×) |
| R2 128,000 samples | 7.98 / 11.64 | 7.71 / 13.31 | none (p50 within 4%; MorletDB 13% faster at p95) |
| R3 3 × 64,000 samples | **10.68 / 14.62** | 12.52 / 19.11 | MorletDB faster (15% / 24%) |
| R4 recent | **0.68 / 1.20** | 0.87 / 1.44 | MorletDB faster |
| R4 older | **0.69 / 1.21** | 0.88 / 1.71 | MorletDB faster |
| R5 4 threads | 1.38 / 2.76 ms, 2,481 q/s | 1.32 / 5.61 ms, 2,036 q/s | none on latency (p50 within 5%); 22% more queries/s |
| Analytics (R2 intervals) | **2.70 / 2.99** | 4.02 / 8.09 | MorletDB faster (1.5× / 2.7×) |
| Sample data on disk | **156.75 MiB** | 232.7 (Parquet zstd 9) / 1,162.7 (native) | MorletDB 33% / 87% smaller |
| Whole volume, with the WAL option | 240.6 MiB | 247.3 (Parquet zstd 9) | none (3% smaller) |

QuestDB's storage is taken from stage 1 (its data files were byte-identical in every session); its Parquet
arms were not rerun. The WAL option (`min_wal_size=32MB`, `max_wal_size=48MB`) did not change ingestion
(5.85 M samples/s) and leaves 48 MiB of WAL instead of 176 MiB; it checkpoints more often, which costs extra
writes under sustained load and shortens crash recovery.

## Track B: maximum throughput, parallel writers (both systems treated alike)

| Writers, class | MorletDB B3.2 | QuestDB 10.1 | Statement (all three loads) |
|---|---:|---:|---|
| 4, D2 | **13.46 M [13.28–14.48]** samples/s, 200–294% CPU | 10.69 M [10.11–11.10], 628–668% CPU | MorletDB 1.2–1.4× faster |
| 8, D2 | **16.47 M [14.36–16.57]**, 274–391% CPU | 10.10 M [9.12–10.70], 604–711% CPU | MorletDB 1.3–1.8× faster |
| 8, D1 | **15.46 M [14.90–16.79]**, 361–378% CPU | 8.42 M [8.37–8.42], 655–712% CPU | MorletDB 1.8–2.0× faster |

Server memory with 8 writers: MorletDB 184–196 MiB, QuestDB 2,429–2,855 MiB. Data was queryable 6–7 ms
(MorletDB) and 70–90 ms (QuestDB, WAL apply) after the last acknowledgement. QuestDB used most of its 8-CPU
limit from 4 writers on; MorletDB stayed below 4 cores, so its ceiling on this machine was not reached.

## What changed and why it is faster

- **Pipelined appends** (client): the importer no longer waits for each statement's result; errors surface at
  the next commit, which covers the same chunks. Single connection 2.4 → 5.8 M samples/s on development.
- **Zero-copy array I/O** (server): append-path functions borrow the `float8[]` input instead of copying it
  element by element, and results are built in one call (pgrx built them one element at a time).
- **Codec** (server; output byte-identical): Rice codes read from one 64-bit window, prediction in a second
  pass with the order fixed per chunk, slicing-by-8 CRC-32C; per-order residual loops in the encoder. Real
  vibration chunks: decode 19.8 → 10.4 ns/sample, encode 72 → 54 ns/sample.
- **Multi-channel reads in one statement** (client): one round trip for R3.

## Updated trade-off map (MorletDB vs QuestDB, this machine and dataset)

| Dimension | Stage 1 | B3.1 | **B3.2** |
|---|---|---|---|
| Sample data on disk | MorletDB 33% smaller | unchanged | unchanged |
| Ingestion, one connection | QuestDB 13–15× | QuestDB 2.5–3.7× | **QuestDB 1.2–1.6×** |
| Ingestion, parallel writers | not measured | not measured | **MorletDB 1.2–2.0×** |
| Server memory | MorletDB 11–14× less | unchanged | unchanged (13×) |
| Short exact reads | tie | MorletDB faster | **MorletDB 1.8× / 2.6×** |
| Long exact reads (128k) | QuestDB 2–3× | tie | tie |
| Multi-channel reads (3 × 64k) | QuestDB faster | QuestDB 25% at p50 | **MorletDB faster** |
| Interval analytics | QuestDB 3–6× | tie | **MorletDB 1.5× / 2.7×** |
| Altered retries | refused vs replaced | unchanged | unchanged |

# Amendment B3.3: acquisition reads, and the cost of search at ingest

Amendment frozen in [PROTOCOL.md](PROTOCOL.md) (commit `bf8d3fe`) before this run; development on the
development set only ([`results/dev/b33*`](results/dev/)); confirmation once on the benchmark set
([`results/b33/`](results/b33/), run log [`results/b33/run.log`](results/b33/run.log)), restarted unchanged
after an aborted first attempt (deviation 1). Image `morletdb:b33` (`sha256:b36cf02aa055…`), reference image
`morletdb:b33-dev` (`sha256:2eb9102ebcc8…`). 17 loads, every one passing full correctness (868 recordings,
101,586,735 samples, every channel read back bit for bit).

## Acceptance criteria (frozen; change 1, benchmark set)

| # | Criterion | Result | Met |
|---|---|---|---|
| 1 | every load passes full correctness | 17 of 17 | yes |
| 2 | every feat and search load on `morletdb:b33` has the features digest of every feat-reference load | all 12 feature loads (incl. the 3 profile loads): 395,835 segments, md5 `514a372e…aa49` | yes |
| 3 | M-033-feat median ingestion ≥ 1.5× the feat-reference median | **1.635 M vs 0.658 M samples/s: 2.49×** | yes |

**Verdict: adopt change 1** (the faster extractor). **Change 2** (`morlet_read_acquisition_raw`) had no
mismatch in 2,000 checked reads: **adopted.**

## Part A: acquisition reads (all 7 channels of one acquisition, 20 intervals × 50, benchmark set)

Registering all 124 acquisitions with their 868 channels took 0.38 s. Every arm returned every sample, first
index and time exactly (0 mismatches in 2,000 reads per arm).

| Arm | AQ1, 64 ms (13,026 samples), p50 / p95 | AQ2, 0.5 s (102,001 samples) |
|---|---:|---:|
| `morlet_read_acquisition` (SQL 0.3.0) | 2.13 / 3.12 ms | 8.32 / 9.51 ms |
| **`morlet_read_acquisition_raw`** (new) | **1.76 / 2.37** | **5.76 / 6.88** |
| `client_raw` (client maps time itself, one statement) | 1.32 / 1.88 | 4.36 / 6.45 |
| QuestDB 10.1 (one query per group table) | 2.86 / 7.39 | 8.41 / 15.61 |

Statements (stage-1 rule, ≥ 10% at both p50 and p95):
- `_raw` is faster than `morlet_read_acquisition`: 17% / 24% (AQ1), 31% / 28% (AQ2).
- `_raw` is faster than QuestDB: 39% / 68% (AQ1), 32% / 56% (AQ2).
- `client_raw` is faster than `_raw` at AQ1 (25% / 21%); at AQ2 no statement (24% at p50, 6% at p95). The
  difference is the server-side time-to-sample mapping and channel lookup of the acquisition API.
- `morlet_read_acquisition` vs QuestDB: faster at AQ1 (26% / 58%); at AQ2 no statement (1% at p50).

## Part B: the cost of search at ingest (D2, one connection, three loads per arm)

| Arm | Ingestion, median [range] | Ack per acquisition, p50 | Server CPU, mean | Server memory, peak | Database |
|---|---:|---:|---:|---:|---:|
| M-033, no features | 6.33 M [6.27–6.33] samples/s | 128 ms | 74% | 168 MiB | 164.8 MiB |
| **M-033-feat**, new extractor | **1.64 M [1.62–1.64]** | 497 ms | 93% | 184 MiB | 358.2 MiB |
| M-033-search, features + envelopes | 1.64 M [1.42–1.64]; envelope build 12.2 s [12.2–14.5] | 497 ms | 91% | 190 MiB | 409.0 MiB |
| M-033-feat, reference extractor | 0.66 M [0.64–0.80] | 1,217 ms | 96% | 180 MiB | 320.2 MiB |

- **Searchable at ingest now costs 3.9× the ingest time of raw storage, down from 9.6×.** Base ingestion
  (6.33 M samples/s) is 14% above B3.2's 5.56 M on the same path; QuestDB's one load in this session reached
  6.42 M, so the base rate is not a statement here.
- Search load 2 was slower (71 s vs 62 s) while its features digest was identical; the median is unaffected.
- **Storage.** Features add 130 MB (segments heap 86.8 MB, indexes 43.2 MB) and envelopes 53 MB; sample data
  is unchanged. The feat database is 39.9 MB larger on `morletdb:b33` than on the reference image, almost all of it
  in the recordings table's TOAST (74.9 vs 35.7 MB; development showed the same, 66 vs 36 MB). The stored
  checkpoints are rewritten on every append; the likeliest cause is that the faster load leaves autovacuum
  less time, so more dead checkpoint versions pile up and plain `VACUUM` frees but does not return the space.
  **Not verified**; it is checked before the next release.

**Attribution** (one extra load per arm with `track_functions=all`; server time, outside every statement;
`results/b33/profile/`):

| Function (self time) | Base | Feat, new | Feat, reference |
|---|---:|---:|---:|
| `morlet_chunk_step` (features) | 0.5 s | **48.9 s** | 117.9 s |
| `morlet_append_chunk` (incl. writing segments) | 6.6 s | 19.1 s | 18.4 s |
| `morlet_encode_chunk` (codec) | 7.1 s | 7.2 s | 6.5 s |
| Load wall time (profiling on) | 24.2 s | 86.5 s | 153.7 s |

The extractor's server time fell 2.4× (development: 2.6×). It remains the largest single cost of searchable
ingestion; writing the segment rows adds about 12.5 s per load. With envelopes, `morlet_envelopes_from_chunks`
took 14.0 s of the 15.7 s build.

## Updated trade-off map (additions)

| Dimension | B3.2 | **B3.3** |
|---|---|---|
| Aligned read of all channels of an acquisition | not measured | **MorletDB `_raw` 32–39% faster at p50, 2.3–3.1× at p95** |
| Searchable at ingest (features), one connection | 0.30 M (stage 1, old path) | **1.64 M samples/s** (2.5× the old extractor on the B3.2 path) |
