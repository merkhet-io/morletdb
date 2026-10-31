# ADR: where search representations are maintained during ingestion

Status: **accepted for now** (2026-10-31). Revisit when a deployment needs any of the
triggers listed under "Revisit".

## Context

Raw samples are appended at global sample offsets; envelopes and, since probe search, a
float16 vector copy with an HNSW index are derived from them. Evaluation BL1 Stage B
(`evaluation/bounded_lag/STAGE_B_RESULTS.md`, PR #62; one machine, development data) compared
two ways of keeping them current while recordings are replayed:

| | Synchronous (in the append transaction) | Background worker (separate transaction) |
|---|---|---|
| Raw append throughput, 4 writers, unpaced | 919k samples/s | 1.73M samples/s (raw only) |
| Window searchable after its completing commit | immediately (same commit) | p95 0.09 s at 200× real time; p95 64 s when appends outpaced one worker (~200 windows/s) |
| Failure handling | nothing to recover: raw and representation commit together | worker killed with SIGKILL and restarted: resumed from the stored envelopes; every window present exactly once |
| Probe recall vs exact on the same snapshot | 0.9997 | 0.9999 |

A third option is an external stream processor. RisingWave is the one examined
(`evaluation/bounded_lag/RELATED_WORK.md`): streaming SQL with incrementally maintained
materialized views, an LSM state store on object storage with epoch-versioned keys and
barrier-driven checkpoints, and HNSW maintained inside the dataflow (append-only inputs
at the time of reading).

## Decision

1. **Maintain representations inside PostgreSQL, synchronously by default:** call
   `morlet_build_envelopes` in the append transaction. It keeps envelopes and probe rows
   exactly as current as the raw data and needs no recovery logic.
2. **Use a background worker only when appends must outrun extraction**, for example a
   burst backfill. The worker is a loop over `morlet_build_envelopes` with a bounded
   `max_windows`; its checkpoint is the envelope table itself. Run several workers by
   recording if one is too slow.
3. **Do not integrate an external stream processor now.** Nothing in the measured
   workloads needs it, and it would duplicate state that PostgreSQL already holds
   transactionally.

## Semantics that any option must keep

- **Sample offsets, not event time, are authoritative.** A window is final when the
  recording's committed raw data covers it; that is a property of the append contract,
  not a watermark. Wall-clock (event) time is metadata and needs a time origin.
- **No late data in the archive.** The append contract rejects gaps, overlaps and changed
  replays, so there is nothing "late" to amend: a late or gapped stretch becomes a new
  recording or waits in a staging layer (the collector spool) until it can be appended in
  order. A live view's lateness policy (how long to wait before showing a window) is a
  separate, display-side choice and must never drop or rewrite archived samples.
- **Three kinds of progress:** raw progress (recording checkpoint), representation
  progress (last stored envelope) and index visibility (probe row). Synchronous mode keeps
  them equal at every commit; worker mode makes the last two trail the first.
- **No "exactly once" claim.** Idempotent replay plus primary keys give at most one row per
  window; completeness is checked by counting against the checkpoint.

## Options not chosen, and why

- **RisingWave integration.** Useful if context (machine state, program, operator events)
  arrives as streams that must be joined continuously with representation events, or if
  many consumers need derived views. Costs: a second system with its own consistency model
  (epochs and barriers, versus PostgreSQL transactions), and event-time windows instead of
  sample offsets, which would need a mapping that preserves gaps. RisingWave speaks the
  PostgreSQL wire protocol, which is **not** extension compatibility; we found no support
  for loading PostgreSQL extensions, so pg_morlet's scorer and codec would not run inside
  it. A realistic shape is RisingWave consuming change events from PostgreSQL (or the
  collector) for context joins, while samples, envelopes and search stay in PostgreSQL.
- **Recent mutable versus immutable historical segments.** Sealing old recordings into
  immutable, compacted segments (possibly on object storage, as LSM stores do) could cut
  index maintenance and storage for archives. Not needed at the measured scale (hundreds
  of thousands of windows); it would be a storage-engine project, out of scope.

## Revisit when

- appends must stay faster than synchronous extraction for long periods (worker backlog
  that does not drain);
- galleries grow well beyond about a million windows per filtered search, or deletes and
  index rebuilds become routine;
- context arrives as independent streams that must be joined continuously.
