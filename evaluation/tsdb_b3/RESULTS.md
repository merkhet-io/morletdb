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

# Amendment B3.4: CrateDB

> **Superseded in part (B3.9):** this stage's speed ratios compared against MorletDB's B3.3 numbers from another
> session, most likely on a busy machine; the same-session figures are in [B3.9](#amendment-b39-cratedb-iotdb-tdengine-and-timescaledb-re-paired-in-one-session).
> Storage, correctness and semantics stand.

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `6996513`) before any load of the benchmark set;
development on the development set only ([`results/dev/b34/`](results/dev/b34/)); run once on the benchmark set
([`results/b34/`](results/b34/)), 2026-11-04 15:14–19:23. CrateDB 6.4.6 (`crate:6.4.6`, linux/arm64,
single node, 8 GB heap, no replicas), Python client `crate` 2.3.0 over HTTP. MorletDB is not re-run: its
numbers are B3.3's (SQL 0.3.3, `results/b33/`, same machine and container limits). One deviation: C-row D2 r1
spanned a clamshell sleep of the Mac and was rerun from a fresh volume under the stage's own rule; the
interrupted result is kept in `results/b34/interrupted/` and used for nothing.

**Correctness: every one of the 13 CrateDB loads passed** (868 channels read back with the manifest's
SHA-256, sample indexes exact; 0 mismatches in every read benchmark; analytics within 1e-12).

## Ingestion (one client connection, bulk inserts; median [range] of three loads)

| Arm | D1 (fsync per request) | D2 (async) | Ack per acquisition, p50 (D2) | Queryable after the last ack |
|---|---:|---:|---:|---:|
| MorletDB (B3.3, base) | — | **6.33 M** [6.27–6.33] samples/s | 128 ms | immediately (at commit) |
| C-array (blocks of 4,096 as `DOUBLE` arrays) | 404 k [370–404 k] | 425 k [385–450 k] | 1,908 ms | 7.4–13.5 s |
| C-row (row per sample instant) | 237 k [231–344 k] | 245 k [242–264 k] | 3,254 ms | 0.9–1.6 s |
| C-row-best (one load, no statement) | — | 335 k | 2,386 ms | 0.8 s |

**Statement (≥ 10% in all loads): MorletDB ingested about 15× faster** than CrateDB's fastest arm (median
6.33 M vs 425 k; every MorletDB load ≥ 13.9× every CrateDB load). CrateDB used 1.1–2.2 cores for one client (MorletDB 0.74); parallel clients
were not part of the stage. Ingestion time includes building the client's parameter rows (1–8 s per load).

## Storage (D2 r1, settled: `REFRESH`, `OPTIMIZE`, clean stop)

| | MiB | × MorletDB database |
|---|---:|---:|
| MorletDB (B3.3): sample data / database / volume with retained WAL | **148.8** / **164.8** / 363.6 | 1 |
| C-array: data files after `OPTIMIZE` / after `OPTIMIZE` to one segment | 1,785.6 / 1,110.8 | 10.8 / 6.7 |
| C-row-best | 4,507.2 / 3,663.3 | 27 / 22 |
| C-row | 5,243.6 / 3,761.5 | 32 / 23 |
| raw float64 (for scale) | 775 | 4.7 |

CrateDB's most compact layout took 2.3× the raw float64 size after `OPTIMIZE` (1.4× after merging to one
segment). **Statement: MorletDB stored the data in a tenth of CrateDB's smallest arm** (10.8× on the
database; 4.9× against MorletDB's whole volume including PostgreSQL's retained WAL). Translog and cluster
state were below 1 MiB in every arm. Storage options beyond the frozen arms (for example disabling the column
store of the array) were not tried.

## Reads and analytics (D2 r1 of each arm, one connection unless stated; p50 / p95 ms)

| Query | MorletDB (B3.3) | C-array | C-row | C-row-best |
|---|---:|---:|---:|---:|
| R1, 4,096 samples | **0.62 / 0.92** | 10.9 / 19.3 | 8.1 / 14.3 | 7.2 / 11.5 |
| R2, 128,000 samples | **6.6 / 8.9** | 86.6 / 109.9 | 145.6 / 202.6 | 127.3 / 135.2 |
| R3, 3 channels × 64,000 | **8.1 / 10.1** | 167.0 / 190.7 | 122.7 / 159.7 | 98.9 / 105.4 |
| R4, 4,096 samples, recent / older (p50) | **0.60 / 0.59** | 6.9 / 7.1 | 7.8 / 7.3 | 6.2 / 5.8 |
| R5, 4 threads: queries/s | **4,119** | 385 | 177 | 243 |
| min, max, mean, RMS of 128,000 samples | **2.5 / 2.7** | 82.5 / 94.2 (client) | 6.6 / 13.8 | 3.9 / 7.9 |

**Statements:** every read was **8–13× faster** in MorletDB than in CrateDB's fastest arm for that query, at
p50 and p95 (R1–R3 10–13×, R4 8–10×, R5 throughput 10.7×); interval analytics were **35% faster at p50 and 66% at p95** than CrateDB's fastest arm
(C-row-best, one load). The array layout cannot compute statistics over a slice in SQL; it was computed in the
client after the read, as the protocol states.

## Semantics

- An altered resend of an acquisition is **silently ignored** by CrateDB (`ON CONFLICT DO NOTHING`, development
  subset); MorletDB refuses it. An identical resend leaves one copy in both.
- Rows become queryable after a refresh (default interval), not at acknowledgement: up to 13.5 s in C-array.
- CrateDB timestamps have millisecond resolution; at 64 kHz every arm stored the sample index to stay exact,
  as MorletDB does.
- Server memory peaked at 6.0–6.1 GiB (C-array) and 8.7–9.0 GiB (C-row), with the documented 8 GB heap; MorletDB 167 MiB.
  This reflects the configuration, not a minimum.

## Scope

One dataset (Paderborn, 101.6 M samples), one machine, single-node CrateDB at image defaults apart from the
frozen settings, one client connection, the official Python client over HTTP. CrateDB's vector search and
clusters were not tested; similarity search was not part of B3.

## Updated trade-off map (additions)

| Dimension | QuestDB (B3.2/B3.3) | **CrateDB 6.4 (B3.4)** |
|---|---|---|
| Sample data on disk vs MorletDB | 1.5× (Parquet zstd 9) | **10.8× (array), 32× (rows)** |
| Ingestion, one connection | about equal (B3.3 session) | **MorletDB about 15× faster** |
| Short and long exact reads | MorletDB faster or tied | **MorletDB 8–13× faster** |
| Interval statistics | MorletDB faster | **MorletDB 35–66% faster** |
| Altered resend | silently replaces | **silently ignored** |

# Amendment B3.5: Apache IoTDB

> **Superseded in part (B3.9):** this stage's speed ratios compared against MorletDB's B3.3 numbers from another
> session, most likely on a busy machine; the same-session figures are in [B3.9](#amendment-b39-cratedb-iotdb-tdengine-and-timescaledb-re-paired-in-one-session).
> Storage, correctness and semantics stand.

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `fa2c0a0`) before any load of the benchmark set; development on
the development set only ([`results/dev/b35/`](results/dev/b35/)); run once on the benchmark set
([`results/b35/`](results/b35/)), 2026-11-04 20:40–21:04, no deviation (no system sleep in the window, `pmset`
log). Apache IoTDB 2.0.11 standalone (`apache/iotdb:2.0.11-standalone`, linux/arm64), table model, timestamps at
nanosecond precision, Python client `apache-iotdb` 2.0.11. MorletDB is not re-run: its numbers are B3.3's
(SQL 0.3.3, same machine and container limits).

**Correctness: every one of the 12 IoTDB loads passed** (868 channels read back with the manifest's SHA-256,
and every sample's time equal to start + n × period to the nanosecond; 0 mismatches in every read benchmark;
analytics within 1e-12).

## Ingestion (one client connection, `NumpyTablet` inserts; median [range] of three loads)

| Arm | D1 (WAL SYNC) | D2 (WAL ASYNC, default) | Ack per acquisition, p50 (D2) | Count complete after the last ack |
|---|---:|---:|---:|---:|
| MorletDB (B3.3, base) | — | **6.33 M** [6.27–6.33] samples/s | 128 ms | at commit |
| I-table (GORILLA + LZ4, defaults) | 2.09 M [1.95–2.15 M] | 2.55 M [2.35–2.56 M] | 311 ms | 0.6–0.7 s |
| I-table-zstd | 1.99 M [1.97–2.12 M] | 2.37 M [2.07–2.38 M] | 332 ms | 1.2–1.5 s |

