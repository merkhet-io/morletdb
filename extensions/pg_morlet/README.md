For cross-chunk reads, stored-interval search and the Python client, start with
the [recording workflow](../../docs/recording-workflow.md).

> **Active API:** use the continuous recording functions (`morlet_create_stream_collection`,
> `morlet_start_recording`, `morlet_append_chunk[s]`, `morlet_search_streams`).
> Bounded recordings, JSONB grids and typed composite arrays below are retained
> experimental APIs. New product features target the continuous model.
> See [architecture](../../docs/architecture.md).

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

### Existing installations

Changes to SQL objects keep extension version `0.1.0` and ship **no upgrade
script**. This includes `morlet-energy-lowrate-v1` (new `morlet_features` and
`morlet_chunk_checkpoint` overloads, trailing `extractor` arguments) and the
bounded `morlet_read_interval` lookup. PostgreSQL stores an extension's SQL
objects in each database at `CREATE EXTENSION` time, so:

- Rebuilding the Docker image or reinstalling the shared library does **not**
  update functions in an existing database or an existing Docker volume. Old
  definitions keep running against the new library without the new arguments
  or the read fix.
- `ALTER EXTENSION pg_morlet UPDATE` does nothing, because the version did not change.
- The supported procedure is a fresh installation: `CREATE DATABASE`, then
  `CREATE EXTENSION pg_morlet`, then re-import from the source recordings. For
  the demo containers, a new volume works too (`docker compose ... down -v`
  **deletes** stored recordings).
- Replacing individual functions by hand is not a supported migration.

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

Search returns recording IDs and zero-based half-open sample intervals, ranked by aligned sample RMSE (`none`, `demean` or `zscore`), with deterministic ID/offset ties. Query length is 512..4096; stride is positive; top_k is 1..100. At most 4096 candidate windows are evaluated, otherwise the query raises an error. Windows are generated at query time; overlapping hits and the query's own source are included. NULL arguments return no rows. This legacy bounded SQL path uses aligned sample distance, distinct from the active continuous-recording feature ranking. No resampling, ANN index or cross-channel comparison is performed.

## Persisted Morlet segments

```sql
-- Owner operation: computes existing rows and stores future grids automatically.
SELECT morlet_enable_segments('recordings'::regclass, 512, 256, 'zscore');
SELECT * FROM morlet_search_features('recordings'::regclass,
  ARRAY(SELECT sin(i::float8 / 8) FROM generate_series(0,511) i),
  4096, 'vibration', 'g', '{"asset":"spindle-1"}', 'zscore', 10);
```

`morlet_grid` is a STORED generated JSONB column on the collection. Each segment records a start sample and fifteen features; the grid records `morlet-energy-v1`, sample count/rate, window size, stride and normalization. Features are extracted after normalizing **each window independently**. INSERT and sample/rate UPDATE recompute the grid atomically; DELETE removes it with the recording. Callers cannot directly overwrite a generated grid. Adding the column locks and rewrites the table; extraction errors roll back the operation. This is intended for bounded prototype datasets.

Each grid allows at most 256 complete windows, window sizes 512..4096, and positive stride. A shorter recording has an empty grid; trailing partial windows are omitted. Stored grids use ordinary Postgres storage/TOAST. `morlet_search_features` scans at most 4096 eligible stored segments and computes exact RMS Euclidean distance over the fifteen features, extracting only the query at search time. Results include the extractor version and zero-based half-open intervals. It shares metadata, rate, channel, unit, NULL and top_k behavior with `morlet_search`. Feature search suppresses overlapping hits by default; see the interval-search policy below.

A query must match the stored window size and normalization. Incompatible versions/specifications in eligible rows raise an error rather than mixing representations. To change a grid's specification, drop `morlet_grid` and call `morlet_enable_segments` again within one transaction. To recompute under the same specification, `UPDATE recordings SET samples=samples` regenerates it. Before any future extractor-version change, plan an explicit rebuild/upgrade; this unreleased extension does not promise migrations. Generated expressions are preserved by normal pg_dump/restore, which recomputes their values using the installed extractor.

