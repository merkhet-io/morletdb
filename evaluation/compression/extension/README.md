# Encoded chunk storage: engineering check

This is an engineering measurement, not a frozen-protocol test. It checks the
storage change that the stage-1 benchmark ([results](../results/ab70819/RESULTS.md))
recommended: a standard integer codec in place of `float8[]` chunks. It reuses
that benchmark's held-out data (same loaders) and was run once on an Apple M5 Max,
PostgreSQL 17.11 (pgrx), release build.

Inputs are the floats a user would append:
- CX seismic counts (`quantum` 1);
- IMS three-decimal values (`0.001`);
- Paderborn ADC counts (`1`);
- Paderborn's physical floats as stored in the `.mat` files, with the nominal ADC
  step 100/32768 as quantum. These floats are not exact multiples of any step a
  user would know.

## Codec alone (`codec.json`, all held-out blocks, 128–175 M samples per dataset)

Bits per sample, every chunk decoded and compared bitwise:

| Dataset | This codec, 4096 / 32768 chunks | FLAC (stage 1), 4096 / 32768 |
|---|---|---|
| CX | 4.99 / 4.91 | 4.74 / 4.59 |
| IMS | 8.32 / 8.29 | 8.18 / 8.04 |
| Paderborn counts | 7.99 / 7.95 | 7.83 / 7.68 |
| Paderborn physical floats | 9.31 / 9.27 | — |

- The fixed-predictor coder is 2–7% larger than FLAC's LPC. It is pure Rust with no
  dependencies.
- Paderborn's physical floats need last-place corrections of ±1 ULP at the
  per-chunk fitted step, which costs about 1.3 bits/sample.
- Throughput is single-threaded and measured with other work running: encode
  3–14 M samples/s, decode 35–57 M samples/s.

## In Postgres (`pg_storage-*.json`, first ~4.2 M held-out samples per dataset)

The same chunks were appended through `morlet_append_chunk` into a pre-change
`float8[]` collection and a new encoded collection. Table bits/sample are
`pg_total_relation_size` of the chunk table (heap, TOAST, index) after `VACUUM`:

| Dataset | float8[] | encoded | reduction |
|---|---|---|---|
| 4096-sample chunks (importer default) | | | |
| CX | 17.4 | 5.9 | 2.9× |
| IMS | 19.0 | 11.2 | 1.7× |
| Paderborn counts | 18.3 | 9.8 | 1.9× |
| Paderborn physical | 22.0 | 11.4 | 1.9× |
| 32768-sample chunks | | | |
| CX | 16.9 | 5.1 | 3.3× |
| IMS | 18.1 | 9.8 | 1.9× |
| Paderborn counts | 17.8 | 8.7 | 2.1× |
| Paderborn physical | 20.4 | 9.8 | 2.1× |

- Four interval reads per table matched the source bitwise.
- Append throughput (with feature extraction) was unchanged within noise.

### Corrections to earlier statements

- The stage-1 RESULTS.md estimated about 5× against MorletDB's storage. That
  compared different data and assumed 22.7 bits/sample for `float8[]`. Measured
  here, Postgres TOAST already compresses integer-valued `float8[]` chunks to
  16–20 bits/sample, so the real gain is **1.7–3.3×**.
- At 4096-sample chunks, per-row and TOAST overhead adds 1.3–2.4 bits/sample. Larger
  chunks recover most of it, at a read-latency cost (see below).
- Forcing values inline (`STORAGE MAIN`) was tried. It made IMS worse (one ~4.5 kB
  value per page) and was not kept.

## Read and ingest performance (`read_bench.py`, `results/read_bench.json`)

- **Data:** development-period Paderborn bearings, not the held-out units: 64 files
  of int16 counts concatenated into one 16,252,928-sample recording, appended as
  float8 with quantum 1.
- **Environment:** Apple M5 Max, 128 GB, macOS 27.0.1; PostgreSQL 17.11 (pgrx), release
  build, default settings (`shared_buffers` 128 MB).
- **Client:** Python 3 with psycopg over localhost TCP, one connection, no
  concurrency.