**Statement (≥ 10% in all loads): MorletDB ingested about 2.5× faster** than IoTDB's faster arm (every MorletDB
load ≥ 2.45× every IoTDB load).
"Count complete" includes the time of the counting query itself; rows were readable without an explicit flush.

## Storage (D2 r1, settled: `FLUSH`, data directory unchanged for 60 s, clean stop)

| | MiB | × MorletDB database |
|---|---:|---:|
| MorletDB (B3.3): sample data / database / volume with retained WAL | **148.8** / **164.8** / 363.6 | 1 |
| I-table-zstd: volume (TsFiles 655.9) | 660.0 | 4.0 |
| I-table: volume (TsFiles 765.9) | 770.0 | 4.7 |
| raw float64 (for scale) | 775 | 4.7 |

IoTDB's default arm stored the data at about the raw float64 size, its ZSTD arm at 85% of it: GORILLA (XOR
coding) gains almost nothing on these noisy waveforms. **Statement: MorletDB stored the data in a quarter of
IoTDB's smaller arm** (4.0× on the database; 1.8× against MorletDB's whole volume including retained WAL).

## Reads and analytics (D2 r1 of each arm; p50 / p95 ms)

| Query | MorletDB (B3.3) | I-table | I-table-zstd |
|---|---:|---:|---:|
| R1, 4,096 samples | **0.62 / 0.92** | 7.8 / 10.5 | 8.3 / 11.3 |
| R2, 128,000 samples | **6.6 / 8.9** | 46.0 / 58.9 | 46.1 / 55.4 |
| R3, 3 channels × 64,000 | **8.1 / 10.1** | 36.0 / 52.0 | 35.6 / 45.2 |
| R4, 4,096 samples, recent / older (p50) | **0.60 / 0.59** | 4.8 / 5.0 | 5.3 / 4.9 |
| R5, 4 threads: queries/s | **4,119** | 422 | 435 |
| min, max, mean, RMS of 128,000 samples (SQL) | **2.5 / 2.7** | 7.4 / 11.1 | 8.8 / 12.2 |

**Statements:** every read was **4.4–12.7× faster** in MorletDB than in IoTDB's faster arm for that query, at p50
and p95 (R1 11–13×, R2 6–7×, R3 4.4–4.5×, R4 7–8×, R5 throughput 9.5×); interval analytics were **3.0× faster at
p50 and 4.2× at p95**. IoTDB's reads select the time as `INT64`, because its Python client converts a TIMESTAMP
column element by element (10× slower in development; protocol). Part of IoTDB's read time is the Python
client's decoding; the server answered a 128,000-sample query in about 20 ms in development.

## Semantics

- An altered resend **silently replaces** the stored value (a write of the same time and tag overwrites; subset
  check in development), like QuestDB; CrateDB ignores it; MorletDB refuses it.
- Times are stored exactly at nanosecond precision (configured at first start; the image default is
  milliseconds).
- Two JVMs (ConfigNode and DataNode) sized from the container limit (3.9 and 6.4 GiB heap); server memory peaked
  at 4.5–6.3 GiB. This reflects the configuration, not a minimum. MorletDB: 167 MiB.
- The table model accepts no per-column encoding; similarity search is not part of IoTDB (its optional UDF
  library computes DTW or cross-correlation between two given series, without an index over an archive).

## Scope

One dataset (Paderborn, 101.6 M samples), one machine, standalone IoTDB at image defaults apart from the frozen
settings, one client connection, the official Python client. IoTDB's tree model, clusters and AINode were not
tested; similarity search was not part of B3.

## Updated trade-off map (additions)

| Dimension | CrateDB 6.4 (B3.4) | **Apache IoTDB 2.0 (B3.5)** |
|---|---|---|
| Data on disk vs MorletDB | 10.8× | **4.0× (ZSTD), 4.7× (default)** |
| Ingestion, one connection | MorletDB about 15× faster | **MorletDB about 2.5× faster** |
| Exact reads | MorletDB 8–13× faster | **MorletDB 4.4–12.7× faster** |
| Interval statistics | MorletDB 35–66% faster | **MorletDB 3.0–4.2× faster** |
| Exact time at 64 kHz | via a stored sample index | **native (nanosecond precision)** |
| Altered resend | silently ignored | **silently replaces** |

# Amendment B3.6: TDengine

> **Superseded in part (B3.9):** this stage's speed ratios compared against MorletDB's B3.3 numbers from another
> session, most likely on a busy machine; the same-session figures are in [B3.9](#amendment-b39-cratedb-iotdb-tdengine-and-timescaledb-re-paired-in-one-session).
> Storage, correctness and semantics stand.

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `6a90427`) before any load of the benchmark set; development on
the development set only ([`results/dev/b36/`](results/dev/b36/)); run once on the benchmark set
([`results/b36/`](results/b36/)), 2026-11-04 22:37–23:13, no system sleep in the window. TDengine TSDB-OSS 3.4.3.3
(`tdengine/tsdb:3.4.3.3`, linux/arm64), single node, supertables, nanosecond precision, Python WebSocket client
`taos-ws-py` 0.7.0. MorletDB is not re-run: its numbers are B3.3's (SQL 0.3.3, same machine and limits). One
deviation: the tsz arm is lossy (below).

**Correctness.** The default (bss + lz4) and zstd (bss + zstd high) arms passed all 12 loads (868 channels read
back with the manifest's SHA-256, every time exact to the nanosecond; 0 mismatches in every read benchmark;
analytics within 1e-12). **The tsz arm failed**: in its first load two vibration channels differed in 7 and 47
samples near the end of the recording, by at most 8 ULP (5.6e-17). TDengine's taosd reference documents
`dPrecision` (default and minimum 1e-16) as the mantissa truncation of TSZ, so tsz is error-bounded lossy
compression that cannot be made lossless; the development set happened to show no difference. The tsz arm makes
no statement and its remaining loads were not run (deviation 1).

## Ingestion (one client connection, stmt2 binding; median [range] of three loads)

| Arm | D1 (WAL fsync per write) | D2 (default WAL) | Ack per acquisition, p50 (D2) | Count complete after the last ack |
|---|---:|---:|---:|---:|
| MorletDB (B3.3, base) | — | **6.33 M** [6.27–6.33] samples/s | 128 ms | at commit |
| T-default (bss + lz4) | 3.94 M [3.93–4.00 M] | 4.08 M [3.90–4.13 M] | 195 ms | 0.26–0.29 s |
| T-zstd (bss + zstd high) | 0.89 M [0.79–0.90 M] | 0.84 M [0.80–0.87 M] | 219 ms | 0.59–0.69 s |

**Statement (≥ 10% in all loads): MorletDB ingested about 1.5× faster** than TDengine's faster arm (every MorletDB
load ≥ 1.52× every TDengine load). TDengine is the fastest-ingesting of the three IIoT databases in B3, and its
fsync-per-write class was within 4% of its default. The zstd-high arm used about 3.5 cores and ingested 4.8×
slower than its default arm.

## Storage (D2 r1, settled: `FLUSH DATABASE`, files outside the WAL unchanged for 60 s, clean stop)

| | MiB | × MorletDB |
|---|---:|---:|
| MorletDB (B3.3): sample data / database / volume with retained WAL | **148.8** / **164.8** / 363.6 | 1 |
| T-zstd: data files (`tsdb`) / whole volume with retained WAL (1,034.5) | 566.7 / 1,602.3 | 3.4 (database) / 4.4 (volume) |
| T-default: data files / whole volume | 626.6 / 1,662.1 | 3.8 / 4.6 |
| raw float64 (for scale) | 775 | 4.7 |

**Statement: MorletDB stored the data in less than a third of TDengine's smaller arm** (3.4× on data files
against MorletDB's database; 4.4× whole volume against whole volume, both with their retained WAL). TDengine
retains about 1 GiB of WAL by default; `COMPACT` is not available in the open-source edition.

## Reads and analytics (D2 r1 of each arm; p50 / p95 ms)

