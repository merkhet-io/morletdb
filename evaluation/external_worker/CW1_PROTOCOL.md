# CW1: moving representation work off the database server (external compute workers)

Status: **FROZEN** in the commit that adds this file, before any CW1 measurement.
Development data only (R2's recordings, consumed).

## Why

The long-term direction is RisingWave-style separation of heavy reorganisation work from
the ingest and query path ("compaction nodes"). In MorletDB, PostgreSQL owns the pages, so a
compaction node is a **stateless compute worker**: it reads its input through SQL, computes
outside the database, and writes results back in key-idempotent transactions. The ADR
(`docs/adr-envelope-maintenance.md`) keeps maintenance in PostgreSQL until extraction cannot
keep up; CW1 measures what moving it out costs and buys, so that decision can rest on numbers.

**Scope:** envelope computation only — the CPU-heavy part (z-score, |Hilbert| by FFT,
smoothing, decimation, float16 encoding; about 1.2 ms per window in BL1). Out of scope here,
each for a later experiment of its own: raw-chunk repacking, HNSW work (it lives in
PostgreSQL pages and cannot leave the server), mixed fast/compact representations,
Kubernetes, object storage.

## What is unchanged

The append contract, raw chunks, the envelope table and its key
`(recording_id, window_size, start_sample)`, the representation `xc-envelope-f16-v1`, and the
probe table maintained from envelopes. An external worker must produce **byte-identical
envelope rows** to `morlet_build_envelopes`; anything else is a failure, not a variant.

## Arms

- **IN (in-database):** N concurrent sessions, each calling `morlet_build_envelopes(rid,
  4096, 2048, 64)` in a loop for its share of recordings (today's background worker).
- **EXT (external):** N worker processes. Each claims recordings, reads the encoded raw
  chunks it needs (`SELECT start_sample, sample_count, encoded ...`), decodes and computes
  envelopes in a Rust program built on `morlet-core` (`rawcodec::decode`,
  `envelope::encode`; the same code the extension calls), and inserts batches of 64 envelope
  rows per transaction with `ON CONFLICT DO NOTHING`. Progress = the stored envelope rows
  (resume after the last stored window), exactly as IN.

N ∈ {1, 2, 4, 8}. Both arms start from the same state: raw chunks loaded, envelope table
empty (no probe table, so that index work does not mask the difference).

## Workload

R2's recordings for days 1–2, four stations (8 recordings, 69.1 M samples, 33,736 windows of
4096 at stride 2048), already loaded raw. Each arm/N combination runs on a fresh copy of the
envelope table (truncated), warm caches, one machine (database server and workers share the
same 18-core host; this is stated as a limitation: CPU moved out of PostgreSQL is still on the
same machine, so the measure is *database-server CPU*, not machine CPU).

## Measures

1. **Identity:** every envelope row written by EXT equals IN's row byte for byte (all 33,736).
2. **Throughput:** windows per second, wall clock, per arm and N.
3. **Database CPU:** CPU seconds consumed by PostgreSQL backend processes during the run
   (sum of process CPU time of `postgres` processes, before vs after), per window.
4. **Worker CPU:** CPU seconds of the worker processes, per window.
5. **I/O:** bytes read from and written to PostgreSQL by EXT (encoded raw + envelope rows);
   WAL bytes per arm (`pg_current_wal_lsn` difference).
6. **Robustness (EXT, N = 2):** SIGKILL one worker mid-run and restart it; run two workers on
   the same recording deliberately. Afterwards every window has exactly one row, all rows
   byte-identical to IN.

## Decision (pre-registered)

**Worth pursuing** (an external compute path is a viable basis for compaction-node-style
workers) if all hold:
- identity is exact (measure 1) and robustness checks pass;
- EXT moves at least **70%** of per-window CPU out of the database server (database CPU
  per window ≤ 0.3 × IN's at the same N);
- EXT throughput at N = 4 is at least IN's at N = 4 (offloading does not cost speed).

**Narrow** if identity and robustness hold but only one of the other two does; **stop** if
identity or robustness fails, or neither of the other two holds.
