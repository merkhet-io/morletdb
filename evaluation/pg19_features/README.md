# PostgreSQL 19: what it changes for MorletDB

**Development review, 2026-11-19, PostgreSQL 19.0 (released that day).** It reads the 19.0 release notes against
pg_morlet's code and operations, and runs one development check (`repack_check.sh`). It is not a frozen protocol and
not a benchmark. No speed claim is made, and nothing here is a measured result.

## Compatibility

- pg_morlet 0.4.0-beta.1 (SQL 0.3.12) builds with pgrx 0.19.3 against a 19.0 source build (macOS arm64, lz4) and
  passes all 189 `#[pg_test]`s and the on-demand loading checks (`tests/ci_local.sh 19 without-pgvector`). CI runs
  the same on Linux, with and without pgvector 0.8.7.
- TOAST compression: 19 defaults to lz4, and pg_morlet's sample and envelope columns stay pinned to pglz (#280).
- pgrx 0.19.4 fixes CVE-2026-6460 (composite datums read under an incompatible row type). pg_morlet reads no
  composite datums through pgrx, so it is not affected, and it stays on 0.19.3 (see `docs/compatibility.md`).
- JIT is off by default on 19. pg_morlet already turns it off where it hurt (#340); nothing changes.
- `max_locks_per_transaction` rises from 64 to 128. `postgres -C shared_memory_size` on 19.0 gives 26 MB → 30 MB
  at `shared_buffers = 16MB` and 80 MB → 83 MB at 64MB (PostgreSQL 17.11: 25 MB at 16MB). That is about 4 MB more
  shared memory, which matters only for the smallest containers. The release image stays on PostgreSQL 17.
- Nothing pg_morlet uses was removed or renamed: no RADIUS, `MULE_INTERNAL`, `btree_gist` `inet` opclasses, the
  removed planner hook, wait event names or `sync_error_count`.

## Features worth using, in order of value

1. **`REPACK (CONCURRENTLY)`: disk space back without stopping ingest.** Today, removing recordings or moving
   episodes to memory level 4 frees space only inside the table files: "the files do not shrink, so the disk's free
   space does not grow" (`docs/runbooks.md`, runbook 8). `VACUUM FULL` would give the space back, but it locks the
   collection and needs free space the size of the table. On a full edge disk, that is the one moment it cannot run.
   `repack_check.sh`, on 19.0 with default settings (`wal_level = replica`; 19 enables logical decoding by itself
   when needed, and `max_repack_replication_slots` defaults to 5):
   - 40 recordings × 32 chunks; half the recordings deleted, then `VACUUM`: `rp_chunks` stays at 5.63 MB;
   - `REPACK (CONCURRENTLY) rp_chunks`, while another session appended 160 chunks: 3.06 MB afterwards;
   - every kept chunk is byte for byte the same (md5 of `encoded`), all 800 chunks are there, `morlet_verify`
     reports 0 problems, and every recording reads its last 32,768 samples, including the concurrent appends.

   Limits: a small collection (5.6 MB), so the repack took under a second and overlapped the appends only briefly.
   It was not tried with a full disk, on the Pi, or with probe tables. The rewrite still needs temporary space about
   the size of the kept data. The space comes back sooner after a smaller rewrite, so it suits a regular steward task
   (repack after a memory move) better than an emergency one. **Proposal:** a steward action, with approval like the
   others, that repacks a collection's tables after removals on PostgreSQL 19 (and says "needs 19" elsewhere). Progress
   comes from `pg_stat_progress_repack`. Runbook 8 documents the manual step (step 4).
2. **Selective `NOTIFY` wakeups.** Commit notifications (#205) send one `pg_notify` per appending transaction. Up to
   18, every `NOTIFY` woke most backends; 19 wakes only the listeners of that channel. It comes free with many
   connections (collectors, steward, MCP, dashboards). Not measured.
3. **`WAIT FOR LSN` on standbys: read-your-writes on a replica.** After an append on the primary, a reader on a
   standby can wait until that commit is replayed before reading. That suits the replica runbook and a central read
   replica of edge boxes. It needs `pg_current_wal_lsn()` from the writer; the SDK could offer it later.
4. **Observability for the steward:** `pg_stat_autovacuum_scores` (why a table is or is not vacuumed next),
   `pg_stat_lock`, `pg_stat_recovery`, `log_lock_waits` on by default, and `EXPLAIN (ANALYZE, IO)`. They are candidates
   for the steward's `/metrics` and health checks on 19, and help with the S1 findings (#527, #528).
5. **Visibility map set by table scans.** Collection tables are mostly appended and then read. On 19, a scan can
   mark pages all-visible without waiting for `VACUUM`, which helps index-only scans and later vacuums. Not measured.
6. **Logical replication of sequences (`ALL SEQUENCES`, `REFRESH SEQUENCES`) and `EXCEPT`.** A fleet that copies
   edge collections to a central server by logical replication gets sequence values too. MorletDB's own transfer
   functions do not need them. Worth noting in the replica runbook.

Not useful for MorletDB now: `INSERT ... ON CONFLICT DO SELECT` (appends must stay identical on 17 and 18),
`CREATE AGGREGATE ... SUPPORT` and negative `aggtransspace` (the aggregates keep bounded state), `COPY TO` JSON,
`pg_plan_advice`, parallel autovacuum (small machines), server-side SNI and OAuth changes.

## Reproduce

```sh
# a PostgreSQL 19 build with pg_morlet installed (cargo pgrx install --pg-config <its pg_config> --features pg19)
PG19_BIN=/path/to/19.0/bin bash evaluation/pg19_features/repack_check.sh
```

It creates a throwaway cluster in a temporary directory on port 28839 and removes it at the end.