| Query | MorletDB (B3.3) | T-default | T-zstd |
|---|---:|---:|---:|
| R1, 4,096 samples | **0.62 / 0.92** | 5.4 / 8.6 | 5.4 / 9.4 |
| R2, 128,000 samples | **6.6 / 8.9** | 70.5 / 86.3 | 72.7 / 91.1 |
| R3, 3 channels × 64,000 | **8.1 / 10.1** | 57.0 / 75.5 | 62.0 / 101.5 |
| R4, 4,096 samples, recent / older (p50) | **0.60 / 0.59** | 5.5 / 5.0 | 5.4 / 6.0 |
| R5, 4 threads: queries/s | **4,119** | 175 | 165 |
| min, max, mean, RMS of 128,000 samples (SQL) | **2.5 / 2.7** | 5.0 / 11.8 | 6.9 / 14.2 |

**Statements:** every read was **7–11× faster** in MorletDB than in TDengine's faster arm for that query, at p50
and p95 (R1 9×, R2 about 10×, R3 7×, R4 7–9×), and concurrent reads reached 23× the throughput; interval analytics
were **2.0× faster at p50 and 4.4× at p95**. As with IoTDB, part of TDengine's read time is the Python client:
the WebSocket connector returns rows as Python tuples, and time is read as an integer because TIMESTAMP values come
back as text without fractions of a second.

## Semantics

- An altered resend **silently replaces** the stored value (subset check in development), like IoTDB and
  QuestDB; CrateDB ignores it; MorletDB refuses it.
- Times are exact at nanosecond precision (database option).
- Server memory peaked at about 1.0–1.1 GiB, the lowest of the IIoT databases in B3 (MorletDB: 167 MiB).
- Similarity search is not part of TDengine (its TDgpt agent targets forecasting and anomaly detection).

## Scope

One dataset, one machine, single node at image defaults apart from the frozen settings, one client connection,
the official Python WebSocket client. Clusters, the enterprise edition, TDgpt and the native connector were not
tested.

## Updated trade-off map (additions)

| Dimension | Apache IoTDB 2.0 (B3.5) | **TDengine 3.4 (B3.6)** |
|---|---|---|
| Data on disk vs MorletDB | 4.0× | **3.4× (data files), 4.4× (whole volume)** |
| Ingestion, one connection | MorletDB about 2.5× faster | **MorletDB about 1.5× faster** |
| Exact reads | MorletDB 4.4–13× faster | **MorletDB 7–11× faster** |
| Interval statistics | MorletDB 3.0–4.2× faster | **MorletDB 2.0–4.4× faster** |
| Lossless float compression | GORILLA, LZ4, ZSTD all lossless | **tsz is lossy (1e-16 bound)**; bss + lz4 / zstd lossless |
| Altered resend | silently replaces | **silently replaces** |

# Amendment B3.7: TimescaleDB

> **Superseded in part (B3.9):** this stage's speed ratios compared against MorletDB's B3.3 numbers from another
> session, most likely on a busy machine; the same-session figures are in [B3.9](#amendment-b39-cratedb-iotdb-tdengine-and-timescaledb-re-paired-in-one-session).
> Storage, correctness and semantics stand.

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `9953afd`) before any load of the benchmark set; development on
the development set only ([`results/dev/b37/`](results/dev/b37/)); run once on the benchmark set
([`results/b37/`](results/b37/)), 2026-11-04 23:52 to 2026-11-05 00:16, no system sleep in the window, no
deviation. TimescaleDB 2.31.0 on PostgreSQL 17.11 (`timescale/timescaledb:2.31.0-pg17`, linux/arm64), PostgreSQL
defaults as for MorletDB, integer time in nanoseconds, binary COPY through psycopg. MorletDB is not re-run: its
numbers are B3.3's (SQL 0.3.3, PostgreSQL 17, same machine and limits).

**Correctness: all 6 loads passed, in both states** (rowstore after ingestion and columnstore after compression:
868 channels read back with the manifest's SHA-256, every time exact to the nanosecond; 0 mismatches in every
read benchmark; analytics within 1e-12).

## Ingestion (one client connection, binary COPY; median [range] of three loads)

| | D1 (synchronous commit) | D2 (asynchronous commit) | Ack per acquisition, p50 (D2) | Count complete after the last ack |
|---|---:|---:|---:|---:|
| MorletDB (B3.3, base) | — | **6.33 M** [6.27–6.33] samples/s | 128 ms | at commit |
| TimescaleDB (rowstore) | 0.75 M [0.74–0.77 M] | 0.75 M [0.72–0.77 M] | 1,145 ms | 0.8–0.9 s |

**Statement (≥ 10% in all loads): MorletDB ingested about 8.5× faster** (every MorletDB load ≥ 8.2× every
TimescaleDB load). The server used about one core, and a vectorised COPY stream gave the same rate in
development, so the limit is the server's row ingestion with its unique index, not the client. Compressing the
3 chunks into the columnstore afterwards took 33 s.

## Storage (D2 r1, after VACUUM ANALYZE)

| | MiB | × MorletDB database |
|---|---:|---:|
| MorletDB (B3.3): sample data / database / volume with retained WAL | **148.8** / **164.8** / 363.6 | 1 |
| TimescaleDB columnstore: hypertables / database / volume with WAL (1,024) | 806.1 / 815.4 / 1,865.2 | 4.9 (database) |
| TimescaleDB rowstore: hypertables / database | 6,582.9 / 6,592.1 | 40 |
| raw float64 (for scale) | 775 | 4.7 |

**Statement: MorletDB stored the data in about a fifth of TimescaleDB's compressed size** (4.9×, database
against database, both PostgreSQL 17 with default settings). TimescaleDB's columnstore came out at about the raw
float64 size: its float compression (Gorilla) gains nothing on these noisy calibrated values, as P2 had found on
four signals. Fresh data in the rowstore needs 40× MorletDB's space until it is compressed.

## Reads and analytics (D2 r1, both states; p50 / p95 ms)

| Query | MorletDB (B3.3) | TS rowstore | TS columnstore |
|---|---:|---:|---:|
| R1, 4,096 samples | **0.62 / 0.92** | 2.9 / 4.0 | 2.8 / 4.5 |
| R2, 128,000 samples | **6.6 / 8.9** | 67.1 / 78.0 | 59.3 / 73.5 |
| R3, 3 channels × 64,000 | **8.1 / 10.1** | 51.5 / 68.1 | 49.2 / 66.5 |
| R4, 4,096 samples, recent / older (p50) | **0.60 / 0.59** | 2.9 / 3.1 | 3.0 / 2.8 |
| R5, 4 threads: queries/s | **4,119** | 563 | 687 |
| min, max, mean, RMS of 128,000 samples (SQL) | **2.5 / 2.7** | 18.8 / 24.0 | 12.8 / 15.5 |

**Statements:** every read was **4.3–9× faster** in MorletDB than in TimescaleDB's faster state for that query, at
p50 and p95 (R1 4.3–4.6×, R2 8–9×, R3 6–7×, R4 4.6–4.9×), with 6× the concurrent throughput; interval analytics
were **5.1× faster at p50 and 5.8× at p95**. Both are PostgreSQL extensions on the same server version, but they
return data differently: MorletDB returns an interval as one value of raw float64 bytes, TimescaleDB returns one
row per sample instant, which psycopg converts to Python objects; part of TimescaleDB's read time is that
client-side conversion.

## Semantics

- Retries: with the unique index, **both** an identical and an altered resend are refused with a unique
  violation and leave stored values unchanged. MorletDB accepts an identical resend as a no-op and refuses an
  altered one.
- Times exact at nanosecond resolution through an integer time dimension (PostgreSQL timestamps resolve
  microseconds only, too coarse for 64 kHz).
- Server memory peaked at about 394 MiB (MorletDB 167 MiB), with PostgreSQL's default 128 MB shared buffers in
  both.
- Similarity search is not part of TimescaleDB; vector search needs embeddings computed elsewhere (pgvector).

## Scope

One dataset, one machine, PostgreSQL defaults, one client connection, binary COPY through psycopg. Continuous
aggregates, columnstore writes on ingest, tiered storage and TimescaleDB Cloud were not tested.

## Updated trade-off map (additions)

