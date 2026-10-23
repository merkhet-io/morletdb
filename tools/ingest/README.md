# Parquet ingestion into MorletDB

The importer reads local Parquet in Arrow batches, validates signal coordinates,
and sends canonical chunks through the durable Postgres API. Extraction remains
synchronous. Both supported layouts produce the same samples and features.

## Run

Install the current extension, create a stream collection once, then import:

```sql
SELECT morlet_create_stream_collection('public','signals');
```

```sh
python -m pip install -r tools/ingest/requirements.txt
export DATABASE_URL='postgresql://localhost/morlet_db'
python tools/ingest/parquet_import.py signals.parquet --collection public.signals
# Validate input without a database connection:
python tools/ingest/parquet_import.py signals.parquet --collection public.signals --validate-only
# Multiple immutable files, explicitly ordered:
python tools/ingest/parquet_import.py part-1.parquet part-2.parquet --collection public.signals
```

Normal libpq environment variables also work. Do not put passwords in command
arguments. The JSON report excludes the DSN. It records source filenames, SHA-256
fingerprints, byte sizes, mappings, extraction/chunk settings, elapsed time and
submission counts. There is no automatic schema/extension creation or grant.

## Input contracts

Both layouts require `recording_id` (nonempty string), `sample_rate_hz` (numeric),
`channel` and `unit` (nonempty strings). Optional `metadata` is a JSON-object string
or Arrow struct converted to a Python dictionary; absent metadata is `{}`.

| Layout | Position column | Values column |
|---|---|---|
| Default `--layout chunks` | `start_sample`, integer global offset | `samples`, list of 1..32768 numbers |
| `--layout samples` | `sample_index`, integer global offset | `value`, one number per row |

Samples must be finite and within +/-1e9. Rates must be finite in 128..200000 Hz.
Offsets are nonnegative bigint coordinates, not timestamps. Gaps, overlaps, NULLs,
metadata/acquisition changes and out-of-order rows fail validation. Rows must be
grouped in ascending recording-ID string order within each file and have contiguous
sample positions for each recording. The first position of a file may be nonzero
when continuing an existing recording; Postgres checks it against durable progress.

An explicit column-map JSON maps canonical names to source names:

```json
{"recording_id":"capture_id","start_sample":"position","value":"amplitude"}
```

Use it with `--layout samples --columns mapping.json`. Unknown keys, missing required
columns, duplicate source mappings, or explicitly mapped missing metadata fail.
No implicit timestamp-to-sample conversion, resampling, channel mixing, sorting,
remote/S3 credentials or format guessing is performed in this first importer.

## Batching, transactions and replay

`--chunk-size` defaults to 4096; chunks are packed across Parquet row and Arrow
batch boundaries within a file. A recording's final partial chunk is sent as-is.
This permits windows to span input rows, batches and persisted chunks. Chunk size
must be 1..min(32768,256*stride), matching the extractor's per-append budget.
`--batch-rows` defaults to 64 (1..1024); only selected columns are read. A single
input array may contain at most 32768 values. Memory is bounded by decoded batches
and one recording buffer at the Python layer; Parquet row-group decoding can have
additional format-dependent memory overhead. This is not a hard RSS limit.

`--commit-chunks` defaults to 16 (1..256). Each transaction commits all raw chunks,
features and checkpoints together. Earlier batches remain committed when later
input fails. Failures report acknowledged committed submissions; the pending batch
is rolled back. A connection loss during commit can leave the outcome uncertain;
rerun unchanged input to resolve it safely.

Recording creation is idempotent through `morlet_ensure_recording`: existing rate,
channel, unit, metadata, window, stride, normalization and extractor version must
match. Append verifies raw contents at every previously stored chunk offset. An
identical retry is a no-op; conflicting contents or chunk boundaries fail. Counters
include replay submissions and are **not counts of newly inserted database rows**.

Resume means rerunning the same immutable files in the same order with the same
chunk size and column/extraction settings. It rereads and verifies the prefix; it
does not seek directly to a persisted Parquet row-group cursor. Read batch size and
transaction size can change without changing chunk coordinates. Files must remain
immutable during a run; size/mtime/identity checks catch common source mutations,
but are not a filesystem snapshot guarantee. SHA-256 is provenance, not a database
manifest or an authorization mechanism.

Do not concurrently import different chunkings into the same recording identity.
Postgres serializes appenders, but a later offset arriving first can fail until its
predecessor commits. Use a new recording identity for calibration/specification
changes. Permissions/RLS follow the [durable ingestion contract](../../docs/durable-ingestion.md).
The importer needs recording creation plus append privileges.

## Validation

Unit tests verify mapping, layout/batch-independent chunk boundaries, malformed
input and validation without a database. CI runs an optimized integration smoke:
a late input gap leaves a committed prefix; corrected rerun completes it; repeated
import does not duplicate data; sample-row and array-row layouts match direct
extraction; a changed extraction specification is rejected.

The [continuous-recording sweep](../../benchmarks/README.md#continuous-recording-sweep)
now measures server-side batches. Next: object-storage sources, tuning at larger
scales and an asynchronous extraction contract.

## Database call batching

`--rpc-chunks` defaults to 1 (1..64), preserving the single-chunk baseline.
Enable batching explicitly, for example `--rpc-chunks 16`. In batched mode the importer registers the server's
`morlet_input_chunk` composite type and sends a typed array per append call. Calls
are flushed at recording/file boundaries, the RPC limit, or the transaction limit.
`--commit-chunks` still controls transaction size independently. The default uses the original single-chunk API. The initial local sweep reduced
RPC counts through batching but did not improve throughput, so a new default is
not justified without representative network/workload measurements.

The configured RPC size times chunk size may not exceed 262144 samples; the server
also validates actual payload size. Default chunks are 4096 samples. A batch error
rolls back the pending transaction, while earlier committed batches remain safe to
replay. Reports add append `rpc_calls` and the server extractor version. `elapsed_s`
excludes preflight hashing; `preflight_s` and `wall_elapsed_s` expose that overhead.
Batched calls reduce round trips; throughput improvement must be measured, since
composite adaptation and feature extraction also consume CPU.
