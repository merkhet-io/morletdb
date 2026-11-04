# Python SDK

`sdk/python` is a small, synchronous client for MorletDB: ingest a regularly sampled NumPy waveform with its
timing and metadata, read exact sample intervals, search for similar intervals, and resolve every result to
the stored samples. It wraps pg_morlet's SQL functions and adds no database features of its own.

- Package: `morletdb` 0.1.0a1 (alpha), Python 3.10+, depends on `numpy` and `psycopg` 3 only; plotting
  (`[plot]` extra, matplotlib) is optional and used only by the example.
- Not published yet: `pip install "./sdk/python[plot]"` from a checkout.
- Example: [`examples/python/quickstart.py`](../examples/python/quickstart.py), synthetic data, no download.
- Servers: pg_morlet SQL 0.3.2 (release image `0.3.3-alpha.1`) and later. On SQL 0.3.3 (release image
  `0.3.4-alpha.1`) the SDK sends and
  reads samples as raw float64 bytes, offers `stats` and `overview`, and `create_collection` enables per-chunk
  summaries; on 0.3.2 it uses binary `float8[]` arrays instead
  (same results, more server-side parsing) and `stats` and `overview` raise `UnsupportedError`.

## First run

```sh
docker run -d --name morletdb -e POSTGRES_USER=morlet -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=morlet -p 127.0.0.1:54329:5432 ghcr.io/merkhet-io/morletdb:0.3.4-alpha.1
docker exec morletdb psql -U morlet -d morlet -c "CREATE EXTENSION IF NOT EXISTS pg_morlet"
python -m venv .venv && . .venv/bin/activate && pip install "./sdk/python[plot]"
DATABASE_URL=postgresql://morlet:change-me@127.0.0.1:54329/morlet python examples/python/quickstart.py
```

The example stores two two-minute recordings at 8 kHz with "impacts" and "electrical transients" at known
times, reads 0.5 s of the first impact by time, searches from it, prints the ranked matches with their time
and generated event, and saves `quickstart.png` (the query and its three best matches). On the release image
it runs in a few seconds; the four impacts rank first (distance ≈ 0.01), the electrical transients follow
(≈ 0.16–0.19).

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
  each time (the server's `morlet_time_to_samples`). `Interval.time_offsets_s()` gives float64 seconds since
  sample 0, rounded in the last digits.
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
