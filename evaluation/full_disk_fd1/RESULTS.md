# FD1 results: a full disk loses nothing, and the steward prevents it

**Pass in all 9 runs.** Protocol [PROTOCOL.md](PROTOCOL.md), frozen at `216b135` before any reported run; run once,
2026-11-08 23:18–23:33, `run_fd1.sh`, results `results/*_r[123].json`, log `results/fd1.log`. Image
`morletdb-fd1:local` (release 0.3.9-alpha.1 plus the steward), PostgreSQL on a 600 MB ext4 filesystem with a 64 MB
ballast file, durable batches of 262,144 samples. Other work ran on the Mac at the same time (benchmark preparation);
FD1 judges correctness only, and its times are not statements.

| Arm | First error | Server after it | Acknowledged → stored | Back after space was freed | Problems |
|---|---|---|---|---|---|
| bare (3 runs) | `could not extend file "base/…"`: No space left on device | **answers** (ordinary error) | 183,238,656 → 183,238,656 in each | 1.1 s after restart | none |
| bare-wal (3 runs) | `could not write to file "pg_wal/xlogtemp…"`: No space left on device | **down**: PANIC, container exited | 129,761,280 → 129,761,280 in each | 1.1 s after restart (crash recovery) | none |
| steward (3 runs) | `new samples are refused … the disk reserve is reached` (SQLSTATE 53100) | **answers** throughout, no PANIC | 142.9–143.4 M → all stored | appends resumed 1.1 s after the ballast was removed, without restart | none |

In every run: every stored sample equal to the generator (SHA-256 over the whole recording), the stored end on a batch
boundary and not below the last acknowledged end, `morlet_verify` without findings, and two further appends
succeeded.

## What this answers (#341)

- **A full disk does not lose acknowledged data.** Whether a data file (bare) or the write-ahead log (bare-wal) runs
  out of space first, every sample acknowledged before the failure was stored and exact. PostgreSQL acknowledges a
  commit only after its WAL is flushed; the WAL case ends in a PANIC and a stopped server, and crash recovery after
  freeing space restores exactly the acknowledged state.
- **The two failure modes differ for operations.** A full data file is an ordinary error and the server keeps
  serving reads; a full WAL stops the server until space is freed. Which comes first depends on timing: on ext4 the
  data file usually lost the race in development runs (L1 on tmpfs met the WAL case three times); bare-wal forces it.
- **With the steward the failure does not happen.** At its 100 MB reserve (88.5–89.2 MB free) the steward set its
  ingest hold; for 20 s every append was refused with its reason, the server kept answering, no PANIC was logged; once
  space was freed it released the hold and appends resumed by themselves.

## Limits

One file system (ext4 on a loop device inside Docker's VM), one machine, WAL and data on the same volume, the
steward in the same container with a 1 s tick and a writer paced at about 1 M samples/s; a faster writer or a longer
tick needs a larger reserve. The disk was filled by the database itself; space taken by another process behaves the
same for PostgreSQL but was not tested.
