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
  chunks recover most of it.
- Forcing values inline (`STORAGE MAIN`) was tried. It made IMS worse (one ~4.5 kB
  value per page) and was not kept.

## Reproduce

```sh
.venv-compress/bin/python evaluation/compression/extension/dump_chunks.py OUT 4096
cargo run --release -p morlet-core --example rawcodec_bench -- OUT/*-4096.bin
MORLET_DSN=... python evaluation/compression/extension/pg_storage.py OUT 1024 4096
```
