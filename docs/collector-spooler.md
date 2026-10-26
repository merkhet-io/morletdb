# Collector/spooler: first implementation contract

Status: proposed build specification, not an implemented collector.
Date: 2026-10-27.

## Goal and scope

Separate acquisition durability from PostgreSQL availability. Build a portable Rust
spool library and CLI, then a replay worker using the existing recording APIs.
The first source is a deterministic sample generator or file replay, not a vendor
DAQ driver. Keep channel and acquisition concepts generic, without CNC semantics.

Do not add adaptive waveform discarding, ML, resampling, Parquet conversion,
automatic spool eviction or a new database storage backend in this increment.
Preserve all accepted samples; filtering/retention is a later, explicit policy.

## Architecture

Source adapter -> bounded writer queue -> local durable spool.
Independent replay worker -> existing PostgreSQL recording and append APIs.
Database slowdown must not block acquisition while configured spool capacity remains.
Spooling does not guarantee hard real-time acquisition or replace device buffering.

Choose Rust to share validation/types with the active codebase; do not expand the
frozen Go reference. Prefer existing dependencies and pin new dependencies.

## Identity and data model

Each recording is one uniformly sampled channel. Persist an immutable manifest:
format version, recording ID, acquisition/channel identity, sample rate, unit,
source/clock metadata and optional UTC origin with uncertainty. Persist the
database recording/extractor specification separately and reject conflicting replay
configuration. Default the pilot to raw-only ingestion when no extraction is needed.

Frames contain recording identity, monotone sequence, start sample, sample count,
sample encoding, bounded payload length and checksum. Define canonical byte order,
checksum coverage and numeric encoding. Preserve finite float64 values exactly in
the first version; retain existing finite/range validation. No silent float32 cast.

Use global integer sample offsets; arrival time is not sample time. A source gap
ends a recording and starts another with explicit discontinuity metadata.
Replay must never concatenate samples across a gap or infer missing values.
Clock drift remains metadata/unsupported, not silently corrected.

## Publication and durability

Use bounded append-only spool segments and a single writer per spool directory,
enforced by an OS-backed lock. Readers only consume sealed immutable segments in v1.

Write framed data to an active temporary segment; sync file contents; close and
atomically rename it to ready on the same filesystem; sync the directory.
The producer distinguishes accepted-in-memory, written and durable acknowledgments.
Only an acknowledged sync boundary promises power-failure durability.
Segment duration and sync interval bound visibility delay and potential
unacknowledged loss; expose both settings and counters.

Recovery scans an active segment with bounded allocations. A provably incomplete
trailing frame may be truncated to the last valid boundary. Checksum failure in a
complete frame or corruption in a sealed segment stops replay and quarantines the
segment; never silently skip it. Recovered valid frames can be sealed and replayed.
Unsupported versions fail explicitly.

These guarantees assume a local filesystem honoring sync and atomic rename.
Document the supported OS/filesystem scope; do not claim network-filesystem parity.

## Replay and database commit

Use morlet_ensure_recording and bounded morlet_append_chunks batches (the existing
Python client documents their behavior). Transactions may split a spool segment.
After PostgreSQL commit, persist a replay checkpoint atomically and durably.

Crash after commit but before checkpoint -> replay the same chunks; the server
verifies identical retries. Conflicting samples stop with actionable diagnostics.
Describe delivery as at-least-once with verified idempotent appends, not an
unqualified exactly-once pipeline. A lost database commit acknowledgment is retried.

Do not advance a checkpoint on timeout, connection loss or rollback. Bounded
exponential backoff applies only to transient errors. Permission, schema, identity
and numerical errors need operator intervention. Retain spool data by default;
no automatic deletion in v1. Checkpoints do not replace a backup policy.

## Bounded resources and failure behavior

Configure queue capacity, frame length, segment size, sync interval and maximum
spool bytes. Enforce disk budget before accepting data, including in-flight frames.
At exhaustion, return a typed backpressure/full error. An unpausable source must
report sample loss and start a new recording when acquisition resumes. No overwrites.

Expose received/durable/committed sample counts, queue occupancy, spool bytes,
oldest pending age, sync latency, replay rate, retries and gaps.
Credentials come from environment/config with restricted permissions, never manifests
or logs. Local spool contains potentially sensitive raw measurements.

## Acceptance checks

1. Database unavailable: source continues to spool up to the configured bound;
   recovery replays exact samples and sample offsets.
2. Kill at frame write, sync, rename, database commit and checkpoint publication;
   restart recovers the documented durable prefix without duplication.
3. Lost commit acknowledgment and repeated replay preserve server content.
4. Truncated tail, checksum corruption and oversized length fields exercise distinct,
   explicit recovery/error paths with bounded memory.
5. Full disk and queue overflow produce visible failures and discontinuities.
6. Conflicting source retries and recording configurations are rejected.
7. Concurrent collectors cannot write the same spool; replay reads sealed files only.
8. Read back ingested intervals and compare sample bit patterns.
9. Sustain the reference three-channel 25.6 kHz workload using float64
   (614,400 payload bytes/s) for a declared duration with induced database outages.
   Report hardware, sync policy, bytes, CPU/memory, dropped samples and recovery time.
   This is a test workload, not a scale claim.

Use an independent frame decoder/expected fixtures for format checks and real
PostgreSQL integration for replay/commit checks. Mock tests alone are insufficient.

## Implementation sequence

A. Rust spool format, writer, recovery scanner, generator/file adapter and
   inspect/status CLI; fault-injection tests.
B. Replay worker against existing APIs with real database crash/retry checks.
C. Optional compose demonstration: record offline, start database, replay, verify.

Each PR reports implemented guarantees and remaining limitations. Do not call A
a complete live sensor collector; hardware adapters follow once an actual device,
driver interface, sample format and buffering behavior are known.
