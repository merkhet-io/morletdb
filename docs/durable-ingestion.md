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

Encoding per chunk:

- **grid**: every sample is within a thousandth of a step of a grid point
  `k * quantum` (or `k / d` when the quantum is `1/d` for an integer `d`, so
  parsed decimals such as 0.123 match exactly). The integers `k` are stored with a
  fixed polynomial predictor and partitioned Rice coding (the scheme of FLAC's
  fixed subframes). The step is refined per chunk, so floats produced as
  `count * calibrated_step` need no corrections; remaining differences (rounding
  noise, signed zeros) are stored as last-place corrections.
- **float**: other chunks code the float64 bit patterns, mapped to
  order-preserving integers, with the same coder.
- **raw**: plain float64 when neither is smaller.

Every kind decodes bitwise to its input; reads, replays and searches return
exactly what was appended. The quantum is a storage hint, not a conversion: a
wrong quantum costs space, never accuracy. `morlet_stream_storage(collection,
recording_id)` reports chunk counts per kind, stored bytes and bits per sample.

Collections created before this change keep `float8[]` chunks and work unchanged;
they reject a `sample_quantum` other than 1. There is no in-place migration: copy
recordings into a new collection to re-encode them. Measured on held-out seismic and bearing data,
chunk tables are 1.7-2.9x smaller than `float8[]` at 4096-sample chunks and
1.9-3.3x at 32768 (CX seismic counts: 5.1 vs 16.9 bits/sample); see the
[engineering check](../evaluation/compression/extension/README.md). Prefer large
chunks: per-row and TOAST overhead adds 1-2 bits/sample at 4096.

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
