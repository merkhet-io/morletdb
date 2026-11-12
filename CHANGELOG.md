# Changelog

## 0.3.12-alpha.1

SQL version 0.3.9, with an update script from 0.3.8: run `ALTER EXTENSION pg_morlet UPDATE` after installing.
Python SDK 0.1.0a5 (reads and transfers Grizzbit 2.1 chunks). The default chunk format is unchanged (Grizzbit 1);
Grizzbit 2.1 is opt-in. Existing collections keep their layout and their value rules (`simple-v1`); new collections
get `simple-v2`.

- **Lossless chunk shipping from edge to centre (#420).** `morlet_encoded_chunks` returns a recording's stored
  chunks as bytes; `morlet_append_encoded_chunk` appends a chunk that another MorletDB encoded, keeping its bytes,
  after decoding it and running the usual append checks (offset, identical replay a no-op, conflicting samples
  refused, ingest hold, checkpoint, summary). `tools/ingest/morlet_ship.py` ships recordings with them: it creates
  the target recording with the source's parameters, resumes at the target's end, and compares the SHA-256 of all
  chunk bytes at the end (`docs/chunk-shipping.md`).
- **MCP: negative search answers state their scope, checked by a scripted conversation (#379).**
  `tools/mcp/scope_conversation.py` asks the server, through its own tools, the questions an analyst would ask of
  a collection with a period out of search, and fails when a search without a hit does not say what it searched
  and what it did not; the transcript is in `examples/mcp-scope/TRANSCRIPT.md`, and the test runs it. It found
  one weak answer: a search whose filters matched no recording said "every period of the matching recordings
  was searchable"; it now says that no recording matched and nothing was searched.
- **Alarm and PLC messages as context events (#421).** `tools/ingest/morlet_events.py` places each message of a
  log (CSV: time and message columns) as a context event on the recordings whose time range contains it, at the
  sample its time maps to (optionally only on the recordings of the message's machine), with the recording's
  measured time uncertainty (#393) where known. An identical re-import is a no-op. `decision_state` lists the
  events inside its segment (`events`: key, value, sample, offset in seconds).
- **MCP `trend` over an order key (#334).** `order_by` (a metadata key such as `cut`) orders the recordings by
  that key, numbers numerically, instead of by time. Numbered cuts of a tool, which have no clock time, then get
  a trend and change points named by the order value (`first_change_order`). Without a start time, the error
  now points to `order_by`.
- **Label glossary (#333).** `morlet_define_label(collection, label, definition, unit, source)` records what a
  label means; `morlet_label_definitions(collection)` lists the definitions. Context enabled from SQL 0.3.9 on
  gets the table; existing collections get it with their first definition. `decision_state` returns, for the
  labels it shows, their definitions (`label_definitions`) and the labels nobody defined (`undefined_labels`),
  and logged precedents carry them. In a demo session an agent had read `not_sharp` (flank wear above 0.2 mm)
  as the 0.3 mm replacement limit.
- **MCP `search_similar`: `exclude="occurrence"` (#384).** On cyclic production, the useful answer is the same cut
  on earlier parts. `occurrence` drops hits from the query's own part, cycle or run (the occurrence of the
  context segment that contains it), but keeps the other parts in the same recording, which `exclude=recording`
  drops as well. Tested on one recording of three parts.
- **`decision.finish_recordings`: build a cut's channels on several connections (#414).** Context, segments and
  values for several recordings at once, spread over the given autocommit connections (one thread each, each
  recording in its own transaction), with the same results as one by one. On a Raspberry Pi 3B+, 4
  connections took the end of a 7-channel cut from 1.21 to 0.72 s (p50, with #423); 7 connections were slower.
- **Logged decisions keep their precedents (#418).** `decision.log_decision` stores, beside the state the model
  saw, its k nearest labelled precedents (default 3): the state's examples (labelled cases from the whole
  history), else its labelled neighbours, never logged decisions, with recording, sample range, distance and
  labels. When the state held none, `precedents_note` says so. `decisions` and the MCP tool return them without
  the full state. History grows and labels change, so the precedents of that moment are part of the evidence.
  Decision log version 2.
- **Steward: integrity checks get a minimum share under load (soak S1).** On a busy edge device the 1-minute load
  stayed above the steward's gate and the builders always had new data, so `morlet_verify` never ran ("public.live
  verified never"). A due verification now gets one batch at least every 15 minutes (`--verify-share-every`),
  before the builders and despite the load gate; disk pressure still stops it. A missing chunk summary counts as
  builder backlog (`summaries_pending`), not as damage: under continuous ingest new chunks lack one for a while,
  and counting them would have raised a false alarm on every pass.
- **`morlet_add_context` no longer scans the whole context table (#414).** Every call looks its batch up by id
  (identical retries are no-ops). Without an index that was a sequential scan, growing with the collection: 33
  of 48 ms per call on a Raspberry Pi 3B+ with 27,000 context rows. Context enabled from SQL 0.3.9 on gets an
  index on `batch_id`; `morlet_index_context(collection)` adds it to existing collections (once, idempotent).
  On the Pi, one call dropped from 48 to 24 ms.
- **Segment values faster: p75 by selection, not a full sort (#414).** `simple-v1`'s 75th percentile takes the two
  order statistics it needs by selection (O(n)) instead of sorting every sample; the values are bit-identical (a
  test compares with the sort; on a Raspberry Pi 3B+ both builds wrote the same SHA-256 over all values). One
  220,000-sample segment: 4.75 → 1.44 ms on an M-series Mac. End of a 7-channel cut on the Pi (context,
  segments, values): p50 1.38 → 1.21 s sequentially, and 0.81 → 0.72 s when the channels are built on 4
  connections.
- **Clock drift is detected and reported (#393, first part).** The importer fits the rate a CSV time column
  actually shows (`time_axis.fitted_rate_hz`, `rate_error_ppm`). When the timestamps depart from start + rate by
  more than one sample, it warns per recording and in the report. MCP `locate_time` says per match how the time
  is derived (`time_basis`: start + rate) and, when an import measured the departure, the uncertainty in
  seconds and samples. Time anchors, which would correct the drift, are not implemented yet.
- **Value rules `simple-v2`: a near-zero median on a quantised history counts as zero (#387, study V2).** A p75
  history of 0, −1 and −2 converter steps had a median of half a step, so a segment at 0 deviated by exactly 1.0
  and dominated every score. Under `simple-v2`, a history whose distinct values (at least 3) lie on multiples of
  their smallest gap is quantised, and a median within two steps of zero is treated as zero (`zero_median`), in
  `morlet_compare_to_history`, `morlet_compare_summary`, `morlet_history_neighbors`, `morlet_value_spreads` and
  the decision state (`history.value_rules`). Values that are not quantised compare exactly as before, and the
  stored values are unchanged. The rules belong to the collection: `morlet_value_rules` reads them and
  `morlet_set_value_rules` sets them. Context enabled from SQL 0.3.9 on starts with `simple-v2`; existing
  collections keep `simple-v1` until switched. Study V2 (`evaluation/values_v2/`, protocol frozen before any
  run): the artefact disappears, continuous values are unchanged, and C2's and C3's AUROCs are identical under
  both rules (0 of 37,670 and 0 of 24 history medians affected). New functions: `morlet_quantised_step`,
  `morlet_value_floors`, `morlet_value_rules`, `morlet_set_value_rules`; the 0.3.8 → 0.3.9 update script adds them.
- **Importer: wide plant exports (#388).** For CSV, `--time-column` now sets the start time (ISO 8601) and,
  without `--rate`, the rate from the median row spacing. The metadata records how far the timestamps depart
  from the even axis; they are not corrected. `--context-columns program,block` stores controller columns as
  context on every signal (runs of equal value, empty cells keep the last value) and enables context on the
  collection if needed.
- **MCP `trend` per context segment (#385).** `segment_context` (a JSON object such as `{"phase": "cut"}`) takes
  the measurement points only inside current context segments whose context contains it, from each segment's
  start (a segment shorter than `samples_per_point` is measured whole). Without it, `trend` measures from each
  recording's start, which on machining data is the idle lead-in before the cut.
- **Decision state about ten times faster, all channels of a cut in one call (#403).** `decision.decision_states`
  returns the states of several segments; `decision_state` is its one-segment form. The history of all segments
  that share a context is read in one query, and medians, quantiles and neighbour distances are computed in Python
  with the SQL functions' rules (checked against `morlet_compare_to_history` and `morlet_history_neighbors` in
  `tools/mcp/test_decision_db.py`). The SQL path read the collection three times per channel and also computed the
  pooled spreads, which the state does not use. On a synthetic history shaped like a milling study (3 tools x 315
  cuts x 7 channels), seven channels took 259 ms on an M-series Mac before and 16 ms after, and on a Raspberry Pi
  3B+ 3.8 s before and 0.26 s after (median). On the real PHM 2010 milling data on the Pi (3 tools x 315 cuts,
  100 decisions of c4 and c6 late in life, soft temperature limit active), a cut's 7 states take p50 256 ms and
  p95 296 ms, from 4.7 s and 5.1 s. `decision.segments_at` looks up several segments in one query. State version 4:
  a value's status no longer reports `no_pooled_spread` (it is `ok`), since the state carries no calibrated
  deviation; values, medians, deviations, quantiles and neighbours are unchanged.
- **Decision-state examples search the whole history and say when there is none (#404).** Examples were the
  labelled cases among the 50 nearest segments, so late in a tool's life, when the nearest segments were its own
  unlabelled cuts, a labelled case further away was missed. Now the nearest labelled cases of the whole history are
  returned, `examples_note` states when none exists, and `example_match_keys` (also an MCP tool argument) lets
  examples come from a wider context, such as other tools of the same process.
- **`morlet.chunk_format`: a misspelt value fails, never writes Grizzbit 1 quietly.** PostgreSQL keeps a value set
  before pg_morlet's library loads (`SET` early in a session, `ALTER DATABASE/ROLE ... SET`, `postgresql.conf`,
  `-c`) and, for an enum setting, only warned about an invalid one and used the default. The setting is now a
  string checked by pg_morlet: once the library is loaded, `SET` refuses an invalid value; a value from before makes
  every append fail with `invalid value for parameter "morlet.chunk_format": "..."; available values: grizzbit1,
  grizzbit21`, storing nothing. Values are exact and lower case (`grizzbit1`, `grizzbit21`). An invalid
  `morlet.verify_appends`, however it was set, already failed every append; the message now names the value
  (`invalid value for parameter "morlet.verify_appends": "..."; available values: on, off`). Before a session's
  first `morlet_*` call, `current_setting` shows only explicitly set values; check with `SHOW` after one.
- **SQL 0.3.9: `morlet_encode_chunk` and `morlet_chunk_batch` are STABLE, not IMMUTABLE.** Their output depends on
  `morlet.chunk_format`, so PostgreSQL could fold or cache an IMMUTABLE call (for example in a generic plan) and keep
  bytes encoded under another setting; the samples were exact either way. Encoding works before the update; the
  update only changes the two functions' volatility.
- **Grizzbit 2.1, chunk format version 5, opt-in (#377, study B6):** `SET morlet.chunk_format = grizzbit21` makes
  every append path keep a version-5 chunk (last-place corrections once per grid index, per-chunk LPC) wherever it
  is strictly smaller than Grizzbit 1's chunk, which is stored otherwise, byte for byte. The default stays
  `grizzbit1`; existing chunks are never rewritten.
  - **Study B6 passed** all six criteria: waveforms 12.6% smaller in total and none larger, decode ≥ 53.1 and
    encode ≥ 10.8 M samples/s per thread, identical bytes on four platforms, B3 standing kept
    (evaluation/storage_b6/RESULTS.md).
  - Write-time verification (#366) covers version 5, with no fallback. Version 4 stays reserved and rejected.
  - Every reader of this release decodes version 5 whatever the setting. **Readers older than this release cannot
    read version-5 chunks**; downgrading needs them re-appended with `grizzbit1`.
  - **Compressed transfer negotiates the version:** the native decoder (`morlet-ffi`) and
    `transfer::MAX_CHUNK_VERSION` report 5; SQL's `max_chunk_version` default stays 3, so a client that states no
    version, or a lower one, receives samples.
  - **File format 1 stays Grizzbit 1:** its writer never writes version 5 and its reader refuses it.
  - The SDK labels a Grizzbit 2.1 server's encoder. `grizzbit::encode` is unchanged (Grizzbit 1 goldens and
    `testdata/grizzbit/encoder-equivalence.bin` byte for byte); `grizzbit::encode_with(.., Format::Grizzbit21)`
    writes version 5.
  - Specification: docs/chunk-format.md, "Version 5"; overview and limits: docs/grizzbit.md.

## 0.3.11-alpha.1

SQL version 0.3.8, unchanged; Python SDK 0.1.0a4. The chunk format is unchanged; existing collections keep their
layout.

- **MCP server starts under `python -I` again (#382):** `morlet_mcp.py` loads its sibling `decision.py` by path. In
  0.3.10-alpha.1 the server fails in isolated mode with `No module named 'decision'`; a test now imports it under `-I`.

- **MCP server, findings from a field evaluation (#384, #386):**
  - `search_similar` takes `exclude`: `"recording"` drops every hit in the query's recording, and `"metadata:<key>"`
    drops hits from recordings sharing the query's value of that key (e.g. the other channels of the same part). It
    fetches up to 100 candidates so that `top_k` hits remain, and reports how many were left out. The SQL option
    remains open in #384.
  - `coverage` treats recordings that overlap in time (channels of one acquisition) as one recorded block, so covered
    time is no longer added up per channel (`covered_fraction` stays at most 1), and snapshots recorded on several
    channels are recognised as snapshots. New fields: `recorded_blocks`, `recordings_per_block`.
  - `locate_time` says when a time falls into a gap, or before the first or after the last recording, and names the
    nearest recording on each side.
  - `latest` reports a too-small `limit` by that name instead of the SQL argument `max_recordings`.
- **Docs (#389):** `morlet_read_interval_raw` returns a bare `bytea`, while `morlet_read_interval` returns a table with
  `samples`; notes for Windows (no `chmod 600` on NTFS, running the tools from a container).

- **The chunk codec is now the `grizzbit` crate (Grizzbit 1 = chunk format versions 2 and 3, unchanged):**
  `crates/grizzbit` holds the encoder, decoder, CRC-32C, sample limits, unit and property tests, golden-chunk test,
  Kani proofs and the codec's fuzz targets, with no dependencies. `morlet_core::rawcodec` re-exports it, so every
  existing path keeps compiling; `morlet_core::MAX_SAMPLES` and `MAX_AMPLITUDE` are the codec's limits.
  `CodecError::Signal` now carries `grizzbit::SampleError` (same messages; `SignalError: From<SampleError>`). No
  byte changes: a new frozen vector (testdata/grizzbit), written by the encoder before the move, must be
  reproduced exactly, and SQL function names and messages are unchanged. The crate is not published.

- **Docs: the chunk codec is named Grizzbit:** [docs/grizzbit.md](docs/grizzbit.md) describes it (what it does, why it
  is lossless, where it is strong and weak in B4, lineage, versions, the planned Grizzbit 2 of #326);
  docs/chunk-format.md is retitled "Grizzbit format specification" with its content unchanged (Grizzbit 1 = chunk format
  versions 2 and 3); the README and report 0.22 (paper Section 4, supplement Section 1) name it. No format or behaviour
  change.

- **Every append path proves at write time that a chunk decodes to its input (#366):** with
  `morlet.verify_appends` on (the default), the batch append `morlet_append_chunks_raw` decoded each encoded chunk
  again and compared it bit for bit with its input, but the single-chunk append `morlet_append_chunk` (and so
  `morlet_append_chunk_raw`, `morlet_append_chunks`, and the batch append's chunk-by-chunk fallback for retries,
  overlaps and float8[] collections) stored `morlet_encode_chunk`'s output unchecked. `morlet_encode_chunk` now
  runs the same check, from one function shared with the batch path, so no SQL function changes. The codec was
  and is lossless by design and every chunk keeps its CRC-32C; this closes the gap in the write-time proof.
  Tests: the single-chunk path stores the same bytes with the check on and off, and refuses (single and batch)
  a code that does not decode to its input, which with the check off is stored, so the check is what refuses it.

- **File and server transfers verify samples, not chunk bytes (#356):** `Client.import_file` and
  `Client.export_file` compared a hash of the chunk bytes on both sides after every 256 chunks, and to continue a
  recording. A file whose chunks another encoder version wrote (the same samples as other bytes) would have been
  refused with "chunks differ after the import", although no sample was lost. They now compare a hash of the
  decoded float64 samples, every bit (hashed by the server from `morlet_read_interval_raw`), and continue a
  recording if its samples equal the other side's first samples. The chunk bytes are still compared when both
  sides report the same encoder version, and reported: `report=[...]` collects a `TransferCheck` per recording.
  Tests import, verify and continue a file written with other chunk bytes, export server chunks written with
  other bytes, and refuse a sample changed by one ulp, a changed sign of zero, and a sample changed on the way.

- **Sample rates up to 100 MHz (#342):** recordings without feature extraction (`extractor 'none'`) accept rates up
  to 100 MHz instead of 200 kHz, so acoustic emission and ultrasound (1–10 MHz) can be stored, read and summarized
  losslessly. Nothing else depended on the rate. L1's rate points at 1, 2.5, 3.125, 10 and 100 MHz: every derived
  time exact, every sample read back identical, about 7 M samples/s per writer at every rate
  (evaluation/limits_l1/results-342). Rates above 100 MHz are refused with a clear message. Search features keep
  their domains (128 Hz–200 kHz, and 1–128 Hz).

## 0.3.10-alpha.1

SQL version 0.3.8, with an update script from 0.3.7. Python SDK 0.1.0a3. The chunk format is unchanged;
existing collections keep their layout. New: the embedded edition (experimental) and file format 1.

- **Embedded edition, experimental (#351):** MorletDB in one SQLite file, with no server. Implemented in
  `crates/morlet-sqlite`, with SQLite included, as a Rust library, a C interface and the `morlet-sqlite` command line.
  In Python: `morletdb.open("x.morlet")`, which offers ingest, append, read and stats. Chunks and summaries are byte
  for byte pg_morlet's, so interval statistics are the same bit for bit (cross-checked against pg_morlet). `Client.import_file` and `Client.export_file` move recordings between a file and a server
  chunk for chunk, checking the chunk bytes on both sides. The SDK's platform wheels carry the library, and the
  release attaches `morlet-sqlite-<platform>.tar.gz` (the command line, the library and a C header).
  [docs/embedded-edition.md](docs/embedded-edition.md).
- **File format 1, a portable format for lossless waveforms (#351):** [docs/file-format.md](docs/file-format.md)
  specifies the file (tables, meaning, rules) with a compatibility promise. [docs/chunk-format.md](docs/chunk-format.md)
  now specifies decoding exactly (bit stream, prediction, integer mappings, chunk summaries).
  `reference/python/morletfile.py` reads the format with the Python standard library alone. CI checks it and the
  library against a committed corpus covering every chunk kind (`testdata/morlet-file-1`), and against fresh files
  from the library.
- **Exact times at slow rates (#343):** converting a time to a sample (`morlet_time_to_samples`) and the time filters,
  `before_only` cutoffs, hit times of search and acquisition alignment computed with `sample_rate_hz::numeric`,
  which rounds a float8 to 15 significant digits. At 1/60 Hz (0.0166666666666667, above 1/60) a time exactly at a
  sample instant mapped to the next sample; about half of all whole-second periods were affected. A rate is now read
  as the exact fraction it stands for (`morlet_rate_ratio`: 1/60, 1/7, 25600/1, or its exact decimal) and sample
  boundaries are computed in integer arithmetic. Found by the L1 limits study; a regression test checks 2,001
  sample instants at periods of 1, 3, 6, 7 and 60 s; checked once over every whole-second period from 1 to 3,600 s.
  Rates such as 25.6 kHz and 64 kHz map exactly as before. `morlet_time_to_samples` costs about 50 µs per call
  instead of 22; where it runs per row (time filters, `before_only`) the fraction is computed once per query.
- **Interval statistics on very long recordings (#340):** on a recording of 10^9 samples, statistics over 10^6
  samples took 5.4–6.1 ms instead of about 0.6 ms. The query itself took 0.1 ms; the rest was PostgreSQL compiling
  it (JIT), because the planner's row estimate, and so its cost, grew with the table until it crossed
  `jit_above_cost`. The general path (`morlet_interval_buckets`, behind `morlet_interval_stats` for long intervals and
  `morlet_interval_overview`) now runs with `jit=off` and takes 0.6–0.8 ms there. The fast path (#348, #349) already
  avoided it for intervals below 2^20 samples.
- **Faster interval statistics (#346):** `morlet_interval_stats` over an interval shorter than a roll-up block
  (2^20 samples) on a collection without memory levels now runs the chunk aggregate directly, through a new internal
  Rust function (`morlet_interval_stats_fast`) that resolves the collection's tables through the system caches and
  keeps its prepared query per session: no catalog query and no planning per call. The same rows in the same order,
  so the same result bit for bit (tested over 305 intervals against the general path, and after new samples and
  enabled summaries). Release image, 128,000-sample intervals, alternating rounds on a loaded development machine:
  server time 113 µs against 302 µs in 0.3.9-alpha.1; end to end p50 0.41 ms against 0.62 ms (a `SELECT 1` round
  trip was 0.28 ms). Other requests take the general path as before.

## 0.3.9-alpha.1

SQL version 0.3.7, with an update script from 0.3.6. Python SDK unchanged (0.1.0a2). The chunk format is unchanged;
existing collections keep their layout.

- **Extensions on demand (#315):** pg_morlet can be loaded from a directory outside the server's installation, such
  as an image volume, on PostgreSQL 18 and later: `extension_control_path` names its control and SQL files and
  `dynamic_library_path` its library. The control file's `module_pathname` is now `pg_morlet` instead of
  `$libdir/pg_morlet`, so the C functions find the library through `dynamic_library_path` (default `$libdir`:
  ordinary installations are unchanged). The update from 0.3.6 redefines the existing C functions with the new path
  (`CREATE OR REPLACE`, same OIDs; no data or table changes). Tested in CI on PostgreSQL 18 and 19
  (`tests/on_demand.sh`): the server's own copy is moved aside, and `CREATE EXTENSION`, an append, a read and
  interval statistics work from the separate directory.
- **Extension-only image (#315):** each release publishes `ghcr.io/merkhet-io/pg_morlet:<version>-pg18` (amd64,
  arm64): pg_morlet's control, SQL and library files and nothing else, under `/share/extension` and `/lib`, with build
  provenance. Mount it as an image volume (or copy its files) and set the two paths above. The release workflow
  builds it from the Debian package's build stage and trials it mounted into a clean `postgres:18` image
  (`deploy/extension_trial.sh`: install, append, read, statistics, restart) before pushing. PostgreSQL 19 follows
  19.0.

## 0.3.8-alpha.1

SQL version 0.3.6, with an update script from 0.3.5. Python SDK 0.1.0a2. The chunk format is unchanged; existing
collections keep their layout (the update pins their bulk columns to pglz, catalog only).

- **Ingest hold and the steward (#287):** `tools/steward` keeps an edge database healthy without a DBA.
  - It runs the rebuildable upkeep (envelopes, summaries, roll-ups, context values, verify, analyze) in one budgeted
    loop, gated by load and the disk.
  - It records every action and shows one plain status.
  - At the disk reserve it sets `morlet_ingest_hold`: appends that would store new samples then fail with SQLSTATE
    53100 and the reason, before PostgreSQL itself runs out of disk. It releases the hold when space is free again.
  - Its role cannot remove stored samples.
  - Changes to stored samples and extension updates are plans: proposed by the steward or a person, approved by
    another person with `morlet_approver`, and applied by a person in one transaction. Rules: no self-approval, the
    payload tied to the approved hash, 24-hour expiry, applied once; enforced in SQL.
- **Synchronous channel groups (#292):** an acquisition sampled at the same instants (one DAQ, one rate) can be
  declared synchronous (`morlet_enable_sync`, `morlet_declare_sync`). Its members are then:
  - appended together in one transaction (`morlet_append_acquisition_raw`);
  - read by sample index, failing closed (`morlet_read_sync_raw`);
  - summarised exactly across channels (`morlet_acquisition_cross_stats`: mean(a·b), such as real power, bitwise the
    same for any chunking).

  Single-member appends, partial memory moves and new channels are refused, and `morlet_verify` reports misaligned
  groups. See [docs/context-workflow.md](docs/context-workflow.md#synchronous-channel-groups-sql-036-292).
- **Exact search is 10x faster on small machines:** prepared query matrices (exact scoring and screening) are
  used only when they fit a quarter of the CPU's last-level cache; otherwise windows are scored directly, with
  bitwise the same distances.
  - On a Raspberry Pi 3B+, an N1 query (1,341 windows of 16,384 samples) drops from 15.1 s to 1.5 s.
  - Desktops are unchanged.
  - Override with `MORLET_SCORE_CACHE_BYTES`.
  - See [evaluation/edge_latency](evaluation/edge_latency/RESULTS.md).
- **Commit notifications (#205):** `morlet_enable_notifications(collection, channel, detail)` sends one
  `pg_notify` per committed transaction that stored new chunks, with the stored sample ranges per recording
  (payload version 1). Rolled-back transactions, savepoints and identical replays send nothing.
  - `detail => false` sends counts only; row security requires it.
  - Python SDK: `Client.enable_notifications`, `Client.notifications`. The SDK now needs psycopg 3.2.
  - Tested with a real listener session (`tests/notifications.sh`).
- **PostgreSQL 19 (#196, #280):** pg_morlet builds and passes its full test suite on PostgreSQL 19 (release
  candidate 19rc1; CI with and without pgvector 0.8.7), with pgrx 0.19.3 and Rust 1.99.0 (from 0.16.1 and 1.91.1).
  - PostgreSQL 19 defaults to lz4 TOAST compression. Every column holding samples, or values derived from them, now
    names pglz, and the update pins the columns of existing collections. `morlet_pin_toast_compression(collection)`
    does the same for one collection (catalog only).
  - On the B3 set, PostgreSQL 19 stores exactly the bytes PostgreSQL 17 does
    ([evaluation/toast_compression](evaluation/toast_compression/RESULTS.md)). Unpinned, lz4 would change encoded
    chunks by 0.016% and legacy float8[] chunks by −19%.
  - Release images and Debian packages for 19 follow 19.0.
- **History counts independent observations (#224):** `morlet_compare_to_history`, `morlet_compare_summary`,
  `morlet_history_neighbors`, `morlet_level_track` and the pooled spreads take history only from segments built with
  the query segment's segmentation spec (keys, occurrence keys, `min_samples`, `trim_samples`), and never from a
  segment that overlaps the query segment's own samples. Before, building one interval's segments with several
  settings made the interval count as its own history, so a comparison could report `status = ok` without any
  independent observation. History built with other settings now gives `insufficient_history` until it is rebuilt
  with the query's settings.
- **Newest data (#206):** `morlet_latest(collection, n, metadata_filter, max_recordings)` returns every matching
  recording's last `n` samples (1..32,768) with their statistics, or with `n = 0` the statistics of its newest
  stored chunk, and the end time, at a fixed number of index lookups per recording (0.4–1.6 ms per recording on
  a laptop, the same for 10 and 10,000 chunks). Unreadable newest samples (compacted, or a gap) are reported in
  `note` without failing the call. MCP tool `latest` and SDK method `Client.latest`.
- **Not found versus not searched (#215):** `morlet_coverage(collection, recording_ids, window_size)` splits
  every recording into ranges that are searchable, not indexed, out of search by a memory level, represented,
  summarized, gone or missing. The MCP server adds a `scope` to every search result (recordings and windows
  searched, the periods not searched and why, event-library entries in range), `coverage` reports the same per
  group, and the server instructions tell agents to state the scope with every negative result.
- **Search refuses over-budget queries in seconds (#279):** screened exact search counts the eligible windows,
  refuses above `max_candidates` and only then scores the kept windows, and the screen stays linear when many
  windows tie (constant windows all have distance 1). On 2 million all-zero windows the refusal took 2 min 48 s
  and now takes 2.7 s; results and latency on E7's development gallery are unchanged.
- **Idle stretches no longer slow exact search (#284):** the screen gives an exactly constant window its known
  distance 1 with no error bound, instead of an infinite bound that sent every such window to exact scoring. On a
  dev gallery where half the windows are idle, the median screened search drops from 253 ms to 65 ms; results
  are bit-identical, and galleries without idle stretches are unchanged. Nearly constant windows keep the
  infinite bound. E7's frozen protocol describes the earlier rule.
- **Exact roll-ups (#219):** `morlet_enable_rollups` and the resumable `morlet_build_rollups` store exact merges of
  chunk summaries in blocks of 2^20, 2^25 and 2^30 samples. `morlet_interval_stats` and `morlet_interval_overview`
  take the coarsest roll-ups that fit each part of the interval (and each bucket), then finer ones and chunks at
  the edges, with bitwise the same results as from the chunks alone. On a year of one 64 kHz channel (61.6M chunks; dev machine, load about 3.5) year statistics take 17 ms instead of 173 s, 365 daily buckets 137 ms instead of 170 s, one day 9 ms instead of 0.4 s, all bit for bit equal; building the roll-ups took 11 minutes. Roll-ups survive memory levels
  2 and 3, go with the samples forgotten at level 4, follow row security, and are skipped for a reader who may not
  read them.
- **Interval statistics merge exactly (#219):** each chunk's part is still summed with compensation, but the parts
  are now merged in exact arithmetic and rounded once (`morlet_core::exactstats`), so results no longer depend on
  how an interval is split or in which order pieces are merged. Mean and RMS over more than one chunk can differ
  from 0.3.5 in the last bits; the mean of a single chunk is unchanged. Stored chunk summaries stay valid.
- **Tiny signals (#228):** normalization, distances, RMS and envelopes keep their shape for amplitudes down to
  1e-300; ordinary inputs give bit-identical results.

## 0.3.7-alpha.1

SQL version 0.3.5, with an update script from 0.3.4.

- **Integrity scrub (#193):** `morlet_verify(collection, after_recording, max_recordings)` re-reads every chunk of a
  batch of recordings and reports, per recording, every problem it finds without changing data: a chunk that does
  not decode (structure, CRC-32C) to its `sample_count`, a missing or wrong chunk summary, gaps or overlaps between
  chunks, and chunks that do not end at the checkpoint. Helper `morlet_check_chunk`. Command-line wrapper
  `tools/verify/morlet_verify.py` (batches in read-only transactions, optional pause, exit status 1 on problems).
  Operations docs explain how it complements PostgreSQL's data checksums.
- **Episodic memory, phase 1 (#221, [design](docs/memory-design.md)):** `morlet_enable_memory` adds episodes, runs, a
  ledger and kept summaries to a collection; `morlet_add_episodes` registers sample intervals with context,
  importance and an exemption flag; `morlet_set_level` moves episodes down the compaction ladder (1 not searchable,
  2 represented by another episode, 3 summarized, 4 gone) in one transaction, with a dry run, removing only rows
  entirely inside the episode that no overlapping episode at a lower level needs, and writing one ledger row per
  move (what was removed, md5 of the removed chunks). Reads of compacted samples fail with the level and ledger
  entry; interval statistics use kept summaries and stay bitwise exact on chunk boundaries; the envelope builder
  skips episodes taken out of search; `morlet_verify` accepts compacted data. `morlet_memory_status` reports
  episodes per level. Policies that decide the moves come in phase 2.
- **Compressed transfer ([design](docs/compressed-transfer.md)):** `morlet_read_interval_encoded` returns an
  interval's stored chunks unchanged in one frame, under the same visibility, checkpoint and coverage checks as
  `morlet_read_interval_raw`, without decoding them (helper `morlet_frame_chunks`); chunks of a format version the
  client does not decode are sent as samples instead. New crate `crates/morlet-ffi`: the chunk decoder behind a C
  interface. The Python SDK uses it through `ctypes` for reads of at least 32,768 samples, verifies every chunk's
  checksum, and falls back to raw reads without the library or on older servers (`MORLETDB_TRANSFER`).
- **SDK wheels:** releases attach Python SDK wheels with the native decoder for Linux x86-64 and arm64 and macOS
  arm64, each installed and tested from the installed package before release, and a pure wheel for other platforms
  (`sdk/python/build_wheel.py`, workflow `sdk-wheels.yml`, also run on SDK and decoder pull requests).
- **Chunk summaries by default:** `morlet_create_stream_collection` creates the chunk table with its `summary`
  column, so every new collection stores chunk summaries and interval statistics read them instead of decoding
  (about 2% of ingestion speed and 1% of storage). Existing collections are unchanged;
  `morlet_enable_chunk_summaries` still adds them.
- **Long reads decode on worker threads:** interval reads of at least 8 chunks (`morlet_read_interval`,
  `morlet_read_interval_raw`) decode their chunks on up to `morlet.decode_threads` threads (1..64, default up to 4;
  1 = the backend's own thread). Results are identical. Development set: 128,000-sample reads 41% faster inside
  the server, 15% from the client ([reads_r2](evaluation/reads_r2/ATTRIBUTION.md)).

## 0.3.6-alpha.1

SQL version 0.3.4, with an update script from 0.3.3 (`ALTER EXTENSION pg_morlet UPDATE`; data and policies are not
modified). Fixes from the 2026-11-04 review:

- **Search budget on the probe route (#226).** `morlet_search_similar` now passes `max_candidates` to probe search
  and rejects values below 1. When probe search falls back to exact search for a small gallery, more eligible
  windows than `max_candidates` is an error, as on the exact path; on the approximate route, more candidates
  (probes × `per_probe`) than `max_candidates` is an error. `morlet_search_envelope_probes` gains a trailing
  `max_candidates` argument (default 1,000,000).
- **Concurrent identical acquisitions (#227).** `morlet_create_acquisition` and `morlet_add_acquisition_channel`
  serialize per acquisition (and per recording) with transaction-scoped advisory locks: a concurrent identical
  call returns false instead of failing with a unique violation; conflicting attributes and a role already
  taken are still refused, with a message. Row-security behaviour is unchanged.
- **Concurrent envelope builders (#225).** `morlet_build_envelopes` serializes per recording and window size, so
  two builders with different strides cannot both commit a mixed grid; an identical concurrent build resumes
  after the other's windows.
- Regression test `tests/sql034_fixes.sh` (two sessions; run by `ci_local.sh` with pgvector).

Faster single-connection ingestion (I1, #233):
- **`morlet_append_chunks_raw(collection, recording_id, start_sample, samples bytea, chunk_size)`** appends a
  run of consecutive chunks (raw little-endian float64, up to 262,144 samples) in one call; the server cuts it
  into chunks of `chunk_size`. It stores exactly what one `morlet_append_chunk_raw` call per chunk stores
  (chunks, summaries, segments, checkpoint), with the same privileges, row security and errors; retries and
  overlaps go through the per-chunk path. Helper `morlet_chunk_batch` does the per-chunk work in one call.
- **Encoding on worker threads:** `morlet_chunk_batch` encodes a batch's chunks on up to `morlet.encode_threads`
  threads (1..64, default up to 4; 1 = the backend's own thread). Output and errors do not depend on the setting.
- **Verified appends:** with `morlet.verify_appends` on (the default), each chunk of a batch is decoded again on
  the encoding threads and compared bit for bit with its input samples, so every chunk stored through
  `morlet_append_chunks_raw` is proven lossless; off relies on the codec's tests. The chunk tables' CHECK
  constraint no longer decodes chunks the append path has just encoded itself (it still decodes every other insert
  or update in full). Development set: 4–5% faster than before with verification, 6–7% without.
- **Importer:** 256 chunks per transaction by default (was 16); `--rpc-chunks` defaults to automatic (batched
  raw appends when the server has them, otherwise one chunk per call); `import_files(..., conn=...)` reuses a
  caller-owned connection; reports add `commit_wait_s`, `raw_batch` and the resolved `rpc_chunks`.

## 0.3.5-alpha.1

The same SQL version 0.3.3 and extension code as 0.3.4-alpha.1; no `ALTER EXTENSION` update is needed.

Packages for existing servers:
- Debian packages `postgresql-17-pg-morlet` and `postgresql-18-pg-morlet` for amd64 and arm64, built against
  the PostgreSQL project's packages (apt.postgresql.org) on Debian bookworm. Before release, each one is
  installed with apt into a clean official `postgres` image and runs the quickstart, a restart and the
  quickstart again. Attached with `SHA256SUMS` and a build provenance attestation per package
  (`gh attestation verify <file>.deb --owner merkhet-io`). Tested on Debian bookworm images only.
- `compose.release.yaml` takes the data directory from `MORLET_DATA_PATH` (postgres 18 images keep data under
  `/var/lib/postgresql`); the default is unchanged.

Documentation:
- Installation starts with the prebuilt release image; the docs state that it does not include pgvector.
- X2: MorletDB on a Raspberry Pi 3B+ (all nine runs pass; 78k samples/s with search features, 258k without;
  search results identical to the desktop); report 0.17.

## 0.3.4-alpha.1

SQL version 0.3.3, with an update script from 0.3.2 (`ALTER EXTENSION pg_morlet UPDATE`; the chained updates
from earlier releases are tested). Includes a security fix for collections that use
`morlet_enable_row_security` (GHSA-pg6f-hcvw-vw34): update if you use row-level security with acquisitions.
The first release with an SPDX SBOM per image architecture and build provenance attestations.

Security: acquisitions under row-level security (SQL 0.3.3):
- With `morlet_enable_row_security`, a role that could add acquisition channels could attach a recording it
  sees to an acquisition it does not see, by naming the acquisition's ID, and then read that acquisition's
  metadata (GHSA-pg6f-hcvw-vw34; affects SQL 0.3.1 and 0.3.2, releases 0.3.1-alpha.1 to 0.3.3-alpha.1). The
  foreign key was the only check. Now a channel row can be inserted or moved only into an acquisition the
  caller can see, in `morlet_add_acquisition_channel` and in the channel table's policy (direct `INSERT` and
  `UPDATE`). An acquisition is visible through a visible channel, and to the transaction that created it until
  its first channel is added: under row security, add the first channel in the creating transaction.
  `ALTER EXTENSION pg_morlet UPDATE` re-applies the policies to collections that use row security.
  Regression test: `tests/acquisition_rls.sh` (two tenants, function and direct writes, separate transactions).

Refusing oversized requests earlier (#160; results within budget unchanged):
- `morlet_compare_summary` checked `max_candidates` after flattening the whole history into one array; it now
  counts at most `max_candidates + 1` segments first. `morlet_compare_to_history`, `morlet_history_neighbors`,
  `morlet_value_spreads` and `morlet_level_track` stop counting at the budget too.
- Screened exact search: a scan that has seen more than `max_candidates` windows stops screening and only
  counts (the `morlet_envelope_screen` aggregate takes the budget). The error still states the exact number of
  eligible windows. The budget remains a candidate limit, not a time or memory limit.

MCP distance guidance (#157):
- The MCP server no longer tells agents that distances below 0.3 are strong and above 0.6 weak, which was not
  evaluated across signal types. It explains what the distance is (1 - envelope correlation: envelope shape,
  not level, frequency content or sample identity; not a probability) and to rank and inspect hits instead.
- `search_similar` adds a `query_note` when the query interval is constant (distance 1 to everything).
- docs/mcp.md: "Reading distances"; core test `distance_semantics_for_agents` (constant, scaled, other
  carrier, other modulation).

Context writes (SQL 0.3.3):
- Concurrent context writes are serialized per recording and per batch ID (transaction advisory locks,
  `morlet_lock_context`; no extra privileges). Two transactions could both commit overlapping intervals of one
  key, and an identical batch retried concurrently was stored twice; now the second waits, then sees the first
  (overlap error, or 0 for an identical retry). `morlet_build_context_segments` takes the same recording lock.
  Use READ COMMITTED for concurrent context writers (#149).
- Context added or revised after segments were built marks the segments it can change as stale
  (`morlet_invalidate_context_segments`: the spec uses the key, and the untrimmed piece overlaps the interval).
  A part annotated after the build left the segment fresh with an empty occurrence; the next build now replaces
  it, and its values are deleted with it (#150).
- Regression tests: `tests/context_concurrency.sh` (two sessions; run by `ci_local.sh` with pgvector) and
  `late_context_invalidates_the_segments_it_changes`.

Fixes from the 0.3.3 review (SQL 0.3.3):
- Replays compare bits: `morlet_append_chunk` (and `morlet_append_chunk_raw`) acknowledged a replay whose
  samples differed only in the sign of a zero, because array equality treats -0 and +0 as equal. It now
  compares the float64 bytes and raises `conflicting replay at chunk offset`. Stored samples were never
  changed (#152).
- `morlet_value_spreads` returned the 1% floor where no context qualified, because `greatest()` ignores NULL;
  the spread now stays NULL there, so comparisons report `no_pooled_spread` and the calibrated score leaves
  the value out instead of dividing by the floor (#154).
- `morlet_build_envelopes` with a stride other than the one the recording's windows were built with returned 0
  ("complete") without building the requested grid, or mixed two grids. It is now an error naming the stride
  in use (#153).
- Screened exact search raised "invalid query envelope, max_lag or candidate pool for screening" for queries
  too large to prepare (window 16384 at lag fraction 0.5; 32768 from 0.1). Those queries now score every
  eligible window directly, with the same results; `morlet_envelope_screen_supported` reports which path a
  query takes (#151).
- Python SDK: `read()` chooses raw float64 reads per collection, so collections that kept float8[] chunks after
  an extension upgrade are read with `morlet_read_interval` instead of failing (#155).

Search time filters and evidence (SQL 0.3.3):
- `morlet_search_similar`, `morlet_search_envelope_interval` and `morlet_search_envelope_probes` take
  `time_from`, `time_to` (windows whose span lies in the range) and `before_only` (windows that end before the
  query interval starts: "has this happened before?"); `morlet_search_envelopes` takes the time range. They
  use recording start times from the metadata (`start_ns`, else `start`, `starttime`, `start_time`;
  `morlet_recording_start_ns`) and leave out recordings without one. Exact search joins per-recording
  sample bounds, so the screened parallel scan is kept; probe search applies them inside the index scan,
  so it still returns full results.
- `morlet_search_similar_evidence(...)`: the same matches with their evidence inline: UTC start and end,
  count, min, max, mean and RMS of the matched interval, and an overview in equal parts.
- Python SDK: `search(..., time_from=, time_to=, before_only=, evidence=)` with `Match.evidence`. MCP:
  `search_similar` and `search_from_time` take `before_only` (and time ranges) and return each hit's rms,
  min, max and an 8-part sketch; the server reads `start_ns` as the SDK writes it.

Faster search features at ingest (features unchanged, bit for bit; extractor versions unchanged):
- `morlet-energy-v1` and `morlet-energy-lowrate-v1` build their twelve wavelet kernels once per sample rate
  instead of once per window, and compute eight coefficients side by side, each accumulated in the same
  order as before (a unit test compares every feature's bits with the previous code).

Per-chunk summaries (SQL 0.3.3, opt-in; chunks unchanged):
- `morlet_enable_chunk_summaries(collection)` adds a `summary` column to the chunk table (no table
  rewrite): count, min, max and compensated sums of each chunk in 66 bytes (`morlet_core::intervalstats`).
  Appends then store each chunk's summary; `morlet_build_chunk_summaries(collection, recording, max_chunks)`
  covers chunks stored before. Summaries follow the chunk table's privileges and row-level security.
- `morlet_interval_stats` decodes only the chunks cut by the interval edges when summaries are enabled, and
  then accepts intervals of any length (16,777,216 samples otherwise). Results are bitwise the same with and
  without summaries: every chunk's part is accumulated on its own and merged in order.
- `morlet_interval_overview(collection, recording, start, end, buckets)`: count, min, max, mean and RMS in up
  to `buckets` consecutive parts of an interval, for example a waveform overview of an hour of data
  (evaluation S1: bitwise identical results on 3,000 comparisons; statistics of 60 s at 64 kHz in 1.7
  instead of 100 ms at p95, one hour of 64 kHz data in 36 ms; ingest 4.5% slower, chunk table 1.3% larger).
- Python SDK: `create_collection` enables chunk summaries on SQL 0.3.3 servers; new `Client.overview()`.

Screened exact envelope search (SQL 0.3.3; results unchanged, bit for bit):
- `morlet_search_envelopes`, `morlet_search_envelope_interval` and the exact path of
  `morlet_search_similar` bound every eligible window's distance with a float32 screen and a rigorous error
  bound (`morlet_core::envelope_screen`), run as a parallel aggregate (`morlet_envelope_screen`) over the
  envelope table, and score exactly only the windows that could be among the nearest. The eligibility count
  for `max_candidates` shares the scan. `SET morlet.exact_screen = off` restores the previous path
  (evaluation E7: identical lists on 100/100 queries at 0.23, 0.50 and 1.03 M windows; p95
  3.2–3.7× faster than scoring every window, e.g. 138 vs 516 ms at 0.23 M windows with eight workers).

Automatic calibration-grid detection (chunks remain readable by every 0.2+ server; format unchanged):
- The encoder detects a value grid in floats sent without a declared `sample_quantum` (calibrated ADC
  counts, fixed decimals) and codes them as integers with last-place corrections, bitwise exact. On B1's six
  inputs with quantum 1 it matches the declared-quantum size (4.1–7.8× smaller than before on float inputs)
  and encodes them faster; integer inputs are unchanged (evaluation G1).

SQL 0.3.3 (update script from 0.3.2; existing functions unchanged):
- `morlet_append_chunk_raw(collection, recording, start, bytes)` appends samples given as little-endian
  float64 bytes; it calls `morlet_append_chunk`, so checks, gaps and retry semantics are the same.
- `morlet_read_interval_raw(collection, recording, start, end)` returns the samples as little-endian float64
  bytes (up to 16,777,216 per call), with the same visibility, coverage and errors as `morlet_read_interval`.
- `morlet_read_acquisition_raw(collection, acquisition, t_start_ns, t_end_ns)` returns the rows of
  `morlet_read_acquisition` with each channel's samples as little-endian float64 bytes, read in one statement
  (up to 16,777,216 samples per channel).
- `morlet_interval_stats(collection, recording, start, end)` returns count, min, max, mean and RMS computed
  while decoding, in one pass (`morlet_core::intervalstats`: exact min and max, compensated sums, RMS scaled
  against overflow, NaN propagates).

Faster ingestion and decoding (chunks unchanged, byte for byte; SQL signatures unchanged):
- The importer core sends its statements in psycopg pipeline mode (errors surface at the next commit, which
  covers the same chunks): one connection went from 2.4 to 5.8 M samples/s on the B3 development set.
- pg_morlet's append path reads `float8[]` arguments without copying them, and float8[] results
  (`morlet_decode_interval`, `morlet_decode_chunk`, `morlet_float8_from_le_bytes`) are built in one call
  instead of element by element.
- Raw codec: Rice codes read from one 64-bit window, prediction in a second pass with the order fixed, a
  slicing-by-8 CRC-32C, and per-order residual loops in the encoder (decoder checked against the previous one
  on valid, corrupted, truncated and random streams). Real vibration chunks: decode 19.8 -> 10.4 ns/sample,
  encode 72 -> 54 ns/sample.

Faster raw codec (chunks unchanged, byte for byte):
- Word-based bit reader for decoding (tested against the bit-by-bit reader); encoder search with bottom-up
  partition costs and an early-stopping Rice parameter search (tested to write identical bytes):
  154 -> 61 ns per sample to encode a 4,096-sample grid chunk on the development machine.

Faster Python client path:
- The importer core (`import_files`, used by `morlet_import.py` and the collectors) sends numpy chunks in
  PostgreSQL's binary `float8[]` format (`tools/ingest/pg_numpy.py`) instead of formatting every sample in
  Python, and as raw float64 bytes on SQL 0.3.3 servers. On the B3 development set, one connection went from
  0.51 to 2.42 M samples/s.

Industrial collection:
- `examples/collectors/opcua`: a reference collector and a local test server with a documented information
  model. A waveform delivered in numbered blocks is appended at exact sample offsets (resume after restarts and
  reconnects, identical duplicates accepted, altered duplicates refused, gaps reported with a new recording);
  scalar tags become context events with value, status code and source time. `scenarios.sh` reruns the checks.

MQTT collection:
- `examples/collectors/mqtt`: a reference collector and test publisher for the versioned `morlet-mqtt/1` JSON
  contract (sample batches and context). Messages are acknowledged after their database commit on a persistent
  session; duplicates are idempotent, altered duplicates refused, reordered batches wait, missing batches are
  reported as gaps with a new recording, and context is placed in the recording covering its time. Plain MQTT,
  not Sparkplug. `scenarios.sh` reruns the checks.

Object storage:
- `tools/ingest/morlet_s3.py` imports supported recording files from an S3-compatible bucket prefix (each
  recording records the object's bucket, key, ETag, version and SHA-256; reruns skip imported objects and a
  changed object is refused) and exports exchange files to a bucket with file and content checksums in the
  object metadata (identical exports are skipped; uploads are read back and compared). Optional dependency:
  `tools/ingest/requirements-s3.txt` (boto3). Tested against a local SeaweedFS S3 server; `docs/object-storage.md`.

Works with:
- `docs/integrations.md` lists every documented integration with how to run it, its scope and how it was
  verified (CI, release trial or manual run), plus logo and trademark notes; the homepage shows it as a plain
  text "Works with" section grouped by role.
- **Grafana:** `examples/grafana` provisions Grafana's PostgreSQL data source (read-only role) and one
  dashboard: recordings, samples, search readiness, samples over time and a bounded recording list.
- **Viewer deep links:** the viewer opens at `?collection=…&recording=…&start=…&end=…`, which the Grafana
  recording list uses.

Data exchange and TDMS:
- **Exchange format:** `tools/ingest/morlet_export.py` writes recordings to a versioned Parquet format
  (morlet-exchange 1: the importer's chunk layout plus per-recording quantum and representation, and a
  per-recording SHA-256 in the file metadata); `--verify` checks a collection against a file.
  `morlet_import.py` reads exchange files (checksums first), and `parquet_import.py` accepts optional
  per-recording `sample_quantum` and `extractor` columns. `docs/exchange-format.md` has the schema, the
  guarantees and a DuckDB recipe.
- **TDMS import:** `morlet_import.py` reads TDMS files through npTDMS, one recording per waveform channel,
  scaled values by default (`--tdms-values raw` for stored values), refusing untimed, non-numeric and empty
  channels with the reason.

## 0.3.3-alpha.1

The extension is unchanged from 0.3.2-alpha.1 (SQL version 0.3.2; no update script is needed). This release
adds a linux/arm64 image and the tools and demos for AI agents below.

Release images for arm64:
- The release workflow builds the image natively on Linux amd64 and arm64 runners, trials each from a clean
  checkout, and publishes one multi-architecture tag (plus `<version>-amd64` and `<version>-arm64`); the
  GitHub prerelease is created only after both architectures pass a trial of the registry digest.
- `compose.release.yaml` takes `MORLET_PLATFORM` (default `linux/amd64`, `linux/arm64` on ARM machines such
  as Apple Silicon). The demos use this release without a fixed platform, so Docker picks the native image.

MCP server:
- `tools/mcp/morlet_mcp.py` is a read-only MCP server for AI agents. Its tools are
  `list_collections`, `list_recordings`, `locate_time`, `describe_interval`, `search_similar` and
  `read_interval`.
  - **Safety:** it runs in read-only transactions with statement timeouts, as the connecting role,
    so row-level security applies.
  - **Errors:** they reach the agent as tool errors with the database's message.
  - **Guide:** `docs/mcp.md`, with Claude Code and Claude Desktop set-up and a worked investigation.
- **Analysis tools:** `compare_intervals` (an interval against the background around it, a median over the
  surrounding minutes, or an explicit reference, with a warning if that reference holds an onset: power ratio
  per band in dB and how the energy above the reference splits across bands) and `find_onsets` (candidate
  onsets by STA/LTA with an optional band-pass). `describe_interval` takes your own bands and a minimum
  frequency, and reports power per band. `search_similar` can also search with the window moved a little
  (`shifts`) and merge the results, and gives metadata once per recording instead of per hit. `search_from_time` finds
  the onset near a UTC time, frames the window (2 s before the onset, `shifts=2`, chosen on the demo's
  development data) and searches.
- **Overview and trend tools:** `coverage` summarises recordings per station, sensor or asset, with the
  schedule (continuous or snapshots) and the largest gaps; `trend` follows level, impulsiveness and band
  power over time and flags the first sustained departure from an early baseline as a candidate change;
  `list_recordings` is compact by default (shared fields once, metadata on request).
- **Envelope spectrum:** `envelope_spectrum` band-passes an interval around a resonance, takes the amplitude
  envelope and reports its spectrum: the strongest lines, a noise-peak level, and the level at named
  frequencies (for example bearing defect frequencies, within a tolerance) and their harmonics.
- **Machine demo, test 3:** the demo also loads IMS test 3 (every sixth snapshot, one an hour), so a fault
  signature can be searched across tests; `search_from_time` returns one hit per snapshot for snapshot data.
- **Machine demo:** `examples/machine-demo` loads NASA/IMS bearing test 2 (984 snapshots of four bearings
  until bearing 1 failed) into the release image and runs the MCP server in Docker; three recorded Claude
  sessions, and a second tab in the homepage replay.
- **Agent demo:** `examples/agent-demo` loads four days of public seismic data (CX, GEOFON) into the release
  image and runs the MCP server in Docker for Claude Code or Claude Desktop, with a scripted replay
  (`replay.py`) that needs no language model. A second, offline MCP server (`seismo_mcp.py`) gives station
  coordinates, an EMSC catalogue snapshot and predicted P and S arrivals.

## 0.3.2-alpha.1

The image contains PostgreSQL 17 and pg_morlet only, as in 0.3.1-alpha.1; probe search needs
pgvector 0.8 or later installed separately.

Search usability (SQL version 0.3.2, with an update script from 0.3.1 as released in 0.3.1-alpha.1):
- **`morlet_search_similar(collection, recording, start, end, ...)`**, one entry point for interval
  search.
  - **Routing:** it uses probe search when probe search is enabled for the window size; probe search
    itself sends small or selective galleries to exact search. Otherwise it uses exact search.
  - **Contract:** results are unchanged; the existing functions remain for choosing a path
    explicitly.
- **Exact envelope search accepts up to 1,000,000 eligible windows by default** (was 4,096) in
  `morlet_search_envelope_interval` and `morlet_search_envelopes`. One day of 100 Hz data from one
  station already has more than 4,096 windows, so a first exact query failed. With the faster scorer
  (E6), a million windows take about 1.5 s on the development machine.
- **The error states what to do:** "N windows are eligible, more than max_candidates (M)", with a hint
  to pass `max_candidates => N`, narrow the filter, or enable probe search.
- **Upgrade test:** CI now also upgrades populated databases from v0.3.1-alpha.1.

Importer:
- `tools/ingest/morlet_import.py` imports WAV, EDF/EDF+, miniSEED or CSV files, builds search
  envelopes (and, with `--probes`, enables probe search) and prints a first query.
  - **Recordings:** one per channel, per EDF signal or per contiguous miniSEED segment.
  - **Quantum from the file:** counts for WAV and miniSEED, the calibration step for EDF, the
    decimal places for CSV. Every sample reads back bit for bit.
  - **Guide:** `docs/import.md`.
  - **Measured:** a day of seismic data (8.6 M samples) took 38 s, and a Sleep-EDF night
    (24.2 M samples) 75 s.
  - `parquet_import.import_files` accepts a reader and per-recording quantum and extractor; existing
    callers are unchanged.

Performance:
- Exact envelope search scores rows about 2.3× faster, with **bitwise identical distances** and
  results (E6). Only the library changes; SQL objects are unchanged.
  - **What changed:** `morlet_envelope_distance` builds the query's shifted copies once per query
    and backend and scores each stored envelope in one pass across lags. Each lag's products are
    still added in ascending order from the same neutral element. float16 values are widened by
    bit manipulation (identical for all 65,536 codes), and the query array is read without
    copying.
  - **Measured on 234,762 development windows:** scoring all rows took 692 → 298 ms with the
    planner's three workers.
  - **More workers:** the envelope table's `parallel_workers` storage parameter lets PostgreSQL use
    more workers on larger galleries (see `evaluation/fast_exact/RESULTS.md`).

Fixes:
- Chunk encoding with a subnormal `sample_quantum` (for example 5e-324) no longer panics with
  an integer overflow in builds with overflow checks (debug builds and `cargo pgrx test`).
  - **Cause:** the encoder tries steps within two units in the last place of a fitted step and
    computed the lower ones by unsigned subtraction, which underflowed when the fitted step
    was the smallest subnormal.
  - **Release builds:** they wrapped to a step that was never chosen. An exhaustive check of
    158,976 three-sample chunks of tiny subnormals with 23 subnormal quanta found every round
    trip bit-exact before and after the fix.
  - **Encoded bytes:** identical whenever the fitted step is at least three units in the last
    place above zero. Below that, only an unusable zero or wrapped step is no longer offered.

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
