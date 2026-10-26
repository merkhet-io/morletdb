# Changelog

## Unreleased

Extension (SQL version 0.3.0, with an update script from 0.2.0 as released in
0.2.0-alpha.1; the chained update from 0.1.0 as released in 0.1.0-alpha.3 is also
tested):
- Acquisitions (context model phase 2):
  - `morlet_enable_acquisitions`, `morlet_create_acquisition` and
    `morlet_add_acquisition_channel`, with per-channel `offset_ns`, clock source and
    offset uncertainty;
  - exact time mapping: `morlet_time_to_samples`, `morlet_sample_time_ns`;
  - acquisition-time context (`morlet_add_acquisition_context`);
  - segments trimmed by clock uncertainty (`morlet_build_acquisition_segments`;
    `trim_samples` on `morlet_build_context_segments`);
  - aligned reads (`morlet_read_acquisition`);
  - per-channel comparison (`morlet_compare_acquisition`).
- `before_only` on `morlet_compare_to_history`, `morlet_compare_summary` and
  `morlet_compare_acquisition`: history only from segments that started earlier.
- `score_values` on `morlet_compare_summary` and `morlet_compare_acquisition`. The
  default is unchanged; add `p75` for level signals such as motor current.

Repository:
- Viewer: sibling channels on the same time axis; per-channel comparison; options
  for "history before this point only" and level.
- Demo: two channels per part as acquisitions.
- The 0.1.0 to 0.2.0 update script is frozen as released. The CI upgrade job also
  updates a populated 0.2.0-alpha.1 database.

## 0.2.0-alpha.1

The first release after the evidence-first research round.
- **New:** lossless, checksummed raw storage; raw-only recordings; materialized
  envelopes for search; the context model (context timeline, segments,
  compare-to-history, labels); a local viewer.
- **Evaluations behind these features** (frozen protocols, each run once on held-out
  data; see the README's measured results):
  - **Storage:** 1.7–3.3× smaller than the previous storage.
  - **Envelopes:** same results as decoding raw at about 13% of raw storage
    (R1/R1b).
  - **Context-conditioned comparison:** supported on lab milling wear (C1) and real
    CNC anomalies (C2).
  - **Calibrated score:** rejected (C3).
  - **Wavelets:** wavelet features did not beat simple baselines (H2, timing test,
    W1).
- **Upgrade:** the SQL extension moves to 0.2.0. `ALTER EXTENSION pg_morlet UPDATE`
  from 0.1.0 as released in 0.1.0-alpha.3 is tested in CI; 0.1.0-alpha.1 databases
  need a reinstall.

Extension (in the image; SQL version 0.2.0, with an update script from 0.1.0 as
released in 0.1.0-alpha.3):
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
- `morlet_version()` reports 0.2.0, the crate version, in step with the SQL
  extension version.
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

- Context model, phase 1 ([docs/context-workflow.md](docs/context-workflow.md)):
  - `morlet_enable_context`;
  - a context timeline (`morlet_add_context` with idempotent batches,
    `morlet_revise_context` with stale tracking);
  - `morlet_build_context_segments` (maximal intersections of context keys) and
    `morlet_build_context_values` (`simple-v1`, from `morlet_simple_values`);
  - `morlet_compare_to_history`, which returns relative deviation, robust z, a
    quantile and an explicit status;
  - labels (`morlet_add_label`, `morlet_retract_label`).

  Evaluation C1 (NASA milling) supports the context-conditioned comparison: ρ 0.91
  against 0.60 context-free.

- `morlet_compare_summary` (context model phase 2 start):
  - the evaluated score (maximum relative deviation over duration, std, crest and
    kurtosis), with its leave-one-out per-context quantile, the top value and the
    duration deviation;
  - `calibrated_score` as an experimental column.

  The spread-calibrated variant (`morlet_value_spreads`, `calibrated_deviation`)
  ranked worse in evaluation C3 and is not the default.

Repository (not in the image):
- Viewer (`tools/viewer`): a local, read-only web page.
  - Waveform with zoom; context, segment and label lanes.
  - Compare-to-history per segment; envelope search for a selected window.
  - Plus a synthetic demo collection.
- Evaluations with frozen protocols, results and reproduction scripts:
  - `evaluation/compression`, `evaluation/materialized` (R1, R1b);
  - `evaluation/wavelets` (W1);
  - `evaluation/context` (C1, C2, C3);
  - the decision milestone (`evaluation/decision`).
- Docs:
  - `docs/chunk-format.md`, `docs/context-model.md`, `docs/context-workflow.md`;
  - the README's measured results now list the frozen held-out tests. In these
    tests Morlet features did not beat simple baselines.

Linux amd64 / PostgreSQL 17 only. Production capacity, concurrent workloads and
performance on real shop-floor data with NC-block context remain unproven. See
SUPPORT.md and evaluation/README.md.

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