| Dimension | TDengine 3.4 (B3.6) | **TimescaleDB 2.31 (B3.7)** |
|---|---|---|
| Data on disk vs MorletDB | 3.4× (data files) | **4.9× (columnstore), 40× (rowstore)** |
| Ingestion, one connection | MorletDB about 1.5× faster | **MorletDB about 8.5× faster** |
| Exact reads | MorletDB 7–11× faster | **MorletDB 4.3–9× faster** |
| Interval statistics | MorletDB 2.0–4.4× faster | **MorletDB 5.1–5.8× faster** |
| Server memory | about 1 GiB | **about 394 MiB** |
| Retries | altered resend replaces | **every resend refused (unique index)** |

# Amendment B3.8: QuestDB re-measured beside the released MorletDB

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `89a9180`) before any load of the benchmark set. MorletDB
`ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1` (SQL 0.3.3, arm `morletdb-033`) and QuestDB 10.1.0, alternating in
one session, three loads per durability class. Run three times:

| Session | Results | Window | Machine | Used for |
|---|---|---|---|---|
| 1 | [`results/b38/`](results/b38/) | 2026-11-05 00:39–00:51 | **busy** (see below) | nothing but this finding |
| 2 | [`results/b38q/`](results/b38q/) | 01:08–01:15 | quiet | **every B3.8 figure** |
| 3 | [`results/b38z/`](results/b38z/) | 03:13–03:21 | quiet, after B3.9 | drift check |

