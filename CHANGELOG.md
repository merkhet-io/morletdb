# Changelog

## 0.3.1-alpha.1

The image contains PostgreSQL 17 and pg_morlet only. Probe search needs pgvector 0.8 or later,
installed separately; exact search and everything else work without it.

Extension (SQL version 0.3.1, with an update script from 0.3.0 as released in
0.3.0-alpha.1):
- `morlet_collection_status(collection)`: operational status per envelope window size.
  - Recordings and raw samples.
  - Stored envelopes, recordings without envelopes, and raw samples not yet covered by an
    envelope window.
  - Probe rows and probe backlog.
  - Whether row security is applied.
  - Bytes per structure: recordings, chunks, segments, envelopes, probe table, probe index.
- `morlet_enable_row_security(collection)` (opt-in) closes a data leak in multi-tenant setups.
  - **The leak:** with RLS only on the recordings table, a restricted role that had the
    `SELECT` grants search needs could read other tenants' raw chunks, envelopes and probe
    vectors directly.
  - **The fix:** every companion table gets a policy following its recording's visibility.
    Writes for hidden recordings are rejected, and tables enabled later are covered
    automatically.
  - **Cost** on 234,622 windows: exact unfiltered search 946 → 995 ms; probe search +6 ms at
    p95.
  - See `docs/access-control.md` for roles, grants, the external-worker trust boundary and
    limits.
- `morlet_search_envelope_probes` routes and filters (evaluation SS3).
  - When at most `exact_threshold` windows are eligible (new argument, default 5000), it runs
    the exact search.
  - Otherwise each probe scans only the eligible recordings, using pgvector's iterative scan.
  - Before, eligibility was applied after probing, and recall fell to 0.52 under a filter
    leaving 1.8% of windows.
  - On held-out CX data at five filter levels it now reaches recall 0.977–1.0, with no
    results outside the filter.
- `morlet_build_envelopes` builds encoded collections in batches of up to 256 windows. It
  decodes each raw chunk once (`morlet_envelopes_from_chunks`) instead of reading every window
  through its own SQL statement, with byte-identical envelopes. On 33,736 windows of CX seismic
  data (4096 / 2048):
  - one session: 740 → 3,084 windows/s;
  - database CPU per window: 1.31 → 0.31 ms;
  - four sessions: 2,660 → 12,034 windows/s.
  Legacy `float8[]` collections keep the per-window path.
- Exact envelope search runs with parallel workers, with identical results.
  `morlet_envelope_ranked` (behind `morlet_search_envelopes` and
  `morlet_search_envelope_interval`) used to walk a cursor over every scored
  candidate, and PostgreSQL never parallelises a cursor. It now fetches the best rows
  in one statement, which PostgreSQL can parallelise. Overlap suppression then walks
  those rows, and the statement is repeated with a larger limit if suppression uses
  them up before `top_k` survive.
  - Checked on R2's CX 2025 gallery (234,762 windows, 100 queries) with four
    settings: unfiltered; one station; one station with top 100; one station without
    suppression. All 400 result lists were identical and distances bitwise equal.
  - Median / p95 latency, warm, one client, 2 workers launched, machine load
    average 6–7 during both measurements:

  | Gallery | Before | After |
  |---|---|---|
  | 234,762 windows, unfiltered | 3,323 / 3,798 ms | 1,255 / 1,341 ms |
  | one station (58,764 windows) | 780 / 896 ms | 306 / 364 ms |
  | one station, top 100 | 785 / 943 ms | 370 / 537 ms |
- Probe search for envelopes (opt-in, needs pgvector; pg_morlet itself does not
  depend on it). `morlet_enable_envelope_probes` keeps a float16 copy of one window
  size's envelopes with an inner-product HNSW index. `morlet_search_envelope_probes`
  retrieves candidates with zero-padded shifted query probes (`morlet_envelope_probe`)
  and re-ranks them with the exact scorer. `morlet_build_envelopes` writes the
  vector rows together with the envelopes, in the transaction that calls it.
  - It is approximate. Evaluation BL1 (`evaluation/bounded_lag`) used unfiltered
    galleries of about 235,000 windows: on held-out data, recall@10 0.976 against
    the exact search, at p95 131 ms instead of 2,854 ms.
  - Selective metadata filters lower recall.
