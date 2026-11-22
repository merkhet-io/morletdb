# Python SDK

`sdk/python` is a small, synchronous client for MorletDB: ingest a regularly sampled NumPy waveform with its
timing and metadata, read exact sample intervals, search for similar intervals, and resolve every result to
the stored samples. It wraps pg_morlet's SQL functions and adds no database features of its own.

- Package: `morletdb` 0.1.0b3 (beta), Python 3.10+, depends on `numpy` and `psycopg` 3.2 or later only; plotting
  (`[plot]` extra, matplotlib) is optional and used only by the example.
- Not on PyPI yet: install a release wheel ([releases](releases.md#install-the-python-sdk)), or
  `pip install "./sdk/python[plot]"` from a checkout.
- Example: [`examples/python/quickstart.py`](../examples/python/quickstart.py), synthetic data, no download.
- Servers: pg_morlet SQL 0.3.2 (release image `0.3.3-alpha.1`) and later. On SQL 0.3.3 (release image
  `0.3.4-alpha.1`) the SDK sends and
  reads samples as raw float64 bytes, offers `stats` and `overview`, and `create_collection` enables per-chunk
  summaries; on 0.3.2 it uses binary `float8[]` arrays instead
  (same results, more server-side parsing) and `stats` and `overview` raise `UnsupportedError`.
- Compressed transfer (SQL 0.3.5, [design](compressed-transfer.md)): reads of at least 32,768 samples fetch the
  stored chunks still encoded and decode them in the client with MorletDB's own Rust decoder, a small shared
  library (`crates/morlet-ffi`) loaded with `ctypes`: about a sixth of the bytes on the network, and the server
  does not decode. Every chunk's checksum is verified in the client, and the result is bit for bit the same as a
  raw read. Without the library or on older servers the SDK reads raw samples as before. Releases attach wheels
  with the library for Linux x86-64 and arm64 (glibc 2.34 or later) and macOS 11+ on arm64, and a pure wheel for
  everything else (`sdk/python/build_wheel.py`, workflow `sdk-wheels.yml`); from a checkout, build
  `crates/morlet-ffi` and set `MORLETDB_NATIVE_LIB=/path/to/libmorlet_ffi.*`. `MORLETDB_TRANSFER=auto|encoded|raw`
  forces a path for tests and benchmarks. Corrupt stored data is raised as `DatabaseError`, never retried.

## First run

```sh
docker run -d --name morletdb -e POSTGRES_USER=morlet -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=morlet -p 127.0.0.1:54329:5432 ghcr.io/merkhet-io/morletdb:0.4.0-beta.4
docker exec morletdb psql -U morlet -d morlet -c "CREATE EXTENSION IF NOT EXISTS pg_morlet"
python -m venv .venv && . .venv/bin/activate && pip install "./sdk/python[plot]"
DATABASE_URL=postgresql://morlet:change-me@127.0.0.1:54329/morlet python examples/python/quickstart.py
```

The example stores two recordings of two minutes each at 8 kHz with "impacts" and "electrical transients" at known
times, reads 0.5 s of the first impact by time, searches from it, prints the ranked matches with their time
and generated event, and saves `quickstart.png` (the query and its three best matches). On the release image
it runs in a few seconds; the four impacts rank first (distance ≈ 0.01), the electrical transients follow
(≈ 0.16–0.19).

## Investigate an interval

**Experimental, from 0.4.0-beta.4.** The method and its version-1 result may still change
between releases; changes are listed in the changelog. `Client.investigate` composes existing SQL search, query
statistics, recording coverage and optional context comparison in one read-only snapshot. This is the first SDK slice of
[#572](https://github.com/merkhet-io/morletdb/issues/572), not a diagnosis or a durable evidence archive.
It needs SQL 0.3.3 or later for interval statistics; coverage is available from 0.3.6 and optional context
comparison needs 0.3.9 or later. It does not create representations or change data.

```python
import json
import morletdb

# Use a separate connection if the application has pending writes.
with morletdb.Client.connect("postgresql://...") as db:
    report = db.investigate(
        "public.signals", "pump-7/accel-x", 40960, 45056,
        metadata_filter={"asset": "pump-7"}, top_k=5,
        timeout_ms=10000, max_candidates=10000,
    )
    for hit in report.search.matches:
        print(hit.distance, hit.ref)
    print(report.coverage_status, report.notes)
    payload = report.to_json()      # the encoding already measured against max_output_bytes
```

The interval must be a power-of-two window of 512–32768 samples supported by the collection's search
representations. `end_sample` defaults to `start_sample + 4096`. Original samples are not included or
resampled; `read_ref` is a separate operation and therefore observes its own transaction state.

The typed `InvestigationResult` (version 1) contains:

- `recording`, `search`, `query_stats`: query specification/metadata, ranked waveform matches and
  interval statistics using the existing SDK result types.
- `context`: optional `InvestigationContext`, or `None` when not requested. Supply `segment_id` for a
  current context segment containing the entire query interval in the same recording. It returns the
  segment's full interval, context, occurrence, segmentation spec and value-rule version, named SQL
  comparison rows, nearest history neighbors and glossary definitions for labels on those neighbors.
- `coverage_status` and `coverage`: `available` with recording ranges and their states, or `unknown`
  if the server lacks the coverage API. Ranges cover caller-visible recordings matching the query's
  rate/channel/unit and `metadata_filter`, before source exclusion. They do **not** count windows actually
  scored, prove probe-index completeness or measure approximate-search recall. See the
  [search contract](search-contract.md#not-found-versus-not-searched).
- `coverage_summary`: samples per coverage state and the number of ranges; with `coverage_ranges=False` the
  ranges themselves are left out (and do not count against `max_output_bytes`).
- `evidence_states`: machine-readable counterparts of the notes: `no_temporal_cutoff`, `no_matches_returned`,
  `approximate_candidates_possible`, `coverage_unknown`, `unsearched_ranges`, `context_not_requested`,
  `labelled_history_not_exhaustively_searched`, `insufficient_history`, `undefined_labels`.
- `settings`, `provenance`, `notes`: effective API settings; in `provenance` the envelope representation
  (with the extension version that defines it) and, with context, the value rules read from the server
  (`simple-v1` or `simple-v2`); database/collection identity within this database, caller role, SQL extension version, snapshot identifier and interpretation
  limits. The snapshot identifier is diagnostic, not a token that can reopen the snapshot later.

Context comparison calls `morlet_compare_to_history` and `morlet_history_neighbors`; it adds no new
numerical implementation. `match_keys` (default all segment context keys) and `exclude_labels` (default
none) apply **only to context history**, not waveform search. `min_history` defaults to 5. Per-value
statuses are kept, and small histories are explicitly noted. A context segment can be longer than the
query window: its deviations describe that whole segment. Neighbors' labels describe those occurrences,
not the query. Missing glossary definitions are reported. This first slice does not search all labelled
history for precedents; an unlabelled neighbor list does not prove that labelled cases are absent.

Waveform search excludes source-overlapping windows and suppresses overlapping results. Both search
and context comparison use **no temporal cutoff** in this first API. Later occurrences and labels added
after the event can participate; do not use it as a causally valid historical decision replay. The
existing separate `search` API retains its time filters. Context neighbor scores and waveform distances
are separate rankings and must not be compared numerically.

### From an asset and a time to an interval

**Experimental, from the release after 0.4.0-beta.4.** Evidence bundles record an investigation so that it can be
checked later:

```python
bundle = db.export_evidence("public.line3", "r1", 8192, 12288, include_samples=True)
bundle.write("case-42")                                   # manifest.json, report.html, samples/
morletdb.verify_evidence("case-42").ok                    # offline: manifest, report and sample digests
db.resolve_evidence("case-42")                            # each interval now: unchanged, changed, missing, ...
```

The manifest digest covers its canonical JSON; sample digests cover little-endian float64 bytes, so signed zero is
preserved. To detect someone who edits a bundle and recomputes its digest, keep `manifest_sha256` elsewhere and pass it
as `expected_manifest_sha256` to `verify_evidence` or `resolve_evidence`. Bundle directories are private to their owner,
and a bundle with some raw samples unavailable is labelled `partial-samples`. Without `include_samples`, a bundle holds digests only: they detect a change but cannot
restore data. Exporting discloses what this role can read and cannot be revoked later. Limits raise instead of producing
a partial bundle. A bundle does not pin data against retention or compaction, and it is not a diagnosis.

**Experimental, from the release after 0.4.0-beta.4; needs SQL 0.3.15 or later.** `Client.resolve_incident` maps an
asset and an incident time to recording intervals in one read-only snapshot, with the same transaction rules as
`investigate`:

```python
from datetime import datetime, timedelta, timezone

when = datetime(2026, 3, 1, 14, 32, tzinfo=timezone(timedelta(hours=1)))   # naive datetimes are refused
found = db.resolve_incident("public.line3", "P7", when, before_s=30, after_s=5, asset_key="machine",
                            identity_keys=("plant",), references={"work_order": "WO-17"},
                            reference_keys={"work_order": "wo"})
print(found.states)                        # every ambiguity, gap and missing time axis, by name
for c in found.candidates:
    print(c.ref, c.basis, c.start_uncertainty_ns, c.covers_window, c.references)
```

The window is `[incident - before_s, incident + after_s)`, rounded outward to whole nanoseconds. A candidate holds
the samples whose UTC time lies in it, by the recording's time anchors or start + rate. Recordings are never
concatenated or resampled, and nothing is chosen for you. No candidates is not "nothing happened":
`asset_not_found`, `recordings_without_time_axis` and `no_recording_at_time` are different findings. The states are
listed in the [MCP guide](mcp.md#from-a-machine-and-a-time-to-an-interval).

### Investigation transactions and limits

On an idle connection the method opens and finishes its own `REPEATABLE READ READ ONLY` transaction,
including on an autocommit connection. If a transaction is already active, it must be read-only and
`REPEATABLE READ` or `SERIALIZABLE`; otherwise the call raises `InvalidInputError` without committing or
rolling back caller work. A compatible caller transaction is used through a savepoint. Failures roll back
only that savepoint, and the caller's statement timeout is restored. Connection settings such as
autocommit are not changed. Do not share the connection with concurrent operations during investigation.

The default limits are 10 seconds overall, 10,000 search candidates, 10,000 context-history segments,
1,000 compatible recordings, 10,000 coverage ranges and 2,000,000 bytes of compact UTF-8 JSON output.
They are configurable with `timeout_ms`, `max_candidates`, `max_history`, `max_recordings`,
`max_coverage_ranges` and `max_output_bytes`. `max_coverage_ranges` bounds the ranges returned, not the
server's work: `morlet_coverage` computes every range of the compatible recordings, which `max_recordings`
bounds. `max_history` bounds the context history; the pooled value spreads that the context comparison
computes have the server's own cap of 100,000 segments, so a history beyond that fails with
`InvestigationLimitError` whatever `max_history` allows. `top_k` (1–100) bounds both returned matches and context neighbors.
Candidate budgets retain the existing SQL semantics, including probe routing; they are not physical I/O
or total-memory limits. The final output-size check is after assembly, not a streaming memory cap.

The remaining overall deadline is the statement timeout (or the caller's shorter timeout). It is resent
when the deadline has moved closer than a tenth of it, at least 50 ms, not before every statement, so a
statement can overrun the deadline by at most that slack; the deadline is checked again after every query
and after serialization. A statement ended by the deadline or a statement timeout, and every server budget
error ("history exceeds", "more than max_candidates", "... exceeds N segments"), raises
`InvestigationLimitError`; no partial report is returned as complete. A cancel request from another session
or thread is not a limit: it raises the translated database error. A context `segment_id` is checked before
the search runs, so a wrong or stale segment fails at once. A query interval past the recording's end raises
`NotFoundError`, as `read`, `search` and `stats` do. `CoverageRange.windows` is `None` for every state but
`searchable`.
Other SQL errors retain the SDK's error translation, and permission errors remain psycopg permission
errors. Unknown coverage, insufficient history and undefined labels are explicit result states/notes;
unreadable query samples or invalid context segments raise instead of silently dropping those stages.

`to_dict()` is JSON-compatible and maps non-finite diagnostic numbers to `None`; `to_json()` returns it as
compact JSON, the encoding measured against `max_output_bytes`, serialized from the current fields. It captures observations
but does not freeze source samples, preserve deleted evidence or provide content hashes. Viewer/MCP
adoption of this shared API and archival report export are follow-up work.

## The recording model, as the SDK exposes it

- **Recording:** one channel of one acquisition, uniformly sampled. Identity: `(collection, recording_id)`;
  attributes: `sample_rate_hz`, `channel`, `unit`, `metadata` (JSON object), `samples` (stored count).
- **Samples** are stored losslessly as float64 and read back bit for bit. `ingest` accepts 1-D float (16, 32,
  64 bit) and integer arrays and widens them exactly; it refuses 2-D, complex, boolean, object and empty
  arrays, NaN and infinities, and values beyond ±10⁹ (pg_morlet's range). It never resamples, normalizes or
  reduces precision.
- **Intervals** are half-open sample ranges `[start_sample, end_sample)`: `read(c, r, 4096, 8192)` returns
  4,096 samples, starting with sample 4096. Sample numbers are the exact coordinates; an `IntervalRef`
  (collection, recording, start, end) is a stable reference you can store and read later.
- **Time.** `start_time` is the UTC time of sample 0: a timezone-aware `datetime` (naive ones are refused)
  or integer nanoseconds since the Unix epoch. It is stored in the metadata as `start_ns` (exact) and `start`
  (ISO 8601, UTC, nanoseconds), so the MCP server and other tools read it too. Sample *n* is at
  `start + n / sample_rate_hz`. `Recording.start_time` is a `datetime` (microseconds, as `datetime` allows;
  `start_ns` is exact). `samples_for_time(rec, t0_ns, t1_ns)` maps a time range to the samples at or after
  each time. When the recording has [time anchors](clock-drift.md) (SQL 0.3.13; the importer writes them from a
  source's own timestamps), it uses them by default (`basis="auto"`); `basis="start+rate"` keeps the mapping by
  start time and rate (the server's `morlet_time_to_samples`), and `basis="anchors"` requires anchors. A range
  that a backward step of the source's clock maps to several disjoint sample ranges raises `InvalidInputError`:
  `time_ranges(rec, t0_ns, t1_ns)` returns every range as a `TimeRange` with its basis, `ambiguous`,
  `extrapolated` and the time error at its first and last sample when one is known: a bound, unless
  `assumes_linear_clock` (estimated anchors without a clock bound), and `None` for `start+rate`. Like
  `samples_for_time`, ranges are offsets on the recording's time axis and are not clipped to the samples stored
  (`Recording.samples`): a range after the last anchor is marked `extrapolated`. Without anchors and without a start time, both raise `InvalidInputError`. `Interval.time_offsets_s()` gives float64 seconds since
  sample 0, rounded in the last digits.
- **Newest data.** `latest(c, n=1, metadata_filter)` returns, for every matching recording, its last `n`
  samples (up to 32,768) with their statistics, or with `n = 0` the statistics of its newest stored chunk, and
  the end time (SQL 0.3.6). It costs a fixed number of index lookups per recording, however long the recording
  is. Newest samples that are not readable (compacted, or a gap) come back as `samples = None` with a `note`.
- **New data as it commits.** After `enable_notifications(c)`, `notifications(c, timeout=None, stop_after=None)`
  yields one `Notification` per committed transaction that stored new chunks, with each recording's new range
  (`NotifiedRange`: samples and seconds) (SQL 0.3.6). It needs an autocommit connection used only for listening,
  because notifications reach a session only between transactions. Missed commits are not replayed; catch up
  with `latest`. With `detail=False`, or under row security, a notification carries only the recording count.
- **Value grid.** `sample_quantum` optionally declares a value grid such as an ADC step; it changes only how
  compactly samples are stored. Servers with automatic grid detection find it themselves.

## Search

`search(collection, recording_id, start, end=None, ...)` calls `morlet_search_similar` with the stored
interval `[start, end)` as the query.

- **Time filters (SQL 0.3.3).** `time_from` and `time_to` (timezone-aware datetimes or integer UTC
  nanoseconds) keep matches whose whole window lies in the range; `before_only=True` keeps matches that end
  before the query interval starts, i.e. only history. They use the recordings' start times (`ingest`'s
  `start_time`) and leave out recordings without one.
- **Evidence (SQL 0.3.3).** `evidence=True` fills `match.evidence`: UTC start and end, the statistics of the
  matched interval (as `stats`) and an overview in `evidence_parts` equal parts (min, max, RMS each).

- **Windows.** Search compares fixed-length windows of stored *envelopes*, a compact derived representation
  built per recording (`ingest` builds them when the collection is searchable; `build_search_index` builds
  them later). The query must have the window length (default 4,096 samples; `end` defaults to
  `start + 4096`). Envelopes are used only to find candidates; every result is read back from the original
  samples.
- **Where windows start.** Stored windows start every `search_stride` samples (default half a window). The
  search tolerates time shifts of up to `max_lag_fraction` of the window (default 0.075, about ±300 samples at
  4,096). An event that starts farther than that from every window start can be missed: in the example, the
  default stride (2,048) found one of the four impacts, a stride of 512 found all four. A smaller stride
  costs envelope storage (4× for 512 instead of 2,048), not exactness.
- **Eligibility.** Candidates are windows of recordings with the same sample rate, channel and unit whose
  metadata contains `metadata_filter`; with `exclude_source` (default) windows overlapping the query are
  skipped; with `suppress_overlaps` (default) overlapping results are thinned in rank order.
- **Distance.** `d = 1 − max over allowed shifts of the correlation of the two z-scored envelopes`: about 0
  for the same shape at some shift, up to about 2; smaller is more similar. It is not a probability or a
  confidence, and it is comparable only within one window size. Results are ordered by
  `(distance, recording_id, start_sample)`.
- **Mode.** `SearchResult.mode` is `"exact"` when every eligible window was scored, `"probes"` when the
  collection has probe search enabled for this window size (`morlet_enable_envelope_probes`): candidates then
  come from an approximate index (small galleries are still searched exhaustively), while the distances are
  computed exactly. See the [search contract](search-contract.md).

## Connections and transactions

- `Client.connect(conninfo)` opens and **owns** a connection; as a context manager it commits on a clean exit,
  rolls back on an exception and closes it.
- `Client(connection)` uses **your** psycopg connection: the SDK never commits, rolls back or closes it.
- Every method runs in the connection's current transaction. `ingest` of identical samples again is a no-op;
  different samples at stored offsets raise `ConflictError` (the transaction is then aborted: roll back).
  Ingestion sends its statements pipelined; an error surfaces at the end of the call.
- `notifications` is the one method that needs `autocommit=True`: it raises `InvalidInputError` otherwise.
- No pooling, retries, caching or async API.

## Errors

`MorletError` is the base class: `InvalidInputError` (also a `ValueError`) for inputs the database cannot store
or answer, `NotFoundError` for missing or invisible collections, recordings and intervals, `ConflictError`
for changed replays, `UnsupportedError` for features the server's version lacks, `DatabaseError` otherwise.
The psycopg exception is kept as `__cause__`.

## Limitations

- One complete waveform per `ingest`, in one transaction; for long or streaming acquisitions, call `append`
  per block and commit as you go.
- Searches by stored interval only (not by an arbitrary array), one window size per search, single channel.
- Row-level security and grants apply as for any SQL client; the SDK passes no credentials anywhere else.
- Relation to `tools/client/morlet_client.py`: that script-style client remains for the existing examples
  and notebook; new code should use this package.