**Session 1 ran on a busy machine.** MorletDB ingested at 4.38 M samples/s (D2 median) against 6.33 M in B3.3.
Two attribution loads per image (`results/b38/attribution/`) gave 4.22 and 4.27 M for B3.3's image and 4.31 and
4.37 M for the 0.3.5-alpha.1 release, so the release was not slower; the machine was. Its load average was 9–10:
Messages.app had been spinning at one full core since 2026-11-03 01:40, with GUI processes on top. QuestDB gave
7.40 M in that session, so the busy machine slowed MorletDB's client path more (QuestDB 1.69× faster) than in the
quiet sessions (1.53×). Session 1 is therefore not used; it is why B3.9 exists. Messages.app was quit, and from
session 2 on the runner records the load average and the busiest processes before and after every load
(`results/<run>/machine/`). Before every later load, the busiest processes were the Docker VM (the previous
load's container stopping) and Docker Desktop; two one-off spikes (a Docker Desktop renderer, `duetexpertd`) lasted
less than a snapshot. Messages.app did not return.

**Correctness: all 24 loads of sessions 2 and 3 passed** (every channel read back with the manifest's SHA-256,
counts, reads and analytics as in B3).

## Ingestion (one client connection; median [range] of three loads)

| | D2 (asynchronous) | D1 (durable) | Server memory, median peak |
|---|---:|---:|---:|
| MorletDB 0.3.5-alpha.1 | 6.30 M [5.58–6.35] | 6.15 M [6.15–6.17] | 168 MiB |
| QuestDB 10.1.0 | **9.66 M** [9.64–9.68] | **8.48 M** [8.39–8.55] | 2,301 MiB |
| Session 3 (drift check): MorletDB / QuestDB | 6.29 M / 9.59 M | 6.12 M / 8.52 M | 167 / 2,306 MiB |

**Statement (≥ 10% in all loads): QuestDB ingested from one connection about 1.5× faster (D2) and 1.4× faster
(D1)** (every QuestDB load ≥ 1.52× and 1.36× every MorletDB load). B3.2 had found 1.3–1.6× against MorletDB's
earlier build. MorletDB used 14× less memory. Session 3 reproduced session 2 within 1%, so the machine did not
drift over the night. MorletDB's 6.30 M matches B3.3's 6.33 M: the B3.3 baseline was taken on a quiet machine.

## Storage (D2 r1)

Identical to B3.2 to the byte for QuestDB: native 1,162.7 MiB data (1,192.4 MiB volume), Parquet LZ4_RAW 496.6 MiB
volume, Parquet zstd 9 232.7 MiB data (247.3 MiB volume). MorletDB's database: 164.8 MiB, as in B3.3.
**MorletDB's whole database is 29% smaller than QuestDB's Parquet zstd 9 data (1.4×) and 7.1× smaller than its
native columns.**

## Reads and analytics (D2 r1; p50 / p95 ms)

| Query | MorletDB | QuestDB (native) | Statement (≥ 10% at p50 and p95) |
|---|---:|---:|---|
| R1, 4,096 samples | **0.63 / 1.01** | 1.02 / 2.29 | MorletDB 1.6× / 2.3× faster |
| R2, 128,000 samples | 6.89 / 9.31 | 6.01 / 10.16 | none: QuestDB 13% faster at p50, MorletDB 9% at p95 |
| R3, 3 channels × 64,000 | **7.38 / 10.28** | 8.75 / 12.92 | MorletDB 1.2× / 1.3× faster |
| R4, recent / older (p50) | 0.63 / 0.62 | 0.67 / 0.62 | none |
| R5, 4 threads: queries/s (p95 ms) | **4,192** (1.5) | 2,647 (3.6) | MorletDB 1.6× |
| Interval statistics | 2.54 / 2.69 | 2.46 / 4.73 | none: on par at p50, MorletDB 1.8× at p95 |

Session 3 gave the same picture (R1 1.6× / 2.5×, R2 QuestDB 13% at p50, R5 1.6×).

# Amendment B3.9: CrateDB, IoTDB, TDengine and TimescaleDB re-paired in one session

Frozen in [PROTOCOL.md](PROTOCOL.md) (commit `2543976`, 2026-11-05 01:07; the amendment's text says 01:20, a
clerical slip: the commit precedes every B3.9 load). Run once, 01:15–03:13, on the quiet machine of B3.8 session
2, each system alternating with MorletDB 0.3.5-alpha.1 (arm `morletdb-033`), three loads per durability class,
reads and analytics on D2 r1 of each, results in `results/b39-<system>/`. No load failed and none was rerun.
Every number is reproduced by [`summarize_b39.py`](summarize_b39.py).

**Correctness: all 60 loads passed** (30 MorletDB, 30 others: every sample and time exact, 0 mismatches in every
read benchmark, analytics within 1e-12). Across the five pairs MorletDB's D2 medians were 6.24–6.33 M, so the
machine stayed steady.

**Why it mattered.** B3.4–B3.7 compared each system with MorletDB's B3.3 numbers from another session. The other
systems were faster tonight than in their own stages, by 8% (IoTDB) to 73% (CrateDB arrays) for ingestion, and
their reads had taken 1.0–2.2× as long (p50), while MorletDB matched B3.3 within 4%. Their stages, on 2026-11-04, most likely ran on the machine that
B3.8 session 1 found busy; the clients run on the host, so a busy host slows client-heavy paths most. B3.4–B3.7's
time-based ratios are therefore superseded by the table below; their storage, correctness and semantics findings
stand (storage was re-measured tonight and matched to the MiB).

## Same-session ratios (× MorletDB's figure; > 1 = MorletDB better)

| Measure | QuestDB (B3.8) | CrateDB arrays / rows | IoTDB table | TDengine default | TimescaleDB columnstore |
|---|---:|---:|---:|---:|---:|
| Ingestion D2, medians (all-loads bound) | **0.65** (QuestDB ≥ 1.52×) | 8.6 (8.0) / 17.7 (17.6) | 2.3 (2.3) | 1.18 (1.14) | 6.5 (6.4) |
| Ingestion D1 | **0.73** (QuestDB ≥ 1.36×) | 8.4 (8.3) / 17.6 (17.3) | 2.6 (2.6) | 1.21 (1.20) | 6.4 (6.3) |
| R1, 4,096 samples (p50 / p95) | 1.6 / 2.3 | 8.0 / 7.5 · 12.5 / 12.4 | 10.7 / 9.2 | 6.0 / 5.8 | 2.4 / 2.1 |
| R2, 128,000 samples | 0.87 / 1.09 (none) | 7.1 / 6.4 · 17.7 / 13.8 | 6.0 / 4.9 | 6.8 / 5.6 | 5.4 / 4.5 |
| R3, 3 channels × 64,000 | 1.2 / 1.3 | 13.2 / 10.5 · 12.2 / 9.3 | 3.8 / 3.1 | 4.2 / 3.6 | 4.0 / 3.4 |
| R5, 4 threads, queries/s | 1.6 | 5.2 · 16.7 | 6.7 | 14.9 | 3.8 |
| Interval statistics | 0.97 / 1.76 (none) | 18.9 / 20.4 · 1.4 / 2.9 | 2.6 / 3.2 | 1.5 / 3.9 | 4.1 / 4.2 |
| Server memory, median peak | 13.7 | 36.5 / 55.4 | 35.9 | 6.1 | 2.4 |

TimescaleDB's rowstore (before compression) read 2.1–6.4× slower than MorletDB and computed statistics 5.3–5.5×
slower; its ingestion is the rowstore's (compression into the columnstore then took 25 s). Absolute values per
pair: `python3 evaluation/tsdb_b3/summarize_b39.py`.

**Statements (B3's rule), replacing B3.4–B3.7's time-based ones:**
- **TDengine:** MorletDB ingested about **1.2× faster** (was stated as 1.5×), read 3.6–6.8× faster (was 7–11×),
  15× the concurrent throughput, statistics 1.5× / 3.9× faster.
- **TimescaleDB:** ingestion about **6.5× faster** (was 8.5×), reads 2.1–5.4× faster (was 4.3–9×), statistics
  about 4× faster (was 5.1–5.8×).
- **IoTDB:** ingestion about **2.3–2.6× faster** (was 2.5×), reads 3.1–10.7× faster (was 4.4–13×), statistics
  2.6× / 3.2× faster (was 3.0× / 4.2×).
- **CrateDB:** ingestion about **8.5× faster than its array layout** (was 15×) and 18× faster than its row layout,
  reads 6.4–12× faster than its faster layout per query (was 10–13×), statistics 1.4× / 2.9× faster than its
  row layout (was 1.5× / 3.0× against row-best, not re-run).

## Scope

As each stage. Secondary layouts (TDengine zstd and tsz, IoTDB table-zstd, CrateDB row-best) were not re-run;
their time-based figures in B3.4–B3.6 are cross-session and are no longer quoted. One dataset, one machine.

# Amendment B3.10: single-connection ingestion after I1

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `a388f87`, 2026-11-05 10:07) before any benchmark-set load;
development work in [`../ingest_i1/ATTRIBUTION.md`](../ingest_i1/ATTRIBUTION.md) (issue #233). One session,
2026-11-05 10:08–10:18 (`results/b310/`, `run_b310.sh`, log `results/b310.log`): for r = 1, 2, 3 and D2 then D1,
`morletdb-i1` (image `morletdb:b310`, `sha256:4a617625773a…`, built from `dc77ed1`, SQL 0.3.4 unreleased), QuestDB
10.1.0, `morletdb-033` (released `0.3.5-alpha.1`). The smoke check (one development-set load of `morletdb-i1`,
`results/dev/b310-smoke/`) passed before the run. Summary: `summarize_b310.py`.

**Machine.** 1-minute load average 2.8 before the session, with no foreign process above half a core (the start
condition). Before the loads it was 4.8–9.9 (median 7.9); as in B3.8 (median 7.0, maximum 11.3) this comes from
the session's own previous load, so every load started above 4 by the frozen note rule. Foreign processes above
half a core appeared in six of the 18 pre-load snapshots: a WebKit page (72% before `morletdb-033` D2 r1, 111%
before its D2 r2), Spotlight indexing and media analysis (70–110% before the three D1 r3 loads) and `duetexpertd`
(79% before `morletdb-i1` D1 r3). Snapshots are taken only before and after each load.

**Correctness: all 18 loads passed** (every channel read back against the manifest's SHA-256, counts, reads and
analytics within 1e-12). **Stored chunks of `morletdb-i1` were byte-identical to `morletdb-033`'s in every load**
(25,138 chunks, one digest).

## Ingestion (one client connection; median [range] of three loads)

| | D2 (asynchronous) | D1 (durable) | Server memory, median peak |
|---|---:|---:|---:|
| **MorletDB, I1 (SQL 0.3.4, unreleased)** | **13.65 M** [13.51–13.73] | **12.99 M** [12.71–13.10] | 177 MiB |
| QuestDB 10.1.0 | 9.58 M [9.03–9.67] | 8.26 M [7.14–8.46] | 2,307 MiB |
| MorletDB 0.3.5-alpha.1 | 6.07 M [5.93–6.16] | 6.05 M [5.96–6.14] | 168 MiB |

**Statements (≥ 10% in all loads):**
- **MorletDB with I1 ingested from one connection about 1.4× faster than QuestDB (D2) and 1.6× faster (D1)**
  (every `morletdb-i1` load ≥ 1.40× and ≥ 1.50× every QuestDB load). B3.8 had QuestDB 1.5× / 1.4× faster than the
  released MorletDB, and this session reproduces that (QuestDB ≥ 1.47× / 1.16× `morletdb-033`).
- I1 against the released MorletDB: 2.2× (D2) and 2.1× (D1) (all-loads bounds 2.19 and 2.07).
- MorletDB with I1 used 13× less peak server memory than QuestDB (177 against 2,307 MiB); the encoding threads add
  about 9 MiB over the released build.

## Storage (D2 r1)

Chunk data identical (155.98 MB TOAST, 2.48 MB heap, 2.09 MB indexes for both MorletDB arms). The recordings table
is smaller with I1 (0.63 against 4.06 MB) because the checkpoint row is rewritten once per call instead of once per
chunk, leaving fewer dead versions: database 169.3 against 172.8 MB (−2%), WAL 160 against 176 MiB.

## Reads and analytics (D2 r1 of each arm; p50 / p95 ms)

| Query | MorletDB, I1 | QuestDB (native) | MorletDB 0.3.5-alpha.1 | I1 against QuestDB |
|---|---:|---:|---:|---|
| R1 4,096 samples | 0.66 / 1.14 | 1.12 / 2.60 | 0.64 / 1.05 | MorletDB 1.7× / 2.3× faster |
| R2 128,000 samples | 7.46 / 10.58 | 7.15 / 13.78 | 6.67 / 8.89 | no statement (0.96 / 1.30) |
| R3 3 × 64,000 samples | 7.84 / 10.81 | 9.29 / 14.63 | 7.82 / 11.03 | MorletDB 1.2× / 1.4× faster |
| R4 recent | 0.63 / 0.99 | 0.79 / 1.39 | 0.63 / 0.97 | MorletDB 1.3× / 1.4× faster |
| R4 older | 0.61 / 0.98 | 0.79 / 1.51 | 0.61 / 1.01 | MorletDB 1.3× / 1.5× faster |
| R5 4 threads, queries/s | 4,070 | 2,316 | 4,196 | |
| Interval statistics | 2.54 / 2.66 | 2.49 / 4.70 | 2.55 / 2.66 | no statement (0.98 / 1.77) |

## Acceptance criteria (frozen)

| # | Criterion | Result | Met |
|---|---|---|---|
| 1 | every load of every arm passes B3's correctness checks | 18 of 18 | yes |
| 2 | stored chunks of `morletdb-i1` byte-identical to `morletdb-033`'s in every load | 6 of 6 pairs | yes |
| 3 | R1–R5 and analytics p50 of `morletdb-i1` each ≤ 1.1× `morletdb-033`'s | R2 **1.118**; R1 1.04, R3 1.00, R4 1.00 / 1.00, R5 1.03, analytics 1.00 | **no** |

**Verdict: criterion 3 is not met** (R2 p50 7.46 against 6.67 ms, +11.8%), so by the frozen rule `morletdb-i1` is
not adopted as the recommended configuration from this stage, and the website is not changed. The ingestion
statements above stand as measured.

**Attribution (exploratory, after the stage, not part of it).** The read path is the same code in both arms. On the
development set the same image read R2 faster after an I1 load than after a `morletdb-033` load (6.99 against
8.27 ms p50, `results/dev/b310-diag/`). Two more benchmark-set loads of `morletdb-i1` and one of `morletdb-033` on
`morletdb:b310` (`results/b310-attr-a/`, `-b/`) gave R2 p50 8.42 and 6.94 ms for I1 and 7.34 ms for `morletdb-033`,
while Spotlight and media analysis were running (1-minute load average up to 10.5): load-to-load spread of R2 is
larger than the 11.8% difference, in both directions. This suggests, but does not establish, that the failed
criterion reflects variation between single read measurements rather than a slower read path. A criterion over
several read repetitions would decide it; that needs a new frozen amendment.

# Amendment B3.11: B3.10's read criterion over three loads per arm

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `82b8b6d`, 2026-11-05 10:40) before any load; run 10:47–10:53
(`results/b311/`, `run_b311.sh`, log `results/b311.log`): for r = 1, 2, 3 a fresh D2 load of `morletdb-i1`
(`morletdb:b310`) then of `morletdb-033` (released `0.3.5-alpha.1`), each followed by the full read benchmark.

**Machine.** 1-minute load average 2.8 before the first load (start condition met; Spotlight and media analysis
idle). Between loads 6.7–12.0 from the session itself. Foreign processes above half a core: `clang` at 98% in the
snapshot after `morletdb-033` D2 r2, that is, just before `morletdb-i1` D2 r3 (not started by this work; that
load also ingested slower, 11.0 M against 12.9–13.6 M); WindowServer 68% once. Docker Desktop's backend
(`com.docker.backend`, which carries the client connections into the container) was seen at about 400% during
the run.

**Correctness: all six loads passed; stored chunks byte-identical in all six** (25,138 chunks, one digest).

## Reads and analytics (p50 ms of each load; criterion: median over three loads, `morletdb-i1` ≤ 1.1×)

| Query | `morletdb-i1` (three loads) | median | `morletdb-033` (three loads) | median | ratio | met |
|---|---|---:|---|---:|---:|---|
| R1 | 0.737, 0.638, 0.625 | 0.638 | 0.655, 0.629, 0.641 | 0.641 | 0.995 | yes |
| R2 | 7.150, 7.413, 7.016 | 7.150 | 7.129, 7.047, 6.561 | 7.047 | 1.015 | yes |
| R3 | 9.743, 10.239, 8.412 | 9.743 | 7.695, 9.997, 7.479 | 7.695 | **1.266** | **no** |
| R4 recent | 0.678, 0.690, 0.626 | 0.678 | 0.634, 0.877, 0.631 | 0.634 | 1.069 | yes |
| R4 older | 0.622, 0.634, 0.612 | 0.622 | 0.614, 0.776, 0.610 | 0.614 | 1.013 | yes |
| R5 (4 threads) | 1.010, 1.143, 0.807 | 1.010 | 0.823, 1.367, 0.794 | 0.823 | **1.227** | **no** |
| Interval statistics | 2.641, 2.610, 2.559 | 2.610 | 2.575, 2.784, 2.573 | 2.575 | 1.014 | yes |

p95 medians (reported, not tested): R3 14.96 against 10.71 ms, R5 1.86 against 1.55 ms; all others within 12%.
Ingestion (reported, not tested): `morletdb-i1` 12.94, 13.62, 11.04 M samples/s; `morletdb-033` 5.99, 5.89,
6.37 M. Server peak memory 175–179 against 167–169 MiB.

**Verdict: criterion 3 is not met** (R3 1.266, R5 1.227; R2, which failed in B3.10, now 1.015). By the frozen
decision rule, I1 is not adopted, the website is not changed, and the read difference is investigated as a
defect before any further stage. Two candidate causes are not separated by either stage: the arms differ in the
write path **and** in the build (`morletdb:b310` is a local `deploy/Dockerfile` build; `0.3.5-alpha.1` was built
by the release workflow), and single-load read latencies vary widely here (R3 7.5–10.0 ms and R5 0.79–1.37 ms
within the released arm alone). The investigation will compare the same write path on both builds and repeated
reads on one load.

## Investigation of B3.11's read difference (exploratory, after the stage, not part of it)

Done as B3.11's decision rule requires, on the benchmark set, `results/i1r-*` (scripts `run_i1_reads.sh`,
`summarize_i1_reads.py`, `i1_reads_process.py`, `i1_reads_server.py`; every load correct).

