# Changelog

## Unreleased

Extension:
- New stream collections store raw chunks losslessly encoded (`sample_count`,
  `encoded bytea`) instead of `float8[]`. Samples on or within 1/1000 step of
  the recording's grid (`sample_quantum`, default 1) use integer prediction +
  Rice coding with last-place corrections; other chunks code float64 bit
  patterns or are stored plain. Every chunk decodes bitwise to its input.
  Measured chunk tables are 2-3.3x smaller than `float8[]` at 32768-sample
  chunks on held-out seismic and bearing data (see docs/durable-ingestion.md).
  New trailing `sample_quantum` argument on `morlet_start_recording` and
  `morlet_ensure_recording`, new `morlet_stream_storage` report, and
  `morlet_encode_chunk` / `morlet_decode_chunk` helpers. Existing `float8[]`
  collections keep working unchanged; there is no in-place migration.
- Importer: `--sample-quantum`.

## 0.1.0-alpha.3

First release built from the rewritten public repository history (author
metadata only; see docs/history-rewrite.md). 0.1.0-alpha.2 was built and
withdrawn before announcement because its source commit was re-authored; do not
use it. alpha.3 contains the same changes.

Extension (in the image):
- Opt-in `morlet-energy-lowrate-v1` representation for native rates in [1,128) Hz,
  with identical math to v1. It adds version-selected `morlet_features` and
  `morlet_chunk_checkpoint` overloads and trailing `extractor` arguments on
  recording creation and stream search. Stored-interval search uses and returns
  the source recording's version. v1 defaults and frozen fixtures are unchanged.
- `morlet_read_interval` uses a bounded chunk lookup: about 10 ms instead of
  0.3-0.5 s for reads deep into multi-day recordings. Semantics are unchanged.

Repository (not in the image):
- `examples/seismic`, a reference application replaying public GEOFON data,
  with a reproducible evaluation in which pooled Morlet beats or ties the
  temporal representation. Its CI job runs synthetic tests against a fresh
  extension install.
- Offline wave-index PoC, visual notebook, removal of the legacy Go runtime,
  demo catalog, and CNC and wave-RAG specifications.

Still `pg_morlet` 0.1.0 with **no upgrade script**: databases created with
0.1.0-alpha.1 keep their old SQL functions; install into a fresh database.
Linux amd64 / PostgreSQL 17 only. Retrieval quality beyond simple baselines and
production capacity remain unproven. See SUPPORT.md and evaluation/README.md.

## 0.1.0-alpha.1

First packaged developer preview of the Rust/PostgreSQL recording workflow:
continuous chunk ingestion, transactional checkpoints and replay, Parquet import,
stored-interval search, overlap suppression, raw interval reconstruction and a
thin Python client. Includes numerical fixtures, SQL contracts, recovery tests,
synthetic benchmarks and a limited CWRU relevance baseline.

Linux amd64 / PostgreSQL 17 only. Fresh installations; no upgrade migration.
Exact synchronous retrieval and extraction. Morlet differentiation and production
capacity remain unproven. See SUPPORT.md and evaluation/README.md.
