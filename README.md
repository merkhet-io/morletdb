# MorletDB

### Search your waveforms.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Go](https://github.com/merkhet-io/morletdb/actions/workflows/ci.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/ci.yml)
[![Rust](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml)

**Signal-native storage and search, with a Go reference server and a Rust PostgreSQL extension scaffold.** A [Merkhet](https://github.com/merkhet-io) project.

MorletDB explores searching waveform recordings by localized signal structure. CNC vibration monitoring is the first application; the recording model is domain neutral.

## Monorepo

| Path | Purpose | Status |
|---|---|---|
| [`apps/server`](apps/server) | Go HTTP server, CLI, binary recording storage and exact interval search | Working bounded single-node prototype |
| [`crates/morlet-core`](crates/morlet-core) | Portable Rust validation, RMS, normalization and aligned sample distance | Initial primitives; no Morlet extractor yet |
| [`extensions/pg_morlet`](extensions/pg_morlet) | Rust/pgrx PostgreSQL extension | SQL primitive scaffold targeting PostgreSQL 17 |
| [`examples`](examples) | Python waveform-search client example | Targets the Go server |
| [`docs`](docs) | Search contract, architecture and development notes | See links below |

The Go implementation and Rust extension are separate execution paths. There is no shared storage, Go-to-Rust FFI or numerical parity claim. The extension does not yet implement waveform storage, Morlet features, indexed retrieval or a custom index access method.

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

`morlet_distance` currently measures aligned sample RMSE, not Morlet-feature distance. SQL functions validate bounded finite inputs and reject NULL array elements. See the extension guide for complete semantics.

## Checks

```sh
make test-go
make test-rust
make pg-test  # requires configured PostgreSQL/pgrx
```

Go code moved from the repository root to `apps/server`; its module path remains `github.com/merkhet-io/morletdb`. A root `go.work` supports editor navigation. Run `go test ./...` inside `apps/server`, or use Make targets. See [monorepo development](docs/development.md).

## License

Copyright 2026 MorletDB contributors. Licensed under the [Apache License, Version 2.0](LICENSE).
