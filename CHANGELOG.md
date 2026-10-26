# Changelog

## Unreleased

Extension (SQL version 0.2.0, with an update script from 0.1.0 as released in
0.1.0-alpha.3):
- New stream collections store raw chunks losslessly encoded (`sample_count`,
  `encoded bytea`) instead of `float8[]`. Samples on or within 1/1000 step of
  the recording's grid (`sample_quantum`, default 1) use established fixed
  polynomial prediction + Rice coding with last-place corrections; other chunks
  code float64 bit patterns or are stored plain. Every chunk carries a CRC-32C
  checksum that is verified before samples are returned, and decodes bitwise to
  its input. Format: docs/chunk-format.md.
- Measured chunk tables (physical size) are 1.9-3.3x smaller than the previous
  `float8[]` storage at 32768-sample chunks and 1.7-2.9x at 4096, on held-out
  seismic and bearing data. This is relative to previous MorletDB storage, not
  to optimized Parquet.
- New trailing `sample_quantum` argument on `morlet_start_recording` and
  `morlet_ensure_recording`; new `morlet_recording_storage` (per-recording value
  sizes) and `morlet_collection_storage` (physical table sizes) reports;
  `morlet_encode_chunk` / `morlet_decode_chunk` / `morlet_encoded_chunk_kind`.
- `ALTER EXTENSION pg_morlet UPDATE TO '0.2.0'` keeps existing `float8[]`
  collections unchanged and working; re-encoding them is a separate, documented
  data transfer. alpha.1 databases need a reinstall (docs/durable-ingestion.md).
- Importer: `--sample-quantum`.
- Raw-only recordings: `extractor => 'none'` stores samples without segments
  or features. It accepts any rate up to 200000 Hz and is skipped by feature
  search. The importer accepts `--extractor none`. Library-only change; the
  default is unchanged.

- Materialized envelopes (`xc-envelope-f16-v1`): opt-in per collection
  (`morlet_enable_envelopes`), built resumably from raw (`morlet_build_envelopes`),
  searched exactly with server-side scoring (`morlet_search_envelopes`,
  `morlet_search_envelope_interval`). They are a rebuildable representation, not
  an index. Evaluated in R1/R1b:
  - same results as decoding raw;
  - about 13% of the raw chunk table at 4096-sample windows.

  `morlet_collection_storage` gains `envelope_table_bytes`.

Docs: the README's measured results now list the frozen held-out tests (Paderborn,
seismic timing, PTB-XL, storage, materialized envelopes). In these tests Morlet
features did not beat simple baselines.

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
