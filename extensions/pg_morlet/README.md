# pg_morlet

Experimental Rust/pgrx extension for MorletDB on PostgreSQL. Initial target: **PostgreSQL 17**, `pgrx` and `cargo-pgrx` **0.16.1**. The crate, CLI and test framework versions must match. Rust is pinned at the repository root.

## Setup

Install Rust using rustup, plus PostgreSQL 17 server/development packages, a C compiler, clang/libclang, pkg-config and OpenSSL development headers. PostgreSQL must run as a non-root OS user. The setup below builds a user-owned PostgreSQL 17 installation, avoiding writes to system extension directories. On Debian/Ubuntu:

```sh
sudo apt-get install build-essential bison flex libreadline-dev zlib1g-dev libclang-dev clang pkg-config libssl-dev
cargo install cargo-pgrx --version 0.16.1 --locked
cargo pgrx init --pg17 download
cd extensions/pg_morlet
cargo pgrx run pg17
```

Inspect `~/.pgrx/config.toml` to locate the managed `pg_config` for packaging.

`cargo pgrx run` installs the extension and opens psql. Execute `CREATE EXTENSION pg_morlet;` there. No `shared_preload_libraries` entry is required for this scaffold.

```sh
# From extensions/pg_morlet:
cargo pgrx test pg17
cargo pgrx schema pg17
cargo pgrx package --pg-config /path/to/pgrx-managed/pg_config
```

SQL is generated from `#[pg_extern]` annotations by pgrx. `sql/smoke.sql` is a manual smoke script, not an extension migration. Before the first released version, establish generated SQL snapshots and upgrade scripts; there is no upgrade path promised for this unreleased scaffold.

## SQL functions

| Function | Semantics |
|---|---|
| `morlet_version()` | Crate version |
| `morlet_rms(float8[])` | Raw RMS including DC |
| `morlet_normalize(float8[], text)` | `none`, mean removal (`demean`), or mean removal plus population-standard-deviation scaling (`zscore`) |
| `morlet_distance(float8[], float8[], text)` | RMSE over aligned normalized samples, requiring equal lengths |
| `morlet_extractor_version()` | `morlet-energy-v1` representation identifier |
| `morlet_signature(float8[], float8)` | Full signature, band energies and localized tokens as JSONB |
| `morlet_features(float8[], float8)` | Fifteen log1p spectral/statistical features |
| `morlet_feature_distance(float8[], float8[], float8)` | RMS Euclidean distance between Morlet features; equal sample lengths required |

Inputs must have 1..32768 samples, finite and within +/-1e9. NULL array elements raise SQL errors; NULL arguments return SQL NULL through strict functions. Constant inputs normalize to zeros in demean/zscore modes. Sample rate and physical units are not encoded in these primitive array arguments: callers must ensure comparability. This is not clinical diagnosis, a protection mechanism or validated fault detection.

The Morlet SQL functions require 512..32768 samples and a finite sample rate in [128,200000] Hz. They remove DC, retain AC amplitude and share the Go extractor's versioned numerical contract. Raw RMS includes DC, whereas signature RMS is AC-only. `morlet_distance` is a sample-space metric; `morlet_feature_distance` uses the Go-compatible log-feature metric. Callers must ensure the same physical units and acquisition conditions. Neither metric is a calibrated probability. Numerically degenerate inputs that produce non-finite features are rejected.

```sql
SELECT morlet_extractor_version();
SELECT morlet_features(array_fill(0.::float8, ARRAY[512]), 4096.);
SELECT morlet_signature(array_fill(0.::float8, ARRAY[512]), 4096.)->>'version';
```

The eight shared fixtures and SQL wrapper tests validate numerical agreement; they do not establish medical or industrial diagnostic accuracy. See [extractor contract](../../docs/morlet-extractor.md).

## Next milestones

1. Define transactional recording/chunk/segment tables and representation specifications.
2. Add exact interval retrieval with SQL metadata filtering and source provenance.
3. Benchmark candidate retrieval before selecting pgvector or a custom access method.

No background workers, native index storage, database-managed waveform tables or search API are implemented yet.
