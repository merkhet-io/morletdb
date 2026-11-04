# Changelog

## Unreleased

Refusing oversized requests earlier (#160; results within budget unchanged):
- `morlet_compare_summary` checked `max_candidates` after flattening the whole history into one array; it now
  counts at most `max_candidates + 1` segments first. `morlet_compare_to_history`, `morlet_history_neighbors`,
  `morlet_value_spreads` and `morlet_level_track` stop counting at the budget too.
- Screened exact search: a scan that has seen more than `max_candidates` windows stops screening and only
  counts (the `morlet_envelope_screen` aggregate takes the budget). The error still states the exact number of
  eligible windows. The budget remains a candidate limit, not a time or memory limit.

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