1. **Build versus write path** (11:40–11:55, machine quiet; Safari at about 70% in two snapshots). Three arms in
   rotation, three fresh D2 loads each, B3's read benchmark three times per load (`results/i1r-summary.json`):
   the old write path on the released image, the old path on `morletdb:b310`, and I1 on `morletdb:b310`. Medians of
   nine p50s: **build** (b310 / released, old path) 0.99–1.01 for every query; **write path** (I1 / old, same
   image) R3 1.11, R5 1.11, R2 1.02, others 0.997–1.016. On the old path reads varied 1–4% within and between
   loads; after I1 loads up to 17% within a load and 20% between loads. The build is not the cause.
2. **Same process versus fresh process** (12:00, 1-minute load average 4.8–7 from the test itself). Two loads per
   arm, reads in the ingesting process, in a fresh Python process, and again after `gc.collect()`
   (`results/i1r-process_*.json`). Noise in this run was as large as the effect in both arms (old path R3 7.5–8.9
   ms across the three readings of one load); fresh-process R3 7.53 against 7.59 ms (1.01), R5 1.07.
   Inconclusive.
3. **Inside the server** (12:03–12:06). Two loads per arm; all of B3's planned intervals read in one statement
   per query type (50 passes, `morlet_read_interval_raw`), five rounds (`results/i1r-server_*.json`): microseconds
   per read, I1 / old: R1 1.003, R2 0.988, R3 0.977, R4 0.981 / 0.998, R5 0.965; rounds within 1–3% (old path R5
   up to 7%). **Reading data written by I1 costs the server the same as reading data written by the old path.**

**Finding.** No read defect in the database: stored chunks are byte-identical, and the server reads them equally
fast. The server spends about 3.2 ms of an R3 read that B3 measures at 7.5–8 ms from the macOS client, so more
than half of a measured read is client, Docker Desktop's network path and conversion. The difference in B3.10 and
B3.11 sits in that part; its exact cause (most likely the state of the client process that has just run the I1
ingestion) is not established. A stage that decides adoption should therefore measure reads in a fresh client
process and also report server-side read cost.

# Amendment B3.12: B3.11 with reads from a fresh client process

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `d819edb`, 2026-11-05 12:16) before any benchmark-set load
(smoke check on the development set, `results/dev/b312-smoke/`). Started automatically when the start condition
held (12:17:55, 1-minute load average 2.75, no foreign process above half a core; `results/b312.start`); run
12:17–12:27 (`results/b312/`, `run_b312.sh`, log `results/b312.log`). No foreign process above half a core in any
snapshot; load averages between loads 3.1–5.5 from the session itself.

**Correctness: all six loads passed; stored chunks byte-identical in all six** (25,138 chunks, one digest).
Ingestion (reported): `morletdb-i1` 13.66, 13.61, 13.52 M samples/s; `morletdb-033` 6.39, 6.31, 6.19 M.

## Reads from a fresh client process (p50 ms of each load) and inside the server (µs per read)

| Query | `morletdb-i1` | median | `morletdb-033` | median | ratio | met | server-side ratio (medians) |
|---|---|---:|---|---:|---:|---|---:|
| R1 | 0.629, 0.622, 0.625 | 0.625 | 0.619, 0.624, 0.623 | 0.623 | 1.003 | yes | 1.027 |
| R2 | 6.683, 6.785, 6.639 | 6.683 | 6.554, 6.681, 6.520 | 6.554 | 1.020 | yes | 1.002 |
| R3 | 7.442, 7.707, 11.333 | 7.707 | 7.162, 7.694, 7.345 | 7.345 | 1.049 | yes | 1.002 |
| R4 recent | 0.621, 0.635, 0.629 | 0.629 | 0.724, 0.632, 0.635 | 0.635 | 0.991 | yes | 0.999 |
| R4 older | 0.619, 0.610, 0.614 | 0.614 | 0.609, 0.616, 0.606 | 0.609 | 1.008 | yes | 1.036 |
| R5 (4 threads) | 0.810, 0.817, 0.804 | 0.810 | 0.869, 0.796, 0.780 | 0.796 | 1.018 | yes | 1.009 |
| Interval statistics | 2.545, 2.540, 2.572 | 2.545 | 2.551, 2.536, 2.520 | 2.536 | 1.004 | yes | |

p95 medians (reported): within 0.96–1.13× for every query. `morletdb-i1` D2 r3 was slower in both its client and
its server-side reads (R3 11.3 ms and 4,275 µs, against 7.4–7.7 ms and 3,155–3,169 µs in its other loads), with
no foreign process in its snapshots; the median over three loads absorbs it.

