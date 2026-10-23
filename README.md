![MorletDB — Search your waveforms.](assets/brand/hero.svg)

# Signal analytics and waveform search

[![Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-39C9DF.svg)](LICENSE)
[![Go checks](https://github.com/merkhet-io/morletdb/actions/workflows/ci.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/ci.yml)
[![Rust & Postgres checks](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml)

**MorletDB stores waveform recordings and finds similar intervals through their spectral and statistical structure.** Import Parquet, preserve acquisition context, and search with SQL. A [Merkhet](https://github.com/merkhet-io) project, licensed under Apache 2.0.

The first application is vibration monitoring. The recording model is domain neutral: uniformly sampled signals, explicit sample coordinates, channel, units and metadata.

> **Early prototype.** Exact search, durable ingestion and recovery are tested. Extraction is synchronous; large-scale retrieval and workload isolation are still under evaluation.

[Get started](#try-it-in-postgres) · [Import Parquet](tools/ingest/README.md) · [SQL guide](extensions/pg_morlet/README.md) · [Benchmarks](benchmarks/README.md) · [Brand assets](assets/brand/README.md)

## From samples to matches

- **Keep the recording.** Append continuous chunks with global offsets. Windows cross acquisition boundaries, and raw samples remain available alongside metadata.
- **Represent local structure.** Versioned Morlet extraction produces spectral energies, statistical features and localized time/frequency tokens. Normalize each search window independently.
- **Find similar intervals.** Search typed feature vectors with acquisition and metadata filters. Results identify the source recording and exact half-open sample interval.

Durable appends publish raw chunks, newly complete segment features and extraction checkpoints in one transaction. Identical retries are safe, and a fresh connection can resume from committed state. See the [ingestion contract](docs/durable-ingestion.md).

## Try it in Postgres

Target: **PostgreSQL 17**. Follow the [Rust/pgrx setup guide](extensions/pg_morlet/README.md#setup) to install the extension, then run:

```sql
CREATE EXTENSION pg_morlet;
SELECT morlet_create_stream_collection('public', 'signals');
SELECT morlet_start_recording(
  'signals', 'motor-001', 4096, 'vibration', 'g', '{"asset":"motor-1"}'
);
SELECT morlet_append_chunk(
  'signals', 'motor-001', 0,
  ARRAY(SELECT sin(i::float8 / 8) FROM generate_series(0,2047) i)
);
SELECT * FROM morlet_search_streams(
  'signals',
  ARRAY(SELECT sin(i::float8 / 8) FROM generate_series(0,511) i),
  4096, 'vibration', 'g', '{"asset":"motor-1"}'
);
```

`morlet_stream_status('signals', 'motor-001')` reports durable progress. The [batched append API](docs/durable-ingestion.md#batched-appends) sends multiple typed chunks per database call with atomic rollback and the same replay checks.

The stream search above includes source and overlapping matches. The separate bounded-recording API also supports source-interval queries and overlap suppression; see the [SQL guide](extensions/pg_morlet/README.md).

## Import waveform archives

The [Parquet importer](tools/ingest/README.md) accepts either sample-array rows or individual sample rows. Column mappings are explicit, samples are packed across file-row boundaries, and input can be validated without a database.

```sh
python -m pip install -r tools/ingest/requirements.txt
# Configure DATABASE_URL or the standard PostgreSQL connection environment.
python tools/ingest/parquet_import.py signals.parquet \
  --collection public.signals --rpc-chunks 16 --commit-chunks 32
```

Imports use bounded batches and configurable transactions. Rerunning unchanged input verifies the committed prefix and avoids duplicates. Current sources are local immutable files; the importer does not yet provide S3 discovery or direct row-group resume.

## What a match means

`morlet-energy-v1` represents each window with twelve log-spaced Morlet energy bands plus AC RMS, crest factor and kurtosis. Search compares fifteen log-transformed features using RMS Euclidean distance. Query and stored windows must have compatible rate, channel, unit, length, normalization and extractor version.

Feature similarity is distinct from aligned sample distance. Both primitives are available, and both Go and Rust implementations validate the versioned extractor against shared fixtures. See the [numerical contract](docs/morlet-extractor.md).

## Measure before scaling

The [benchmark suite](benchmarks/README.md) separates raw ingest, feature computation and retrieval. It compares JSONB and typed Postgres storage with DuckDB/Parquet using identical features and matching top-k results.

A [long-stream sweep](benchmarks/README.md#continuous-recording-sweep) varies recording length, channel count, chunk size and append batch size, recording throughput, storage/WAL growth, sampled CPU/memory and search latency during writes. CI uploads machine-readable reports. These are reproducible prototype measurements, not production scale claims.

## Current boundaries

| Area | Contract today |
|---|---|
| Continuous recordings | Append-only chunks; bigint global sample coordinates; restartable checkpoints |
| Input | 1..32768 finite samples per chunk; 128..200000 Hz; explicit channel and units |
| Representation | 512..4096-sample windows; versioned extraction and per-window normalization |
| Search | Exact scan of up to 4096 eligible segments; metadata filters narrow candidates |
| Consistency | Synchronous extraction and transactional publication; caller permissions and RLS |
| Next | Long-stream tuning, asynchronous extraction and indexed candidate retrieval |

No resampling, gap interpolation or background extraction worker is implemented. Database-managed stream tables use the supported append APIs; direct mutation can violate cross-table invariants.

## Inside the monorepo

| Path | Role |
|---|---|
| [`crates/morlet-core`](crates/morlet-core) | Portable Rust extraction, chunk boundaries and validated checkpoints |
| [`extensions/pg_morlet`](extensions/pg_morlet) | Postgres storage, SQL primitives, ingestion and exact search |
| [`tools/ingest`](tools/ingest) | Parquet importer and input-contract tests |
| [`benchmarks`](benchmarks) | Reproducible storage, retrieval and streaming comparisons |
| [`apps/server`](apps/server) | Go reference server, HTTP API, CLI and original condition-monitoring demo |

The Rust core stays independent of Postgres. The Go server is a separate execution/storage path; there is no Go-to-Rust FFI. The monorepo keeps alternative integrations possible while workload evidence informs the architecture.

## Develop

```sh
make test-go
make test-rust
make pg-test  # configured PostgreSQL/pgrx required
```

For the Go demo, run `make build`, then `./bin/morletdb demo -data ./data` and open http://127.0.0.1:8080. See the [server guide](apps/server/README.md) and [development notes](docs/development.md).

## License

Copyright 2026 MorletDB contributors. Code, documentation and original brand artwork in this repository are licensed under the [Apache License, Version 2.0](LICENSE).
