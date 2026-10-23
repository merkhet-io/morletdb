# MorletDB

### Search your waveforms.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Go](https://github.com/merkhet-io/morletdb/actions/workflows/ci.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/ci.yml)
[![Rust](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml)

**Signal-native storage and search, with a Go reference server and a Rust PostgreSQL extension.** A [Merkhet](https://github.com/merkhet-io) project.

MorletDB explores searching waveform recordings by localized signal structure. CNC vibration monitoring is the first application; the recording model is domain neutral.

## Monorepo

| Path | Purpose | Status |
|---|---|---|
| [`apps/server`](apps/server) | Go HTTP server, CLI, binary recording storage and exact interval search | Working bounded single-node prototype |
| [`crates/morlet-core`](crates/morlet-core) | Portable Rust primitives and versioned Morlet extraction | Validated against shared Go/Rust fixtures |
| [`extensions/pg_morlet`](extensions/pg_morlet) | Rust/pgrx PostgreSQL extension | Transactional collections, persisted Morlet segments and exact search on PostgreSQL 17 |
| [`examples`](examples) | Python waveform-search client example | Targets the Go server |
| [`docs`](docs) | Search contract, architecture and development notes | See links below |

The Go implementation and Rust extension are separate execution paths. Storage remains separate, with no Go-to-Rust FFI. Both implementations check `morlet-energy-v1` signatures against the same committed fixtures within documented floating-point tolerances. The extension exposes Morlet signatures, tokens and features, caller-owned transactional waveform collections, and bounded exact sample-distance window search. A portable [chunk extractor](docs/chunked-recordings.md) handles windows across acquisition boundaries. A separate [durable ingestion API](docs/durable-ingestion.md) stores continuous raw chunks, typed feature rows and restartable checkpoints in Postgres. Generated columns persist versioned Morlet segment features and keep them consistent with recording writes. Feature search scans stored segments exactly, supports stored-interval queries, excludes source overlaps, and suppresses overlapping hits. Indexed similarity retrieval remains future work.

## Run the Go prototype

Requires Go 1.24+:

```sh
make build
./bin/morletdb serve -data ./data
# In another terminal:
python3 examples/waveform_search.py
```

For the CNC dashboard, run `./bin/morletdb demo -data ./data`, then open http://127.0.0.1:8080. See the [server guide](apps/server/README.md) and [waveform search contract](docs/waveform-search.md).

## Develop the PostgreSQL extension

Requires the pinned Rust toolchain, PostgreSQL 17 development headers, clang/libclang and `cargo-pgrx` 0.16.1. Follow the [extension setup guide](extensions/pg_morlet/README.md).

```sh
cargo test --locked -p morlet-core
cargo install cargo-pgrx --version 0.16.1 --locked
cargo pgrx init --pg17 download
cd extensions/pg_morlet
cargo pgrx run pg17
```

In the psql session:

```sql
CREATE EXTENSION pg_morlet;
SELECT morlet_version();
SELECT morlet_rms(ARRAY[3., 4.]::float8[]);
SELECT morlet_distance(
    ARRAY[1., 2., 3.]::float8[],
    ARRAY[12., 14., 16.]::float8[],
    'zscore'
);
```

`morlet_distance` measures aligned sample RMSE; `morlet_feature_distance` compares Morlet feature vectors. `morlet_signature(samples, sample_rate_hz)` returns the full versioned representation as JSONB, and `morlet_features(samples, sample_rate_hz)` returns its 15 search features. SQL functions validate bounded finite inputs and reject NULL array elements. See the extension guide for complete semantics.

## Checks

```sh
make test-go
make test-rust
make pg-test  # requires configured PostgreSQL/pgrx
```

Go code moved from the repository root to `apps/server`; its module path remains `github.com/merkhet-io/morletdb`. A root `go.work` supports editor navigation. Run `go test ./...` inside `apps/server`, or use Make targets. See [monorepo development](docs/development.md).

## Parquet ingestion

The [Parquet importer](tools/ingest/README.md) accepts sample-array and sample-row layouts with explicit mappings, bounded batches and safe replay into durable stream collections.

## Benchmark baseline

See [the benchmark harness](benchmarks/README.md) for bulk ingestion, feature backfill, Postgres versus DuckDB/Parquet exact retrieval, typed Postgres versus JSONB comparisons, and concurrent-write measurements. CI runs a bounded smoke benchmark; production-scale and HTAP conclusions remain unproven.

## License

Copyright 2026 MorletDB contributors. Licensed under the [Apache License, Version 2.0](LICENSE).
