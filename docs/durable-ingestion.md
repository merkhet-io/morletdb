# Durable chunk ingestion (Postgres prototype)

This path stores continuous recordings independently of the earlier bounded-row
collections. Each recording has fixed acquisition metadata and an extraction
specification. Chunks and emitted typed feature rows use global bigint offsets.

```sql
SELECT morlet_create_stream_collection('public','signals');
SELECT morlet_start_recording('signals','capture-001',4096,'vibration','g',
  '{"asset":"A"}',512,256,'zscore');
SELECT morlet_append_chunk('signals','capture-001',0,
  ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,136) i));
-- This call can run from a new connection/process after the first commit.
SELECT morlet_append_chunk('signals','capture-001',137,
  ARRAY(SELECT sin(i::float8/8) FROM generate_series(137,2047) i));
SELECT * FROM morlet_stream_status('signals','capture-001');
SELECT * FROM morlet_search_streams('signals',
  ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,511) i),
  4096,'vibration','g','{"asset":"A"}');
```

The constructor creates three caller-owned tables: `signals` for recording
metadata/checkpoints, `signals_chunks` for raw input, and `signals_segments` for
typed feature vectors. Collection names allow 1..46 bytes so sibling names fit
Postgres identifier limits. Do not rename or detach sibling tables. Start creates
a recording once; reusing its ID raises a uniqueness error.

## Raw chunk storage

New collections store each raw chunk losslessly encoded in a `bytea` column
(`sample_count`, `encoded`) instead of `float8[]`. A recording declares its sample
grid with the trailing `sample_quantum` argument of `morlet_start_recording` /
`morlet_ensure_recording` (default 1, i.e. integer ADC counts):

```sql
SELECT morlet_start_recording('signals','imsB1',20000,'vibration','g',
  '{"asset":"B1"}',512,256,'zscore','morlet-energy-v1',0.001);
```

Each chunk is encoded as one of three kinds:

- **grid**: every sample is within a thousandth of a step of `k * quantum`
  (or of `k / d` when the quantum is `1/d` for an integer `d`, so parsed
  decimals such as 0.123 match exactly). The integers are coded with fixed
  polynomial prediction and Rice coding, with the step refined per chunk.
  Remaining differences (rounding noise, signed zeros) are stored as last-place
  corrections.
- **float**: the float64 bit patterns, mapped to order-preserving integers and
  coded the same way.
- **raw**: plain float64, when neither of the others is smaller.

The [format specification](chunk-format.md) gives the layout, the established
techniques used, and why MorletDB owns the format.

Every chunk carries a CRC-32C checksum. Reads, replay comparisons and the table's
CHECK verify it before returning or accepting samples. Every kind decodes
bitwise to its input, so reads, replays and searches return exactly what was
appended.

The quantum is a storage hint, not a conversion: a wrong quantum costs space,
never accuracy.

### Storage accounting

Three different sizes are reported. Do not compare them with each other.

- `morlet_recording_storage(collection, recording_id)` reports per-recording
  value sizes:
  - chunk counts per kind;
  - `original_float64_bytes` (8 bytes per sample);
  - `encoded_bytes`, the logical encoded payload;
  - `stored_value_bytes`, the datum sizes after any TOAST compression.

  None of these include tuple, page, TOAST-chunk or index overhead. That overhead
  is shared, so it is not divided among recordings.
- `morlet_collection_storage(collection)` reports physical sizes,
  `pg_total_relation_size` (heap, TOAST and indexes):
  - for the recordings, chunk and segment tables separately;
  - and for the whole collection, with bits per received sample for the chunk
    table and for everything.

The benchmark headline below uses the chunk-table physical size.

### Measured footprint

These were measured on held-out stage-1 seismic and bearing data; see the
[engineering check](../evaluation/compression/extension/README.md). Chunk tables
are physically:
- 1.7–2.9× smaller than the previous `float8[]` storage at 4096-sample chunks;
- 1.9–3.3× smaller at 32768-sample chunks. For example, CX seismic counts take
  5.1 instead of 16.9 bits/sample.

