# FD1: a full data disk during durable ingestion, without and with the steward (#340)

Status: **FROZEN** in the commit that adds this file, `fd1.py`, `Dockerfile`, `entry.sh` and `run_fd1.sh`, before
any reported run. Development runs (repeat 0, `results/dev/`) built the harness and are not reported; they showed
that a full ext4 disk usually fails a data-file extension first (an ordinary error, the server keeps running), so the
`bare-wal` arm was added to force the WAL path that L1 met on tmpfs (PANIC).

## Question

L1 (#318) filled the disk and PostgreSQL stopped with a PANIC on the write-ahead log; while the disk stayed full it
could not restart, so whether acknowledged samples survived was not known. When the data disk fills up during
durable ingestion: is the failure visible, does PostgreSQL come back once space is freed, is every acknowledged
sample intact, and does the steward's disk reserve prevent the failure altogether?

## Setup

- Image `morletdb-fd1:local` (`Dockerfile`): the release `ghcr.io/merkhet-io/morletdb:0.3.9-alpha.1` plus Python 3,
  psycopg 3.2.10 and `tools/steward` from this commit; image id recorded per run. PostgreSQL's data directory on a
  600 MB ext4 filesystem (a loop-mounted image in a Docker volume, so it survives a restart; `-m 0`, no root
  reserve) holding a 64 MB ballast file. Data checksums, `max_wal_size=1GB`, otherwise defaults; 8 CPUs, 16 GB.
- One recording, 64 kHz, ADC values (L1's generator), appended in durable batches of 262,144 samples (one transaction
  each, 4,096-sample chunks); the writer records each acknowledged end.

## Arms (3 repeats each, `run_fd1.sh`)

- **bare:** write until an append fails. Then stop the container, free the ballast, start it.
- **bare-wal:** as bare, but no checkpoint before the disk is full (`max_wal_size=8GB`, `checkpoint_timeout=1h`), and
  once less than 150 MB is free and the current WAL segment is past 15 of 16 MB, a filler leaves 2 MB: room for data
  pages, not for the next WAL segment. Freeing removes ballast and filler.
- **steward:** the steward runs in the same container (`--tick 1 --reserve-bytes 100MB --reserve-fraction 0`); the
  writer is paced at one batch per 0.25 s (about 1 M samples/s). After the first refusal it tries once a second for
  20 s; then the ballast is removed while everything runs, and the writer retries for up to 90 s.

## Checks and pass

Per run: the first error and its SQLSTATE; whether the server answered and the container ran after it; PANIC lines
in the log; recovery time after the restart (bare arms) or time until appends resume (steward). Then: the stored end
is at least the last acknowledged end and on a batch boundary; every stored sample equals the generator (SHA-256);
`morlet_verify` reports nothing; two more appends succeed.

**Pass:** in every run of every arm, no acknowledged sample is lost or changed and the database accepts appends again
after space is freed. **steward** additionally passes only if every refusal before space is freed is the steward's
hold (SQLSTATE 53100 with "new samples are refused"), the server answers throughout and no PANIC is logged.

## Expectation (written before the reported runs)

bare: an ordinary disk-full error, server up, nothing lost. bare-wal: PANIC, container down until space is freed,
then crash recovery with nothing lost (PostgreSQL acknowledges a commit only after its WAL is flushed). steward: the
hold before the disk is full, no failure at all.

## Not covered

Real hardware and file systems other than ext4 on a loop device; WAL on its own volume; a disk filled by something
other than the database; the steward running in its own container or on another schedule; recovery without restart
in the WAL case.