- A history median that is rounding residue now counts as zero. This applies to
  values in channel units whose median is at most 1e-9 of the history's median rms.
  Before, a frozen channel's `std` median (about 1e-15) made any real `std` deviate
  by about 1e11. That dominated `morlet_compare_summary`, the calibrated score and
  `morlet_history_neighbors`. Per-value rows report such values as `zero_median`;
  `morlet_simple_value_floors` returns the per-value floors. Comparisons without
  such medians are unchanged.
- `morlet_level_track` (opt-in, read-only): a local-level Kalman filter (`local-level-v1`)
  over one `simple-v1` value per episode, by default ln(std). Episodes are the segment's
  history in its context on the same machine, up to the segment itself. It returns the
  predicted and filtered level, their variances, and innovations per episode. It is
  computed at query time and stores nothing. Process and observation variances are
  explicit arguments with no defaults. Evaluation L1 (synthetic) found it equal to an
  exponential moving average on evenly spaced episodes, with uncertainty and faster
  catch-up after gaps as its additions.
- Faster reads and envelope search, with identical results. On the R1b CX 2022
  windows (1,826 queries), every top 10 is identical in order and bitwise in
  distance. Median latency changed as follows:

  | Operation | Before | After |
  |---|---|---|
  | `morlet_read_interval` of 4096 samples | 2.9 ms | 1.1 ms |
  | Same-station envelope search | 9.4 ms | 6.5 ms |
  | Unfiltered envelope search (1,825 candidates) | 38 ms | 27 ms |

  Three changes produce this:
  - Encoded chunks are decoded and sliced in one Rust call (`morlet_decode_interval`)
    instead of being expanded to one row per sample.
  - `morlet_stream_relations` is PL/pgSQL, so its catalog query plan is cached.
  - Envelope cross-correlation scores 8 lags per pass while keeping each lag's
    summation order.

- `simple-v1` values are finite for tiny amplitudes (#51). Before, a nonconstant
  segment such as [-1e-100, 1e-100] returned kurtosis NaN, because m4 and m2^2
  underflowed to 0. Moments that would underflow are now taken on values divided
  by their largest magnitude. Values are unchanged bit for bit wherever m2^2 and
  peak^2 are normal floats, which covers any real sensor amplitude.
- Offset grids (chunk format version 3). Samples at offset + k·step, as EDF writes
  them when the physical range is not symmetric, are stored as grid indices instead
  of float bits. The 153-night Sleep-EDF example goes from 45.6 to 15.6 bits/sample
  (3.35 GB to 1.15 GB), bitwise identical. Chunks on the plain grid keep their
  version-2 bytes; the P2 benchmark data encodes byte-identically. Version-3 chunks
  are not readable by 0.3.0 and earlier.
- Search-path fixes (#77):
  - `morlet_enable_row_security` treated `_` in a collection name as a wildcard, so
    enabling it on `line_1` also replaced the policy on `line11`'s probe tables. It now
    matches only its own probe tables.
  - The router could raise "exceeds N candidates" when a window was committed between
    its count and the exact search. The exact branch no longer gets a second cap.
  - A build that started before `morlet_enable_envelope_probes` committed wrote
    envelopes without vector rows, leaving a permanent probe backlog. The builder now
    re-checks for the probe table after each insert.

Repository:
- Technical report *MorletDB: Recordings, Not Rows — Lag-Tolerant Waveform Retrieval in
  PostgreSQL*, version 0.11 (`research/paper/`, release `paper-v0.11`).
- Viewer and docs present `morlet_history_neighbors` as similar occurrences to look
  at, not a prediction: evaluation C4 (3W) found their labels name the event worse
  than the machine's last label.

## 0.3.0-alpha.1

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
- `morlet_history_neighbors`: the nearest other (or, with `before_only`, earlier)
  occurrences of a segment's context. Each comes with its values and labels.
- `morlet_search_interval` on a raw-only recording now says so, and points to
  envelope search or the context comparison, instead of a window-mismatch error.
- `morlet_version()` reports 0.3.0. A test now ties it to the SQL extension's
  default version, so the two cannot drift apart again.

Repository:
- Viewer:
  - sibling channels on the same time axis; per-channel comparison; options for
    "history before this point only" and level;
  - per-value history distributions and the nearest occurrences;
  - opt-in label entry (`--allow-labels`), guarded by a per-run token, origin and
    host checks.
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