These ratios compare with MorletDB's previous storage only. They are not
comparisons with optimized Parquet, and not compression ratios against the
original 64-bit samples. For chunk-size trade-offs, including read latency, see
[Choosing a chunk size](#choosing-a-chunk-size).

### Upgrading

pg_morlet 0.2.0 ships an update script from the 0.1.0 SQL objects of release
0.1.0-alpha.3. To use it, install the new extension files, restart PostgreSQL,
then run in each database:

```sql
ALTER EXTENSION pg_morlet UPDATE TO '0.2.0';
```

The update changes extension functions only:
- it adds the codec and storage functions;
- it adds a trailing `sample_quantum` argument to `morlet_start_recording` and
  `morlet_ensure_recording`;
- it redefines `morlet_stream_relations` (new return columns) and three function
  bodies.

It does not modify existing collections or data. Collections created before the
update keep their `float8[]` chunks and remain fully supported (reads, appends,
replays, search). They reject a `sample_quantum` other than 1. Until the update
runs, the old SQL keeps working with the new library.

`extensions/pg_morlet/tests/upgrade.sh` checks all of this in CI against a
populated alpha.3 database. It confirms that:
- results are identical before and after the update;
- legacy appends continue;
- the upgraded catalog equals a fresh 0.2.0 install;
- the guard rejects older objects.

Databases created with **0.1.0-alpha.1** report the same version, 0.1.0, but
have different objects; the update script refuses them. For those databases:

1. Dump the data: `pg_dump --data-only` of the collection tables, or export the
   samples through the application.
2. Drop the extension, then the old collections.
3. Install 0.2.0 into the database.
4. Recreate the collections and re-append the samples.

Keep the original data until the new collection has been verified.

**Re-encoding existing recordings is a separate data transfer, not part of the
extension update.** After the update, an old collection stays `float8[]`. To
store its recordings encoded, create a new collection and append the chunks in
order; features are recomputed and match:

```sql
SELECT morlet_create_stream_collection('public','signals_v2');
DO $$
DECLARE r record; c record;
BEGIN
  FOR r IN SELECT * FROM signals ORDER BY recording_id LOOP
    PERFORM morlet_start_recording('signals_v2',r.recording_id,r.sample_rate_hz,r.channel,
      r.unit,r.metadata,(r.checkpoint->>'window')::integer,(r.checkpoint->>'stride')::integer,
      r.checkpoint->>'normalization',r.checkpoint->>'extractor_version',1 /* sample_quantum */);
    FOR c IN SELECT start_sample,samples FROM signals_chunks
             WHERE recording_id=r.recording_id ORDER BY start_sample LOOP
      PERFORM morlet_append_chunk('signals_v2',r.recording_id,c.start_sample,c.samples);
    END LOOP;
  END LOOP;
END $$;
```

The upgrade test runs this procedure and compares the result bitwise. For large
collections, run it per recording, in separate transactions. Drop the old
collection only after verifying the copy.

### Choosing a chunk size

Chunk size trades storage against short-read latency. The figures below are
from the [read benchmark](../evaluation/compression/extension/README.md#read-and-ingest-performance):
one 16.3 M-sample recording of development-period bearing counts, on an Apple M5
Max, PostgreSQL 17.11 with default settings, warm cache.

| | `float8[]` 4096 | encoded 4096 | encoded 32768 |
|---|---|---|---|
| chunk table, bits/sample | 19.4 | 10.3 | 8.6 |
| 512-sample random read p50 / p95 | 3.4 / 4.8 ms | **1.8 / 2.1 ms** | 3.4 / 3.6 ms |
| 512-sample read across a chunk boundary, p50 | 3.9 ms | 2.1 ms | 5.2 ms |
| samples decoded per sample returned (512 read) | 9 | 9 | 65 |
| sequential read, server side | 1.7 M/s | 2.3 M/s | 2.4 M/s |
| ingest including features | 0.29 M/s | 0.41 M/s | 0.50 M/s |

- **Keep the 4096 default for interactive access to short windows.** Large chunks
  save about 17% of chunk storage but nearly double short-read latency, because
  every read decodes whole chunks.
- **Use 32768 for archive-style data** that is mostly ingested and read in long
  spans.
- Sequential reads are limited to about 2.3 M samples/s in every layout by
  `morlet_read_interval`'s SQL assembly of the result array, not by decoding.
- Cold-cache latency was not measured: a PostgreSQL restart left the OS page
  cache warm, and results matched the warm figures.

## Append, replay and recovery

Append locks the recording row, restores its versioned checkpoint, and validates
the next chunk. In one transaction it inserts raw samples, inserts every newly
complete window's features, and advances the checkpoint. Errors or caller rollback
undo all three changes. Autocommit commits each append; explicit transactions can
batch calls. A new connection reconstructs the extractor from the committed tail
and offsets, with no prior worker memory. Durability follows the database's normal
WAL/commit settings; no separate distributed commit protocol is involved.

An identical retry at an already stored start offset is a no-op and returns the
current received sample count. Comparison is exact numeric array equality, not a
file checksum. Different contents or length at that offset fail. Gaps and overlaps
at other offsets fail. Concurrent appenders of one recording serialize; an
out-of-order chunk may fail and need retry after its predecessor commits.
READ COMMITTED is the initial supported concurrency contract; higher-isolation
serialization errors must be retried by clients.

Chunks contain 1..32768 finite samples within +/-1e9. Each append may emit at most
256 windows. Stream length is not bounded to one chunk; offsets must fit bigint.
Windows span chunk boundaries, use their own normalization and retain an incomplete
tail for the next append. No tail padding or gap interpolation occurs. Rate,
channel, unit and extraction specification are fixed for a recording; use a new
recording identity after acquisition changes.

Checkpoints include format version, extractor version, rate, window, stride,
normalization, received end, next window start and the bounded tail. Restore rejects
unknown versions, invalid specifications, inconsistent offsets and invalid tails.
JSON decoding uses round-trip float parsing to preserve sample values across
checkpoint reloads. An extractor-version upgrade requires an explicit rebuild
policy; no automatic upgrade/migration is provided for this unreleased prototype.

## Raw-only recordings

`extractor => 'none'` stores raw samples without feature extraction:
- the checkpoint tracks the sample offset only and keeps no tail buffer;
- no segment rows are written, and status reports `indexed_segments` 0 and
  extractor `none`;
- feature search skips these recordings; reads, replay checks and storage
  accounting work as usual;
- the sample rate can be any finite value in (0, 200000] Hz.

Use it when recordings are searched by other means, for example materialized
envelopes. In the frozen seismic timing test, envelope cross-correlation beat
temporal Morlet features (see the README's measured results). The default stays `morlet-energy-v1`, and a
recording's extractor cannot be changed after it starts.

## Materialized envelopes

An optional representation stored next to the raw chunks for search-by-example
with envelope cross-correlation. It is materialized, not an index: rows are
derived from raw samples, can be dropped and rebuilt at any time, and raw
storage never depends on them.

```sql
SELECT morlet_enable_envelopes('signals');                  -- creates signals_envelopes
SELECT morlet_build_envelopes('signals', 'pump-7', 4096, 2048);  -- resumable; bounded per call
SELECT * FROM morlet_search_envelopes('signals', :query_samples, 25600, 'x', 'g',
  '{"line": "A"}', 10);                                      -- top 10, overlaps suppressed
SELECT * FROM morlet_search_envelope_interval('signals', 'pump-7', 81920, 86016);
```

- **Representation** `xc-envelope-f16-v1`:
  - z-score, Hilbert magnitude, a 50-sample moving average, every 10th value,
    z-score again;
  - stored as IEEE binary16 behind a one-byte version header;
  - windows are powers of two from 512 to 32768 samples. A 4096-sample window
    stores 821 bytes.
- **Matching:** 1 − the maximum biased cross-correlation over lags up to
  `max_lag_fraction` of the envelope length. The default is 0.075, which is ±30
  envelope values at 4096 samples.
  - The query envelope is computed at full precision; stored ones are decoded and
    z-scored again.
  - Scoring runs in Rust inside the database.
- **Eligibility:** the same rate, channel, unit and window size, plus metadata
  containment. Above `max_candidates` (default 4096) the search fails rather than
  sampling. Overlapping windows of one recording are suppressed in rank order.
- **Building:** `morlet_build_envelopes` covers complete windows on the grid
  `k × stride`, resuming after the last stored window of that size. At most
  `max_windows` windows are built per call. Appends do not build envelopes
  automatically; call it after appending, for example from the ingest job.
- **Storage:** `morlet_collection_storage` reports `envelope_table_bytes`, which
  is included in `total_bytes`.

### Evidence

Frozen evaluations on seismic event windows (CX, 4096-sample windows):
- **R1** (`evaluation/materialized`): stored envelopes return the same top 10 as
  decoding raw.
- **R1b** (`evaluation/materialized/compact`, 2022 held out): the binary16 form
  had recall@10 0.9999 against full raw decode, 0.012× the p95 latency, and 12.9%
  of the raw chunk table.
- **Implementation check** (`compact/sql_check.py`, the same 2022 windows): the
  SQL API returned the identical top 10, in identical order, for all 1,557
  queries. The largest distance difference was 3 × 10⁻¹⁵. p95 search latency was
  13 ms, and the envelope table was 13.1% of the chunk table.

**Not established:** relevance on machine data, behaviour beyond the tested
gallery sizes (about 180 candidates per query), and concurrent workloads.

### Probe search (opt-in, needs pgvector)

The exact envelope search scans every eligible window: about 12 µs per window, so
2.8 s for 235,000 windows on the test machine. Probe search makes large galleries
interactive. It is approximate.

```sql
CREATE EXTENSION vector;                                   -- pgvector, 0.7 or later
SELECT morlet_enable_envelope_probes('signals', 4096);     -- creates signals_probes_4096
SELECT * FROM morlet_search_envelope_probes('signals', 'pump-7', 81920, 86016);
```

- **Why it works:** each lag term of the envelope distance is the inner product of
  the stored envelope with the query envelope shifted by that lag and zero-padded
  (`morlet_envelope_probe`). The search issues probes at lags −L, −L+5, …, L, where
  L is ±30 at 4096 samples, so 13 probes. It takes the 50 best windows per probe from
  an inner-product HNSW index on a float16 copy of the envelopes. It then re-ranks
  their union with the exact scorer, applying the same eligibility, source exclusion,
  ordering and overlap suppression as `morlet_search_envelope_interval`. Returned
  distances are therefore exact; only the candidate set is approximate.
- **Arguments:** the same as `morlet_search_envelope_interval`, plus `per_probe`
  (default 50) and `probe_step` (default 5). `max_candidates` does not apply.
- **Maintenance:** `morlet_enable_envelope_probes` fills the table from the
  envelopes already stored and builds the index. After that,
  `morlet_build_envelopes` adds each new window's row in the same transaction.
  Calling it in the append transaction makes a window searchable when it commits.
  Rows follow their envelopes: deleting an envelope or a recording deletes them.
  `DROP TABLE signals_probes_4096` disables probe search.
- **Storage:** the copy and its index take about 1.0–1.4× the raw chunk table at
  4096-sample windows with stride 2048. That is in addition to the envelope table.
- **Filters:** eligibility is applied after probing. A selective metadata filter
  therefore discards candidates and lowers recall. For small filtered galleries the
  exact search is fast and should be used.
- **pgvector is optional.** pg_morlet does not depend on it. The probe table and its
  index are created at enable time, and only these two functions use it.

**Evidence** (`evaluation/bounded_lag`, PR #62). One machine, warm, one client,
unfiltered galleries of about 235,000 windows; fidelity against the exact search,
not relevance. Development and confirmation used the experiment harness, which
issues the same probes and re-rank as these functions; the product check ran the
functions themselves.
- **Development** (CX 2025):
  - recall@10 0.998;
  - p95 115 ms against 2,835 ms for the exact search.
- **Independent confirmation** (CX stations and a year no earlier study had read,
  March 2021):
  - recall@10 0.976;
  - p95 131 ms against 2,854 ms for the exact search and 713 ms for an exact parallel
    SQL scan;
  - with the same number of candidates, a zero-lag shortlist reached 0.613.
- **These functions, on the same held-out data:**
  - recall@10 0.977 (random queries 0.998, high-energy 0.956);
  - p50 37 ms, p95 50 ms;
  - enabling on 234,622 existing windows took 5.1 minutes, including the backfill and
    the index build.
- **During replayed ingestion:** with synchronous maintenance, every window was
  searchable at commit and appends ran at 919,000 samples/s with 4 writers. Recall
  against the exact search on the same snapshot was 0.9997.

## Progress and search

Status exposes received samples, next window start, visible segment count and
extractor version. All complete windows made ready by a successful append are
published with that append. The next-window offset is **not** an asynchronous
completeness watermark: a partial tail waits for more samples and stride can skip
sample positions. There is no background extraction worker or durable job queue.

`morlet_search_streams` does an exact scan of at most 4096 matching segments, using
rate/channel/unit equality, JSONB metadata containment, and matching window,
normalization and extractor version. Results have global zero-based half-open
intervals, ordered by feature distance, recording ID and offset. Query size is
512..4096, top_k is 1..100. This first streaming query API includes overlapping and
source matches; policies from the bounded collection API are not yet ported here.

## Ownership and supported writes

All functions run as SECURITY INVOKER. Applications need the relevant table
privileges: SELECT/INSERT for start; SELECT/UPDATE on recordings and SELECT/INSERT
on sibling tables for append; SELECT on recordings/segments for search and status.
RLS applies to each table, and search joins parent visibility to segment visibility.
A denied checkpoint update aborts the append, rather than committing orphaned
progress. Policies and grants remain the collection owner's responsibility.

Use the append API as the supported write path. Direct mutation of checkpoints,
chunks or feature rows can break cross-table consistency; the owner controls all
three tables and this prototype does not defend against such writes. Deleting the
parent recording cascades to its chunks and segments. Tables/data are dumped as
normal application objects; the extension supplies their functions.

## Validation and next steps

Core tests validate checkpoint round trips and malformed state. Native SQL tests
check boundary features against independent extraction, rollback, idempotent and
conflicting replay, deletion and RLS-denied updates. The benchmark smoke adds a
committed-checkpoint resume through a fresh connection, an injected rollback, and
two racing identical retries that must leave one chunk and one segment.

The [Parquet importer](../tools/ingest/README.md) now supplies stable recording
identities, canonical chunk coordinates and transaction-size controls. Next:
larger benchmark datasets, batch tuning and asynchronous extraction. Current extraction is synchronous and the search budget still
limits scale; this milestone supplies durable state, not an HTAP scale claim.

## Batched appends

`morlet_append_chunks(collection, recording_id, inputs morlet_input_chunk[])`
accepts a typed one-dimensional array, with each composite holding a bigint start
coordinate and float8[] samples. It returns the final received position. For example:

```sql
SELECT morlet_append_chunks('signals','capture-001',ARRAY[
  ROW(2048,array_fill(0.::float8,ARRAY[1024]))::morlet_input_chunk,
  ROW(3072,array_fill(0.::float8,ARRAY[1024]))::morlet_input_chunk
]);
```

A batch holds 1..64 chunks and at most 262144 total samples; per-chunk constraints
still apply. One recording per call keeps lock ordering explicit. Chunks execute
in supplied order using the same durable append checks. A later failure rolls back
the **whole SQL statement**, including earlier chunks in the batch. Identical
retries remain no-ops. Transactions can contain several batch calls, and normal
caller privileges/RLS remain in effect. NULL arguments follow STRICT semantics.

Batching amortizes client/server round trips. This first implementation still
advances the checkpoint and inserts features for each chunk inside the call; it
does not coalesce checkpoint writes or decouple extraction from ingestion.
