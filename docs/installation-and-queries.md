# Install and query MorletDB

MorletDB is a developer-alpha PostgreSQL extension. The SQL extension is named `pg_morlet`. Run these examples in a new disposable database; application roles should receive only the grants they need.

## Supported versions

| Component | Current scope |
| --- | --- |
| PostgreSQL 17 | Reference version; functional CI with and without pgvector; upgrades and release images target 17 |
| PostgreSQL 18 | Supported for fresh source installations; functional CI with and without pgvector; no release image or measured performance |
| Rust / pgrx | Repository-pinned Rust 1.91.1; pgrx and cargo-pgrx 0.16.1 |
| Container | Linux amd64 is the documented verified target; other container architectures are unverified |
| pgvector | Optional. Version 0.8.0 is exercised by CI; probe search requires 0.8 or later |

This describes the current source and CI configuration, not a promise that every CI run passes. The paper benchmarks use PostgreSQL 17; compatibility tests and performance measurements are separate.

## Fastest start: Docker walkthrough

Install Git and Docker with Compose v2, then run:

```sh
git clone https://github.com/merkhet-io/morletdb.git
cd morletdb
docker compose run --rm walkthrough
```

The first run builds the extension image and installs Python dependencies. It imports synthetic recordings, searches a known interval and reads matches back. Internet access is required. The synthetic data is a functionality demonstration, not evidence of diagnostic accuracy. The default walkthrough does not install pgvector.

To open SQL in the running demo database:

```sh
docker compose exec db psql -U morlet -d morlet
```

The demo listens on localhost port 54329, database/user `morlet`, password `morlet-demo-only`. These are public local-demo credentials. `docker compose down` preserves the named volume; adding `-v` deletes its data. A new image does not automatically upgrade SQL objects in an existing database.

## Build a development installation

Install Rust through rustup. On Debian/Ubuntu, install the build prerequisites below. Run pgrx as a non-root OS user; only the apt command needs administrator privileges.

```sh
sudo apt-get install build-essential bison flex libreadline-dev zlib1g-dev libclang-dev clang pkg-config libssl-dev
git clone https://github.com/merkhet-io/morletdb.git
cd morletdb
rustup show
cargo install cargo-pgrx --version 0.16.1 --locked
cargo pgrx init --pg17 download
cd extensions/pg_morlet
cargo pgrx run pg17
```

For PostgreSQL 18, initialize with cargo pgrx init --pg18 download and run cargo pgrx run pg18 --no-default-features --features pg18. Build only one PostgreSQL feature at a time. Probe search on 18 needs pgvector 0.8.1 or later (CI uses 0.8.7). PostgreSQL 16 and older are not supported. See the compatibility matrix.

This builds a user-owned PostgreSQL 17 installation and opens psql. In the selected database:

```sql
CREATE EXTENSION IF NOT EXISTS pg_morlet;
SELECT morlet_version();
```

`CREATE EXTENSION` enables already-installed extension files; it does not download or compile the extension. No `shared_preload_libraries` entry is required. Hosting providers must permit installation of this extension. For packaging against an existing PostgreSQL 17 installation, use `cargo pgrx package --pg-config /absolute/path/to/pg_config`; installation into server directories requires suitable filesystem privileges.

## Store, search, read back

The following example creates its own collection, independent of the Docker walkthrough. Run it once in a fresh database with pg_morlet installed. It stores two synthetic recordings as raw-only inputs, then builds envelopes explicitly. This is the envelope-search path; it does not create Morlet feature segments.

```sql
SELECT morlet_create_stream_collection('public', 'getting_started');

SELECT morlet_start_recording(
  'public.getting_started', 'run-a', 4096, 'vibration', 'g',
  '{"asset":"spindle-1"}'::jsonb, 512, 256, 'zscore', 'none'
);
SELECT morlet_start_recording(
  'public.getting_started', 'run-b', 4096, 'vibration', 'g',
  '{"asset":"spindle-1"}'::jsonb, 512, 256, 'zscore', 'none'
);

SELECT morlet_append_chunk(
  'public.getting_started', 'run-a', 0,
  ARRAY(SELECT sin(i::float8 / 8) * (1 + 0.4 * sin(i::float8 / 300))
        FROM generate_series(0, 8191) AS i ORDER BY i)
);
SELECT morlet_append_chunk(
  'public.getting_started', 'run-b', 0,
  ARRAY(SELECT sin(i::float8 / 8) * (1 + 0.4 * sin(i::float8 / 300))
        FROM generate_series(0, 8191) AS i ORDER BY i)
);

SELECT morlet_enable_envelopes('public.getting_started');
SELECT morlet_build_envelopes('public.getting_started', 'run-a', 4096, 2048);
SELECT morlet_build_envelopes('public.getting_started', 'run-b', 4096, 2048);

SELECT * FROM morlet_search_envelope_interval(
  'public.getting_started', 'run-a', 0, 4096,
  '{"asset":"spindle-1"}'::jsonb, 10
);

SELECT * FROM morlet_read_interval(
  'public.getting_started', 'run-b', 0, 4096
);
SELECT * FROM morlet_stream_status('public.getting_started', 'run-a');
SELECT * FROM morlet_collection_status('public.getting_started');
```

