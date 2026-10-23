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

## Transactional collections and exact search

The extension installs in `public`. Collection tables are created in a caller-selected schema, owned by the caller, and dumped/restored as normal application tables. No SECURITY DEFINER functions or automatic grants are used. Grant ordinary table/sequence privileges to application roles as needed; row-level security follows the caller's role.

```sql
SELECT morlet_create_collection('public', 'recordings');
INSERT INTO recordings(sample_rate_hz, channel, unit, metadata, samples)
VALUES (4096, 'vibration', 'g', '{"asset":"spindle-1"}',
        ARRAY(SELECT sin(i::float8 / 8) FROM generate_series(0,1023) i));
SELECT * FROM morlet_search('recordings'::regclass,
  ARRAY(SELECT sin(i::float8 / 8) FROM generate_series(0,511) i),
  4096, 'vibration', 'g', '{"asset":"spindle-1"}', 'none', 256, 10);
```

Rows hold 512..32768 finite samples, rates 128..200000 Hz, channel, unit and JSON object metadata. Arrays must be one-dimensional with lower bound 1. Normal INSERT/UPDATE/DELETE participate in transactions. Searches require SELECT privileges and use exact rate/channel/unit matching plus JSONB containment. Add ordinary indexes for these filters when useful.

Search returns recording IDs and zero-based half-open sample intervals, ranked by aligned sample RMSE (`none`, `demean` or `zscore`), with deterministic ID/offset ties. Query length is 512..4096; stride is positive; top_k is 1..100. At most 4096 candidate windows are evaluated, otherwise the query raises an error. Windows are generated at query time; overlapping hits and the query's own source are included. NULL arguments return no rows. This SQL path currently uses sample distance, independently of the Go server's Morlet-feature ranking. No resampling, ANN index or cross-channel comparison is performed.

Next: persisted versioned segment representations, source-exclusion/overlap policies and retrieval benchmarks. Stored segments and custom index access methods remain future work.
