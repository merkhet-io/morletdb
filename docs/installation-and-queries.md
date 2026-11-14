# Install and query MorletDB

MorletDB is a developer-alpha PostgreSQL extension. The SQL extension is named `pg_morlet`. Run these examples in a new disposable database; application roles should receive only the grants they need.

## Supported versions

| Component | Current scope |
| --- | --- |
| PostgreSQL 17 | Reference version; functional CI with and without pgvector; upgrades and release images target 17 |
| PostgreSQL 18 | Supported for fresh source installations; functional CI with and without pgvector; Debian packages (amd64, arm64) from 0.3.5-alpha.1; no release image or measured performance |
| PostgreSQL 19 | Supported at its release candidate (19rc1) for fresh source installations; functional CI with and without pgvector; TOAST compression pinned to pglz, so storage matches 17 and 18 ([compatibility](compatibility.md#toast-compression-postgresql-19)); no release image yet |
| Rust / pgrx | Repository-pinned Rust 1.99.0; pgrx and cargo-pgrx 0.19.3 |
| Container | Linux amd64; Linux arm64 from 0.3.3-alpha.1 (built and trialled natively in the release workflow) |
| pgvector | Optional. Probe search requires 0.8 or later; CI exercises 0.8.0 (PostgreSQL 17) and 0.8.7 (18 and 19). **Use 0.8.4 or later in production:** probe search uses HNSW indexes, and pgvector 0.8.2–0.8.4 fixed a buffer overflow in parallel HNSW builds and possible index corruption and errors during HNSW vacuuming |

This describes the current source and CI configuration, not a promise that every CI run passes. The paper benchmarks use PostgreSQL 17; compatibility tests and performance measurements are separate.

## Fastest start: the release image

Each release publishes a prebuilt image, `ghcr.io/merkhet-io/morletdb:<version>`: PostgreSQL 17.6 (Debian
bookworm) with pg_morlet installed, for Linux amd64 and arm64, with an SBOM per architecture and build
provenance ([releases](releases.md)). Nothing is compiled. Choose your own password; the port is bound to
localhost and the data is kept in the named volume `morletdb-data`.

```sh
docker run -d --name morletdb -e POSTGRES_USER=morlet -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=morlet -p 127.0.0.1:54329:5432 -v morletdb-data:/var/lib/postgresql/data \
  ghcr.io/merkhet-io/morletdb:0.3.13-alpha.1
docker exec morletdb psql -U morlet -d morlet -c "CREATE EXTENSION pg_morlet"
```

The image does not include pgvector, so [probe search](#optional-indexed-probe-search) needs a source
installation or an image you build on top of it; exact and screened search work as they are. A newer image
does not upgrade the SQL objects of an existing database: run `ALTER EXTENSION pg_morlet UPDATE` after
switching images.

**On Windows** (Docker Desktop with WSL2) the image runs as it does elsewhere. Two things differ:
- A password file such as `.env` cannot be protected with `chmod 600` on NTFS; it has no effect there.
  Restrict the file to your own user with an ACL instead, e.g. `icacls .env /inheritance:r /grant:r "%USERNAME%:R"`.
- Without Python on the host, run the importer and the MCP server from a container (for example
  `python:3.12-slim`) with their `requirements.txt`, and connect to the database through the host port.

## Docker walkthrough with synthetic data

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
cargo install cargo-pgrx --version 0.19.3 --locked
cargo pgrx init --pg17 download
cd extensions/pg_morlet
cargo pgrx run pg17
```

For PostgreSQL 18, initialize with cargo pgrx init --pg18 download and run cargo pgrx run pg18 --no-default-features --features pg18. Build only one PostgreSQL feature at a time. Probe search on 18 needs pgvector 0.8.1 or later (CI uses 0.8.7). PostgreSQL 19 (release candidate 19rc1) works the same way with pg19: register a 19rc1 build with cargo pgrx init --pg19 /path/to/pg_config, since cargo pgrx init --pg19 download fetches 19beta4. PostgreSQL 16 and older are not supported. See the compatibility matrix.

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

SELECT * FROM morlet_search_similar(
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
| Append samples | `morlet_append_chunk`, `morlet_append_chunks` | Global sample offsets; identical retries are checked; gaps and conflicting retries fail. The plural form appends several chunks in one call. With `morlet.verify_appends` on (the default), every chunk is decoded again after encoding and compared bit for bit with its input before it is stored, as on every append path (#366) |
| Append samples as bytes | `morlet_append_chunk_raw`, `morlet_append_chunks_raw` | The same append with samples as little-endian float64 bytes, without per-element parsing (SQL 0.3.3); the plural form takes a run of consecutive chunks in one call and stores exactly what one call per chunk stores (SQL 0.3.4), encoding the chunks on up to `morlet.encode_threads` threads (default up to 4; 1 = none) and, with `morlet.verify_appends` on (the default), decoding each chunk again to prove it bit for bit lossless; used by the importer |
| Chunk format of appends | `SET morlet.chunk_format = grizzbit21` | Opt-in Grizzbit 2.1 (chunk format version 5): every append path keeps a version-5 chunk where it is strictly smaller than Grizzbit 1's, which is stored otherwise; verified like any chunk with `morlet.verify_appends` on. The default, `grizzbit1`, writes versions 2 and 3 as before. Any other value makes appends fail. Reads decode every version whatever the setting; releases up to 0.3.11-alpha.1 cannot read version 5 (study B6; [how to use it](grizzbit.md#using-grizzbit-21)) |
| Float32 codings | `SET morlet.float32_chunks = off` | On by default: every append path keeps a chunk format version 6 chunk where its samples are all float32 values and it is strictly smaller than the configured format's chunk; verified like any chunk. `off` writes no version-6 chunk; any value other than `on` or `off` makes appends fail. Reads decode every version whatever the setting; releases up to 0.3.13-alpha.1 cannot read version 6 (study B8; [details](grizzbit.md#float32-codings-version-6)) |
| Read samples | `morlet_read_interval` | Reconstructs original samples, including across chunk boundaries. Returns a table: `SELECT samples FROM morlet_read_interval(...)` |
| Read samples as bytes | `morlet_read_interval_raw` | The same samples as little-endian float64 bytes, up to 16,777,216 per call (SQL 0.3.3); map them with e.g. `numpy.frombuffer(raw, "<f8")`. Returns a bare `bytea`, not a table: `SELECT morlet_read_interval_raw(...)` (there is no `samples` column) |
| Interval statistics | `morlet_interval_stats` | Count, min, max, mean and RMS computed while decoding, without building a sample array (SQL 0.3.3); min and max exact; each chunk's part is summed with compensation in float64 and the parts are merged exactly, rounding once (SQL 0.3.6), so the result does not depend on how the interval is split |
| Interval overview | `morlet_interval_overview` | The same statistics in up to N consecutive buckets of an interval, for example a waveform overview (SQL 0.3.3) |
| Chunk summaries | `morlet_enable_chunk_summaries`, `morlet_build_chunk_summaries` | Opt-in per-chunk summaries: statistics and overviews of long intervals without decoding them, with identical results (SQL 0.3.3) |
| Roll-ups | `morlet_enable_rollups`, `morlet_build_rollups` | Exact roll-ups of chunk summaries in blocks of 2^20, 2^25 and 2^30 samples: statistics and overviews of months or years read a few rows instead of every chunk summary, with bitwise the same results. Built resumably after ingestion; kept through memory levels 2 and 3 and removed with forgotten samples at level 4 (SQL 0.3.6) |
| Convert time and samples | `morlet_time_to_samples`, `morlet_sample_time_ns` | Nanosecond time ranges to sample intervals and back, for a rate and a channel offset |
| Feature search | `morlet_search_streams`, `morlet_search_interval` | Exact search over compatible Morlet/statistical feature segments, not bounded-lag envelope scoring |
| Build envelopes | `morlet_enable_envelopes`, `morlet_build_envelopes` | Opt-in materialized search representation |
| Similar-interval search | `morlet_search_similar` | Probe search when enabled for the window size, otherwise exact. Optional `time_from`, `time_to` and `before_only` (windows that end before the query starts) filter by recording time (SQL 0.3.3) |
| Search with evidence | `morlet_search_similar_evidence` | The same matches with UTC times, statistics and an overview of each matched interval inline (SQL 0.3.3) |
| Search leaving out the same part or recording | `morlet_search_similar_excluding` | The matches with evidence, choosing what is left out with `exclude`: `'window'` (the default, as above), `'occurrence'` (every context segment of the same part or occurrence, in any recording), `'recording'` (the whole source recording) or `'none'`. For cyclic production, where the useful answer is the same cut on earlier parts rather than the next cycle of the same recording. Experimental (SQL 0.3.11, #384) |
| Exact envelope search | `morlet_search_envelopes`, `morlet_search_envelope_interval` | Input samples or stored interval; bounded-lag correlation |
| Indexed envelope search | `morlet_enable_envelope_probes`, `morlet_search_envelope_probes` | Optional pgvector path with exact fallback for small galleries |
| Inspect progress | `morlet_stream_status`, `morlet_collection_status` | Recording progress, representation coverage and probe backlog |
| Inspect storage | `morlet_recording_storage`, `morlet_collection_storage` | Logical payload and physical storage accounting are distinct |
| Episodic memory | `morlet_enable_memory`, `morlet_add_episodes`, `morlet_start_memory_run`, `morlet_set_level`, `morlet_finish_memory_run`, `morlet_memory_status` | Episodes with context and importance, moved down a compaction ladder (not searchable, represented, summarized, gone) with a dry run and a ledger of every move (SQL 0.3.5; [design](memory-design.md)) |
| Newest data | `morlet_latest` | Per recording, the last n samples with their statistics, or the newest chunk's statistics, and the end time; a fixed number of index lookups per recording; unreadable newest samples are a note (SQL 0.3.6) |
| What search can find | `morlet_coverage` | Per recording, the sample ranges that are searchable, not indexed yet, out of search by a memory level, compacted, gone or missing, so a negative result can say what was not searched (SQL 0.3.6; [contract](search-contract.md#not-found-versus-not-searched-sql-036)) |
| Verify stored data | `morlet_verify` | Integrity scrub: every chunk decoded and checked, summaries compared, coverage up to the checkpoint; reports problems without changing data, in resumable batches (SQL 0.3.5; [operations](operations.md#integrity-scrub-sql-035)) |
| Enable companion-table RLS | `morlet_enable_row_security`, `morlet_row_security_enabled` | Complements recording-table policies; ordinary grants still apply |
| Operating context | `morlet_enable_context`, `morlet_add_context`, `morlet_revise_context` | Timed context events (program, block, tool, part, …); batches are idempotent; revisions and retractions keep the history |
| Context segments and values | `morlet_build_context_segments`, `morlet_build_context_values` | Cut recordings into segments by context keys and compute versioned per-segment values (duration, level, spread, peak and shape) |
| Compare with history | `morlet_compare_to_history`, `morlet_compare_summary`, `morlet_history_neighbors` | Deviation of a segment from earlier segments in the same context, a one-row summary score, and the closest past segments with their labels; `before_only` restricts history to earlier time |
| Track a level | `morlet_level_track` | Kalman level track of a segment value under the same operating conditions: sustained change versus short-lived variation |
| Labels | `morlet_add_label`, `morlet_retract_label` | Labels on sample intervals, used as data, for example to exclude known-bad history |
| Acquisitions | `morlet_enable_acquisitions`, `morlet_create_acquisition`, `morlet_add_acquisition_channel`, `morlet_add_acquisition_context`, `morlet_build_acquisition_segments`, `morlet_compare_acquisition`, `morlet_read_acquisition`, `morlet_read_acquisition_raw` | Groups of channels with their own rates and clock offsets: read one time interval from every channel (the `_raw` variant returns little-endian float64 bytes, SQL 0.3.3), and compare an occurrence across channels |
| Array primitives | `morlet_rms`, `morlet_normalize`, `morlet_distance`, `morlet_features`, `morlet_envelope_distance`, `morlet_simple_values`, `morlet_simple_value_names` | RMS, normalization, aligned sample distance, features, envelope scoring and the per-segment values are different operations |
| Versions | `morlet_version`, `morlet_extractor_version`, `morlet_envelope_version`, `morlet_simple_values_version` | Extension, feature extractor, envelope and segment-value versions, to record with derived results |

Filters use JSONB containment, for example `'{"asset":"spindle-1"}'::jsonb`. Eligibility also requires compatible rate, channel, unit and representation/window settings. Exact searches have explicit candidate budgets and fail when exceeded rather than silently searching a prefix. Returned intervals are ordered by distance, recording ID and start sample, with source exclusion and greedy overlap suppression enabled by default on stored-interval searches.

Do not directly edit managed companion tables. Versioned extractors, original samples and representations have different roles. This guide uses the active continuous-recording API, not the legacy bounded-row APIs.

## Further reading

- [Detailed search contract](https://github.com/merkhet-io/morletdb/blob/main/docs/search-contract.md)
- [Durable ingestion and envelope lifecycle](https://github.com/merkhet-io/morletdb/blob/main/docs/durable-ingestion.md)
- [Access control](https://github.com/merkhet-io/morletdb/blob/main/docs/access-control.md)
- [Context and compare-to-history](https://github.com/merkhet-io/morletdb/blob/main/docs/context-workflow.md)
- [Operations and recovery](https://github.com/merkhet-io/morletdb/blob/main/docs/operations.md)
- [Versioned releases](https://github.com/merkhet-io/morletdb/blob/main/docs/releases.md)

Installation commands and API names were checked against the current Cargo features, workflow configuration and `extensions/pg_morlet/sql/streams.sql`. CI runs the Python quickstart on PostgreSQL 17; the SQL snippets here are checked against the SQL definitions, not executed.