There are fewer than ten admissible, non-overlapping intervals here; fewer than ten results is expected. Sample coordinates are zero-based, half-open: `[0,4096)` contains 4096 samples. Source exclusion removes overlapping intervals in the source recording, not every interval of that recording. Smaller distances indicate stronger similarity under the chosen scorer; distances are not probabilities.

## Optional indexed probe search

Install pgvector server files separately for the same PostgreSQL installation, then enable it in the same database. The commands below continue the example above.

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT morlet_enable_envelope_probes('public.getting_started', 4096);
SELECT * FROM morlet_search_envelope_probes(
  'public.getting_started', 'run-a', 0, 4096,
  '{"asset":"spindle-1"}'::jsonb, 10
);
```

The function routes small eligible galleries (at most 5000 windows by default) to exact search, so this tiny example exercises that fallback. On larger galleries it unions shifted HNSW candidates and re-ranks them with the envelope scorer: candidate selection is approximate even though returned distances are recomputed exactly on stored envelopes. It may return fewer than top_k after exclusions because the per-probe budget is fixed.

Envelopes are opt-in and are not automatically built by append. Call the bounded, resumable builder until the intended windows are covered. Once probes are enabled, the builder writes corresponding vector rows in the same transaction. To publish appended samples and new representations together, explicitly put append and build calls in one transaction. Under READ COMMITTED, the multi-statement probe function does not promise one atomic search snapshot.

## Main functions and query primitives

| Task | Entry points | Meaning |
| --- | --- | --- |
| Create a collection | `morlet_create_stream_collection` | Creates managed recording, chunk and segment tables |
| Declare a recording | `morlet_start_recording`, `morlet_ensure_recording` | Fixes rate, channel, units and extraction configuration |
| Append samples | `morlet_append_chunk` | Global sample offsets; identical retries are checked; gaps and conflicting retries fail |
| Read samples | `morlet_read_interval` | Reconstructs original samples, including across chunk boundaries |
| Feature search | `morlet_search_streams`, `morlet_search_interval` | Exact search over compatible Morlet/statistical feature segments, not bounded-lag envelope scoring |
| Build envelopes | `morlet_enable_envelopes`, `morlet_build_envelopes` | Opt-in materialized search representation |
| Exact envelope search | `morlet_search_envelopes`, `morlet_search_envelope_interval` | Input samples or stored interval; bounded-lag correlation |
| Indexed envelope search | `morlet_enable_envelope_probes`, `morlet_search_envelope_probes` | Optional pgvector path with exact fallback for small galleries |
| Inspect progress | `morlet_stream_status`, `morlet_collection_status` | Recording progress, representation coverage and probe backlog |
| Inspect storage | `morlet_recording_storage`, `morlet_collection_storage` | Logical payload and physical storage accounting are distinct |
| Enable companion-table RLS | `morlet_enable_row_security` | Complements recording-table policies; ordinary grants still apply |
| Array primitives | `morlet_rms`, `morlet_normalize`, `morlet_distance`, `morlet_features`, `morlet_envelope_distance` | RMS, normalization, aligned sample distance, features and envelope scoring are different operations |

Filters use JSONB containment, for example `'{"asset":"spindle-1"}'::jsonb`. Eligibility also requires compatible rate, channel, unit and representation/window settings. Exact searches have explicit candidate budgets and fail when exceeded rather than silently searching a prefix. Returned intervals are ordered by distance, recording ID and start sample, with source exclusion and greedy overlap suppression enabled by default on stored-interval searches.

Do not directly edit managed companion tables. Versioned extractors, original samples and representations have different roles. This guide uses the active continuous-recording API, not the legacy bounded-row APIs.

## Further reading

- [Detailed search contract](https://github.com/merkhet-io/morletdb/blob/main/docs/search-contract.md)
- [Durable ingestion and envelope lifecycle](https://github.com/merkhet-io/morletdb/blob/main/docs/durable-ingestion.md)
- [Access control](https://github.com/merkhet-io/morletdb/blob/main/docs/access-control.md)
- [Context and compare-to-history](https://github.com/merkhet-io/morletdb/blob/main/docs/context-workflow.md)
- [Operations and recovery](https://github.com/merkhet-io/morletdb/blob/main/docs/operations.md)
- [Versioned releases](https://github.com/merkhet-io/morletdb/blob/main/docs/releases.md)

Installation commands and API names were checked against the current Cargo features, workflow configuration and `extensions/pg_morlet/sql/streams.sql`. The examples are source-checked; they were not executed against PostgreSQL in the website editing environment.