**Verdict: all three criteria are met. `morletdb-i1` is adopted as the recommended configuration.** Together with
B3.10: from one client connection, the next release (SQL 0.3.4, unreleased) ingests **1.4× (D2) and 1.6× (D1)
faster than QuestDB 10.1.0** and 2.2× / 2.1× faster than 0.3.5-alpha.1, with byte-identical stored data and reads
unchanged. The website's QuestDB tab adds the B3.10 row, labelled as not yet released; the chart and the other
systems' tabs keep the released build's same-session pairs (B3.8, B3.9), because B3.10 paired I1 with QuestDB only.

# Amendment B3.13: every system beside the released MorletDB 0.3.6-alpha.1

Stage frozen in [PROTOCOL.md](PROTOCOL.md) (commit `0024d59`, 2026-11-05 14:02) before any benchmark-set load
(development smoke check passed for every system, `results/dev/b313-smoke/`). Started automatically when the start
condition held (14:05:53, 1-minute load average 2.39, no foreign process above half a core; `results/b313.start`);
run 14:05–16:25 (`run_b313.sh`, log `results/b313.log`), MorletDB `ghcr.io/merkhet-io/morletdb:0.3.6-alpha.1`
(`sha256:439b1f01484e…`), arm `morletdb-i1`, reads from a fresh client process for every system. Summary:
`summarize_b313.py` (full tables printed by it).

**Machine (reported, as the amendment requires).** From 14:07 on, a Safari/WebKit page used 60–130% of a core in
most pre-load snapshots, Spotlight 70% once (before TimescaleDB D2 r3), and **a LaTeX build started by this work
(`tectonic`, the second report) ran 14:23–14:27, during four loads of the TDengine pair** (MorletDB D1 r2 and r3,
TDengine D1 r2, D2 r3). Systems alternated, so ratios within a pair are comparable; ratios close to the 10%
threshold should be read with that in mind. Per-load snapshots are in `results/b313-*/machine/`.

**Correctness: all 78 loads passed; MorletDB's stored chunks were byte-identical in every load** (25,138 chunks,
one digest, equal to B3.10–B3.12's).

## Same-session results (× MorletDB's figure; > 1 = MorletDB better; ingestion: ratio of medians, all-loads bound)

| | Ingestion D2 | Ingestion D1 | R1 p50 / p95 | R2 | R3 | R5 queries/s | Statistics | Memory |
|---|---|---|---|---|---|---|---|---|
| QuestDB 10.1.0 | **1.49 (1.42)** | **1.65 (1.63)** | 1.7 / 1.8 | no statement (0.95 / 1.29) | 1.1 / 1.2 | 1.7 | no statement (1.02 / 1.94) | 13× |
| TDengine 3.4.3.3 | 2.98 (2.64) | 2.85 (2.48) | 5.9 / 4.6 | 7.3 / 6.3 | 4.9 / 4.0 | 13.8 | 1.5 / 3.9 | 6× |
| TimescaleDB 2.31.0 (columnstore) | 15.3 (13.8) | 14.5 (14.1) | 2.4 / 2.1 | 5.0 / 4.6 | 3.3 / 2.9 | 3.5 | 3.9 / 3.3 | 2.3× |
| IoTDB 2.0.11 (table) | 5.2 (4.9) | 5.7 (5.5) | 11.7 / 8.8 | 4.8 / 3.7 | 2.9 / 2.6 | 7.1 | 2.8 / 3.0 | 34× |
| CrateDB 6.4.6 (array) | 17.2 (15.6) | 15.1 (13.5) | 11.0 / 10.3 | 6.8 / 6.1 | 11.5 / 8.6 | 7.3 | 24.5 / 35.3 | 35× |
| CrateDB 6.4.6 (row) | 35.7 (34.6) | 42.0 (39.7) | 16.2 / 15.1 | 14.5 / 13.1 | 10.4 / 8.6 | 11.1 | 1.7 / 2.6 | 53× |

TimescaleDB's rowstore state reads 2.8–6.2× slower than MorletDB (statistics 5.9×). MorletDB ingested 10.8–14.3 M
samples/s from one connection across the pairs (CrateDB pairs lowest, 11.6–11.8 M D2, with the WebKit load highest
in those snapshots); every competitor's ingestion statement holds in all loads.

**Statements against QuestDB (≥ 10% in all loads, or at both p50 and p95):** MorletDB 0.3.6-alpha.1 ingests from one
connection **1.5× (D2) and 1.65× (D1) faster** (all-loads bounds 1.42 and 1.63); reads 4,096 samples 1.7× / 1.8×
faster, three channels 1.1× / 1.2× faster, recent and older short reads 1.3× faster, 1.7× more concurrent queries;
long reads (R2) and interval statistics: no statement; 13× less server memory.

## Chunk summaries (arm `morletdb-i1-sum`, QuestDB's pair)

| | Statistics p50 / p95 | Ingestion D2 / D1 (median) | Database |
|---|---|---|---|
| MorletDB 0.3.6 | 2.54 / 2.69 ms | 14.13 M / 13.95 M | 169.3 MB |
| **MorletDB 0.3.6 with chunk summaries** | **0.56 / 0.70 ms** | 13.90 M / 13.63 M (−1.7% / −2.3%) | 171.2 MB (+1.1%) |
| QuestDB 10.1.0 | 2.59 / 5.22 ms | 9.49 M / 8.43 M | |

With chunk summaries, MorletDB computes interval statistics **4.6× (p50) and 7.5× (p95) faster than QuestDB**, at
1.7–2.3% of its ingestion speed and 1.1% more database; it still ingests 1.46× / 1.62× faster than QuestDB.

## Scope

One dataset, one machine, one client connection; the competitors' configurations are their stages' (B3.2,
B3.4–B3.7). Long reads are not faster than QuestDB's on 0.3.6; parallel decoding (main, after 0.3.6) addresses them
(`../reads_r2/ATTRIBUTION.md`) and needs its own stage once released.

# Amendment B3.14: long reads and interval statistics against QuestDB with parallel decode

Protocol: Amendment B3.14 in [PROTOCOL.md](PROTOCOL.md), frozen in `e0b926c` before any benchmark load; run by the
assistant overnight at the owner's request. Runner `run_b314.sh`, summary [`summarize_b314.py`](summarize_b314.py);
results in `results/b314-questdb/` and `results/b314-released/`. Development build `morletdb:b314` (main at
`5274e88`, SQL 0.3.5, parallel decode; `sha256:7dff89832ff3…`) and the released `0.3.6-alpha.1`, QuestDB 10.1.0.

**Machine.** The start condition was met at 23:48:57 (1-minute load average 2.71, no foreign process above half a
core; `results/b314-questdb/b314.start`); the run took 23:48–00:00 on 2026-11-06. Load averages before loads
(median 7.2, max 9.7) came from the session itself: Docker's VM and backend are the only processes above half a
core in the snapshots, except `signpost_reporter` (macOS diagnostics) in two. The Mac was kept awake with
`caffeinate` on AC power. One earlier start attempt waited for the condition (a browser process held 70% of a core)
and was stopped before any load; the F2 study ran in between.

