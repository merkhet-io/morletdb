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