`morlet_segment_grid(samples, rate, window, stride, normalization)` exposes grid construction as JSONB. `morlet_feature_vector_distance(left, right)` requires fifteen finite feature values within +/-1e9. These are separate from sample-space `morlet_distance`.

## Stored-interval queries and result policy

```sql
-- The source interval is zero-based and half-open; its samples, rate,
-- channel and unit are read under the caller's permissions.
SELECT * FROM morlet_search_recording('recordings', 1, 0, 512,
  '{"asset":"spindle-1"}', 'zscore', 10);
```

`morlet_search_recording(collection, source_id, start, end, metadata_filter, normalization, top_k, suppress_overlaps)` reads a 512..4096-sample interval, queries its features, and excludes candidate intervals overlapping that source interval in the same recording. Adjacent intervals are eligible. Source reads and candidate retrieval obey table privileges and row-level security under the caller and share the statement snapshot. Missing or invisible sources, invalid ranges and ranges beyond the recording raise errors. NULL required arguments return no rows.

`morlet_search_features` also accepts optional `source_recording_id`, `source_start_sample`, `source_end_sample` and `suppress_overlaps` arguments after `top_k`. Supply all three source fields or leave all NULL. These exclusions are geometric filters; the raw-query function does not fetch or validate the referenced source. Use `morlet_search_recording` to bind a query to actual stored samples.

Feature search now defaults to greedy overlap suppression: rank all eligible segments by distance, recording ID and start sample; accept a hit only if it does not overlap an already accepted hit in the same recording; stop after top_k accepted hits. Adjacent intervals and equal intervals in different recordings are eligible. Suppression can return fewer than top_k hits. Set `suppress_overlaps=false` to return all ranked windows. Source exclusion still applies when suppression is disabled. Selection occurs before the final top_k limit so skipped hits do not consume result slots. The 4096-segment budget and representation checks apply before source exclusion and suppression, so filters may be needed even for searches returning few hits.

This changes the default feature-search result policy in the unreleased prototype; `morlet_search` sample-distance behavior remains unchanged. See the [rollback-only psql demo](../../examples/postgres_search.sql).

Next: retrieval benchmarks and an index strategy. Feature ranking currently uses an exact scan, with no ANN or custom access method.


## Typed segment storage

```sql
SELECT morlet_enable_typed_segments('recordings',512,256,'zscore');
SELECT * FROM recordings r CROSS JOIN LATERAL unnest(r.morlet_segments) s;
SELECT * FROM morlet_search_recording('recordings',1,0,512);
```

This alternative stores a generated array of `morlet_segment` composites, each
holding `start_sample integer` and `features float8[]`. A separate generated JSONB
specification holds version/window/stride/normalization; feature numbers are
stored in binary Postgres arrays. Source samples and sample rate remain on the
recording row. Both columns update atomically with source writes. Existing rows
are backfilled under an ALTER TABLE lock.

The existing feature and stored-interval search APIs detect typed segments and
use them preferentially; JSONB-only collections continue to work. Representation
checks, candidate budgets, source exclusion, overlap suppression, caller privileges
and RLS apply to both. If both layouts exist on a collection, both incur write
cost but searches use the typed specification; normally choose one layout. To
reconfigure typed storage, drop both `morlet_segments` and `morlet_segment_spec`
and enable it again within one transaction.

Typed construction currently converts the shared JSONB extractor output into
composites during writes. It removes JSON number decoding from the retrieval path;
it does not yet optimize extraction serialization. Storage remains per-recording
arrays/TOAST, not a segment-level index or separately indexed segment table. The
benchmark compares both layouts with identical Parquet feature values.

For the continuous-stream foundation, see [chunked recordings](../../docs/chunked-recordings.md).
It does not yet change the Postgres per-row recording limit.

## Durable continuous recordings

The separate stream API persists raw chunks, typed segment rows and versioned
extractor checkpoints atomically, supports identical retries, and resumes through
new connections. See [durable ingestion](../../docs/durable-ingestion.md) for SQL,
permissions, progress semantics and limitations. Extraction is still synchronous;
this path does not change the earlier collection APIs.