**Correctness: all 24 loads passed; MorletDB's stored chunks were byte-identical in every load and on both images**
(one digest, equal to B3.10–B3.13's).

## QuestDB pair (× MorletDB build's figure; > 1 = MorletDB better)

| | Ingestion D2 | Ingestion D1 | R1 p50 / p95 | R2 | R3 | R4 recent / older | R5 queries/s | Statistics | Memory |
|---|---|---|---|---|---|---|---|---|---|
| MorletDB build | **1.48 (1.45)** | **1.64 (1.63)** | 1.2 / 2.1 | no statement (1.03 / 1.17) | 1.2 / 1.3 | no statement (1.03 / 1.34; 0.89 / 1.26) | 1.64 | no statement (0.98 / 1.76) | 13× |
| MorletDB build, chunk summaries | **1.47 (1.46)** | **1.62 (1.60)** | 1.2 / 2.2 | no statement (1.05 / 1.19) | 1.2 / 1.3 | no statement | 1.59 | **4.6 / 8.2** | 14× |

## Build against the release (same session)

Ingestion, R1, R3–R5 and statistics are equal within noise (ratios 0.97–1.07). **R2 (long reads): 1.17× faster at
p50 (5.70 against 6.67 ms) and 1.05× at p95 (8.63 against 9.04 ms): no statement** under B3's rule. Parallel decode
shortens the median long read but not its tail on this machine, where a long read of 64 chunks spends most of its
time outside decoding (B3.12 found more than half of a measured read in the client and Docker's network path).

## Statements against QuestDB (B3.14, development build)

MorletDB ingests from one connection **1.5× (D2) and 1.6× (D1) faster** (all-loads bounds 1.45 and 1.63), reads
4,096 samples 1.2× / 2.1× faster and three channels 1.2× / 1.3× faster, serves 1.6× more concurrent queries, with
13× less server memory. With chunk summaries it computes interval statistics **4.6× (p50) and 8.2× (p95) faster**.
**Long reads (R2) and base-arm statistics remain without a statement**: parallel decode does not close the R2 gap
at p50; the remaining cost is in transfer and the client, which the planned compressed transfer (chunks sent still
encoded and decoded in the client) targets. Short reads of older data (R4) lost B3.13's statement (0.89 / 1.26).

**Scope.** A development build of main, one machine, one dataset. As the amendment says, the website keeps B3.13's
figures until a release containing this code is measured.

# Amendment B3.15: the released 0.3.7-alpha.1 with compressed transfer against QuestDB

Protocol: Amendment B3.15 in [PROTOCOL.md](PROTOCOL.md), frozen in `e3a3471` before any load of the benchmark set,
with three deviations recorded there before the result run. Runner `run_b315.sh`, summary
[`summarize_b315.py`](summarize_b315.py); results in `results/b315-questdb/` and `results/b315-previous/`. Images:
the released `0.3.7-alpha.1` (`sha256:71b3086beadd…`) and `0.3.6-alpha.1` (`sha256:439b1f01484e…`), QuestDB 10.1.0.
Decoder: `libmorlet_ffi.dylib` from the release's macOS wheel, checked against the release's `SHA256SUMS`, sha256
`3857d48f7b8d…`, recorded in every `morletdb-i2` result.

**Attempts.** Two earlier attempts ran all 24 loads correctly but are not admissible, and their figures were not
looked at. Both were set aside before reading any figure, and both are kept for the record:
- Attempt 1 (10:23) started on a busy machine, at a load average of 5.66 (`results/b315-attempt1/README.md`).
- Attempt 2 (11:58) slept from 12:04, with the lid closed on battery (`results/b315-attempt2/README.md`, with the
  power log).

Three arming runs stopped before any load:
- A runner bug under `pipefail`.
- Docker Desktop not running: every load failed at once, and the empty records were deleted.
- A start check that sampled the machine twice.

The runner now waits until Docker is up, the Mac is on AC power without lid sleep, and the machine is quiet. It
checks power and lid again before every load. None of this changes a measurement.

**Machine.** The start condition was met at 15:46:46 (1-minute load average 2.48, no foreign process above half a
core; `results/b315-questdb/b315.start`). The run took 15:46–15:58 on 2026-11-06, on AC power with the lid closed and
an external display (deviation 3). The power log shows no sleep and no battery power during the run.

Load averages before loads (median 5.87, max 7.68) came from the session itself: Docker's VM and backend are the
only processes above half a core in the snapshots, except WebKit's networking process (57% and 51%) in two of 48.
Docker was never restarted.

**Correctness: all 24 loads passed.** Every read matched the source bit for bit, and MorletDB's stored chunks were
byte-identical in all 18 MorletDB loads, on both images (one digest, `bd6cd3a4…`, equal to B3.10–B3.14's). Every
`morletdb-i2` load read R2 and R3 by compressed transfer.

## QuestDB pair (× MorletDB's figure; > 1 = MorletDB better; ingestion: ratio of medians, all-loads bound)

| | Ingestion D2 | Ingestion D1 | R1 p50 / p95 | R2 | R3 | R4 recent / older | R5 queries/s | Statistics | Memory |
|---|---|---|---|---|---|---|---|---|---|
| 0.3.7, compressed transfer (`morletdb-i2`) | **1.53 (1.51)** | **1.58 (1.55)** | no statement (1.02 / 1.33) | **2.6 / 2.6** | **2.9 / 2.7** | no statement (0.84 / 1.02); **QuestDB 1.5 / 1.2** | 1.57 | **4.4 / 7.3** | 13× |
| 0.3.7, raw transfer (`morletdb-i1`) | **1.54 (1.53)** | **1.60 (1.55)** | **1.2 / 1.5** | no statement (1.08 / 1.13) | **1.4 / 1.2** | no statement (0.95 / 1.26; 0.90 / 1.36) | 1.71 | **4.5 / 8.1** | 13× |

Medians (p50 / p95 ms), `morletdb-i2` against QuestDB:
- R2: 2.53 / 3.27 against 6.68 / 8.34.
- Interval statistics: 0.56 / 0.64 against 2.45 / 4.66.
- R4 older: 0.82 / 1.43 against 0.54 / 1.16.

Server memory: 176 MiB against 2,267 MiB.

## The frozen questions

1. **R2 (128,000 samples): MorletDB 0.3.7 with compressed transfer is 2.6× faster than QuestDB at p50 and at p95.**
   With raw transfer, the same release has no statement (1.08 / 1.13), as in B3.13 and B3.14.
2. **Interval statistics: 4.4× faster at p50 and 7.3× at p95.** Chunk summaries are now on by default; raw
   transfer gives 4.5 / 8.1.
3. **Everything else, including the losses:**
   - Ingestion keeps its statements (1.5× and 1.6×), and R3 keeps and strengthens its statement (2.9× / 2.7×).
   - **R1 loses B3.13's statement in the compressed-transfer arm** (1.02 / 1.33). The raw arm keeps it (1.2 / 1.5).
   - **R4 on older data is a loss: QuestDB is 1.5× faster at p50 and 1.2× at p95.** The raw arm has no statement
     (0.90 / 1.36).
   - R5 serves 1.57× more queries than QuestDB, against 1.71× for the raw arm.

## Compressed against raw transfer, and 0.3.7 against 0.3.6 (same session)

**Compressed against raw transfer:**
- R2 is 2.4× / 2.3× faster (2.53 against 6.20 ms at p50) and R3 2.2× / 2.2× faster.
- Interval statistics: no statement (0.96 / 0.90).
- Ingestion and memory are equal (0.99).

**Short reads are slower in the compressed-transfer arm** (R1 1.1× / 1.1×, R4 recent 1.1× / 1.2×, R4 older
1.4× / 1.7×; R5 0.91×). Both arms read these by the same raw statement: they are shorter than the 32,768-sample
threshold, as in the SDK. The cause is not established:
- One known difference: the harness looks up the decoder on every read (about 20 µs per call, measured afterwards
  outside the run), where the SDK decides once. That explains part of R1's 0.09 ms, not R4 older's 0.22 ms.
- The arm also always runs first in each round.

Finding the cause needs its own exploratory measurement, so these figures stand as measured.

**0.3.7 with compressed transfer against 0.3.6:**
- R2: 2.8× / 2.6× faster. R3: 2.3× / 2.0× faster.
- Interval statistics: 4.5× / 4.1× faster, from summaries by default.
- Ingestion: no statement (0.96 / 0.97). Memory is equal.
- Short reads: 1.1×–1.5× slower, as above.

Storage: chunk bytes are unchanged, and the summaries add 1.8 MB of heap on this dataset (4.33 MB on 0.3.7 against 2.48 MB on 0.3.6
for `b3_chunks`).

## Statements against QuestDB (B3.15, released 0.3.7-alpha.1)

MorletDB, used as its SDK uses it:
- Ingests from one connection **1.5× (D2) and 1.6× (D1) faster**.
- Reads 128,000 samples **2.6× faster** and three channels **2.9× / 2.7× faster**.
- Computes interval statistics **4.4× / 7.3× faster**.
- Serves 1.6× more concurrent queries, with 13× less server memory.

**Short reads of older data (R4) are 1.5× / 1.2× faster on QuestDB, and R1 has no statement** in this arm.

**Scope.** One machine, one dataset, a client on the same host as the server's Docker VM. Compressed transfer helps
most where transfer is costly, and its gain will differ on other network paths.

**Website.** Under the rule fixed in the amendment, the QuestDB tab and lane take the `morletdb-i2` figures above,
labelled release 0.3.7-alpha.1 with compressed transfer, including the R4 loss and R1's missing statement. The
other databases keep B3.13's figures, labelled 0.3.6-alpha.1.