- **Latency** is client wall time per query and includes the round trip. Reads
  return only `cardinality(samples)`, so the cost is server-side read and decode.
  The client-fetch variant transfers the arrays.

| | float8[] 4096 | float8[] 32768 | encoded 4096 | encoded 32768 |
|---|---|---|---|---|
| chunk table bits/sample | 19.35 | 18.59 | 10.35 | 8.58 |
| ingest incl. features (M samples/s) | 0.29 | 0.36 | 0.41 | 0.50 |
| random 512 read p50 / p95 / p99 (ms) | 3.39 / 4.80 / 6.54 | 3.65 / 3.92 / 4.17 | **1.82 / 2.11 / 2.23** | 3.41 / 3.60 / 3.74 |
| random 4096 read p50 / p95 (ms) | 4.86 / 6.37 | 4.88 / 6.85 | 3.19 / 3.39 | 4.61 / 6.32 |
| 512 read across own chunk boundary p50 / p95 (ms) | 3.88 / 5.15 | 5.77 / 6.17 | 2.14 / 2.32 | 5.21 / 5.45 |
| decode amplification, 512 / 4096 / boundary | 9.0 / 2.0 / 16 | 64.6 / 9.0 / 128 | 9.0 / 2.0 / 16 | 64.6 / 9.0 / 128 |
| sequential, server (M samples/s) | 1.73 | 2.34 | 2.34 | 2.39 |
| sequential, fetched to client (M samples/s) | 1.37 | 1.83 | 1.83 | 1.85 |
| after PG restart: 512 read p50 / p95 (ms) | 4.09 / 6.05 | 3.65 / 3.94 | 1.84 / 2.18 | 3.46 / 4.26 |

Sample sizes: 1000 random 512 reads, 300 random 4096 reads, up to 1000 boundary
reads (496 boundaries exist at 32768), and 300 reads after restart. Offsets come
from one fixed seed and are identical across configurations.

- **Decode amplification** is the number of samples decoded (or detoasted, for
  `float8[]`) per sample returned. Whole chunks are always materialized.
- **Not a true cold cache.** "After PG restart" empties shared_buffers only. The
  OS page cache cannot be dropped without root on this machine, and the results
  match the warm figures. Disk-cold latency is unmeasured.
- **Sequential reads are limited by `morlet_read_interval`**: its SQL
  `unnest`/`array_agg` assembly runs at about 2.3 M samples/s regardless of layout.
  This, not decoding, is the read bottleneck; changing it is outside this PR.
- **Chunk-size recommendation.** Keep 4096 for interactive short-window access:
  it has the lowest latency and is about 1.9× faster than 32768 for 512-sample
  reads. 32768 saves about 17% of chunk storage for archive-style access. The
  storage saving alone is not a reason to choose it.

## What these numbers are, and are not

- **Engineering work, not fresh validation.** These runs reuse the stage-1
  held-out units, which that benchmark had already consumed. Two design changes
  were prompted by failures observed on them:
  - the per-chunk step fit with ULP corrections, after strict exact-grid coding
    stored Paderborn's physical floats raw;
  - not forcing inline storage.

  The post-fix held-out numbers therefore show that the implementation works on
  these units; they are not independent evidence. No codec parameter was
  tuned against them, and later performance work uses development-period data
  (`read_bench.py`).
- **Pre-checksum format.** The size runs used format version 1. Version 2 adds a
  4-byte CRC-32C per chunk: +0.008 bits/sample at 4096-sample chunks and +0.001 at
  32768. That is below the precision reported here.
- **Function names.** The script's per-recording value sizes now come from
  `morlet_recording_storage`. The table sizes above are `pg_total_relation_size` of
  the chunk table, which `morlet_collection_storage` reports.
- **Three baselines**, never multiplied together: previous MorletDB storage (the
  table above), optimized Parquet, and the original sample payload. See
  [../README.md](../README.md).

## Reproduce

```sh
.venv-compress/bin/python evaluation/compression/extension/dump_chunks.py OUT 4096
cargo run --release -p morlet-core --example rawcodec_bench -- OUT/*-4096.bin
MORLET_DSN=... python evaluation/compression/extension/pg_storage.py OUT 1024 4096
```
