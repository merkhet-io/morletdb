# PostgreSQL 19: what it changes for MorletDB

**Development review, 2026-11-19, PostgreSQL 19.0 (released that day); measurements added 2026-11-20.** It reads the
19.0 release notes against pg_morlet's code and operations, runs one development check (`repack_check.sh`), and
compares 17 and 19 on MorletDB's own tasks on a desktop and a Raspberry Pi ([below](#measured-on-morletdbs-own-tasks-2026-11-20)). It is not a frozen protocol and
not a benchmark. The sizes, counts and times below are observations from single development runs on one machine
(Apple M5 Max, macOS); they show that something works, not how fast or how well it works elsewhere. The measurements
section makes speed comparisons only for the tasks, machines and settings it names.

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
   One development run of `repack_check.sh` (it exits non-zero if any check fails), on 19.0 with default settings
   (`wal_level = replica`; 19 enables logical decoding by itself when needed, and `max_repack_replication_slots`
   defaults to 5), observed:
   - 40 recordings × 32 chunks; half the recordings deleted, then `VACUUM`: `rp_chunks` stays at 5.63 MB;
   - `REPACK (CONCURRENTLY) rp_chunks`, while another session appended 160 chunks: 3.06 MB afterwards;
   - every kept chunk is byte for byte the same (md5 of `encoded`), all 800 chunks are there, `morlet_verify`
     reports 0 problems, and every recording reads its last 32,768 samples, including the concurrent appends.

   Limits: a small collection (5.6 MB), so the repack took under a second and overlapped the appends only briefly.
   It was not tried with a full disk, on the Pi, or with probe tables. The rewrite still needs temporary space about
   the size of the kept data, so it cannot rescue a disk that is already full.

   **Decision (2026-11-19): a manual tool, not a steward action for now.** Collections are append-only, so space freed
   by retention is reused by new samples. Giving it back to the operating system matters only for other users of the
   disk, or after a large one-off removal. Against that stand the swap stall, the snapshot behaviour, the extra disk,
   and cleanup held back cluster-wide (third-party measurements below). Runbook 8 step 4 documents the manual use with
   its guards. #535 keeps a steward action conditional on a frozen measurement on the Pi with a slow disk. The gap
   that matters on every PostgreSQL version is the steward's space accounting: it counts only filesystem free space,
   so a retention plan cannot lift the ingest hold at the reserve (#538).

   **Not MVCC-safe, and this matters for MorletDB's readers.** After the repack commits, a transaction whose snapshot
   was taken before the repack started can see the repacked table as empty. A single statement is safe: it holds its
   lock on the table until it ends, and the final swap waits for that lock. The risk is a multi-statement
   `REPEATABLE READ` transaction that takes its snapshot first and reads the table later: the search contract
   recommends that for a single snapshot (`docs/search-contract.md`), and the MCP decision tool does it
   (`tools/mcp/decision.py`). Such a reader could get no rows and report "no match" instead of missing evidence. Guard,
   for runbook 8 now and for the steward action before it is built (#535):
   - start the repack only when no other transaction in the database is open (`pg_stat_activity`: no `xact_start`
     other than the repacking session's, or only idle sessions outside transactions), and pause the steward's own
     readers and the MCP server for its duration;
   - afterwards, check with `morlet_verify` and a count of the collection's chunks that nothing looks empty;
   - the steward action should refuse to start while such a transaction is open and say so, as it does for the disk
     reserve.
2. **Selective `NOTIFY` wakeups.** Commit notifications (#205) send one `pg_notify` per appending transaction. Up to
   18, every `NOTIFY` woke most backends; 19 wakes only the listeners of that channel. It comes free with many
   connections (collectors, steward, MCP, dashboards). Not measured by us. A third-party test on 19rc1 (below) found
   the same commit throughput as 18 and much less wake-up work: it saves database work but does not let more
   notifying appends commit per second, because the commit-time lock for notifications remains.
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

## Third-party performance claims (not MorletDB data)

There are no official PostgreSQL 19 benchmarks. The claims found on 2026-11-19 are single-author blog posts, almost all
on betas or release candidates, on hardware unlike an edge box. They are listed so that nobody repeats them as
MorletDB results.

| Claim | Source, build | Relevance for MorletDB |
|---|---|---|
| `REPACK (CONCURRENTLY)`: writers stopped 7 s at the swap (NVMe, 28 GB, 3,000 updates/s), 13 s and 231 s on a 4-vCPU VM (28 and 100 GB); a 20 s report held all lookups at 0 for 18 s; with `lock_timeout = 3s` the run is discarded; about 1× the kept data in extra disk and 1× the table in WAL; `VACUUM` held back in all databases; a pre-swap `REPEATABLE READ` reader saw 0 rows | [boringsql](https://boringsql.com/posts/repack-concurrently-costs/), 19beta4 | High: these are the costs of runbook 8 step 4 and the steward action (#535); the runbook now names them |
| `NOTIFY`: commit throughput within 3% of 18.6 with 64 clients; the database does about 1/25 of the wake-up work | [DEV](https://dev.to/desty2k/listennotify-performance-in-postgresql-3a82), 19rc1; [DBOS](https://www.dbos.dev/blog/postgres-listen-notify-scalability) on the remaining commit lock | Medium: less load from commit notifications, no faster commits |
| Inserts 25–30% faster, about 3% overall, updates flat to slightly slower (OLTP, cloud VMs, noisy) | Kaarel Moppel, via [iMasters](https://imasters.com/banco-de-dados/postgres-19-beta-shows-inserts-up-to-30-faster-in-independent-oltp-benchmark), 19beta3/4 vs 18.6 | Low to medium: MorletDB's append cost is mostly chunk encoding in Rust and a few large rows |
| Write-heavy steps 15% and 9% faster, slightly more CPU; read-only tests flat except one count query about 2.5× (new plan) | [Small Datum](https://smalldatum.blogspot.com/2026/06/the-insert-benchmark-on-small-server-io.html), [sysbench](https://smalldatum.blogspot.com/2026/06/cpu-bound-sysbench-on-large-server.html), 19beta1 vs 17.10 | Low: small server, but row-oriented workloads |
| Tuple deformation 21% faster on average, up to 44% | [pgsql-hackers](https://www.postgresql.org/message-id/CAApHDvpoFjaj3+w_jD5uPnGazaw41A71tVJokLDJg2zfcigpMQ@mail.gmail.com), micro-benchmarks | Low: MorletDB reads few, large rows |
| Aggregation before joins over 5× faster | [Cybertec](https://www.cybertec-postgresql.com/en/super-fast-aggregations-in-postgresql-19/), one laptop run | None: MorletDB's aggregates run over one table |

## Measured on MorletDB's own tasks (2026-11-20)

Development measurements, not a frozen protocol and not a benchmark: same-session comparisons on two machines, a
few rounds each, with the order of the servers alternating every round. They say how 17 and 19 compare on these
tasks on these machines, nothing more. Result files are in `results/`, and `summarize.py` prints the tables below.

**Setups.**
- **Desktop:** Apple M5 Max (18 cores, 128 GB), macOS. PostgreSQL 17.11 and 19.0 built from source with the same
  flags (no assertions, `-O2`, lz4), each with pg_morlet SQL 0.3.13 (`main` at `7c0b357`) built by
  `cargo pgrx install --release`. Each server ran alone with `shared_buffers = 1GB`, `max_wal_size = 8GB` and
  otherwise default settings. On this desktop, the owner's applications used about two cores throughout
  (load average 4–8).
- **Edge:** Raspberry Pi 3B+ (4 × Cortex-A53, 1 GB RAM, SD card), Debian 11, Docker. Two images from one Dockerfile
  (`Dockerfile.same`) on the official `postgres:17.12-bookworm` and `postgres:19.0-bookworm`, each with pg_morlet
  built from the same commit. `shared_buffers = 128MB`, `max_wal_size = 1GB`. The Pi's firmware reported a soft
  temperature limit at some time since boot (`throttled=0x80000`), not during the runs (57–59 °C).
- **Data:** the B3 Paderborn subset of the TOAST study, `prepare_data.py`: every 20th acquisition on the desktop
  (868 recordings, 101,586,735 samples, SHA-256 `1e13dd017c66…`) and every 40th on the Pi (434 recordings,
  50,853,629 samples).

**Same bytes, same answers.** In every round on both machines, 17 and 19 stored byte-identical chunks and envelopes
(md5 over every row) and returned identical exact search results (20 queries, distances compared bit for bit);
`morlet_verify` found nothing. `same_session.py` stops if any of that differs, so no timing below compares different
results.

**Task times** (`same_session.py`; seconds, median and range; database calls only):

| Task | Desktop 17.11 | Desktop 19.0 | 19/17 | Pi 17.12 | Pi 19.0 | 19/17 |
|---|---:|---:|---:|---:|---:|---:|
| load, 32,768-sample appends | 7.94 (7.85–8.33) | 8.01 (7.75–8.13) | 1.01 | 117 (80–154) | 150 (111–203) | not comparable (SD card) |
| build envelopes | 9.68 (9.64–10.02) | 9.76 (9.65–9.86) | 1.01 | 164 (132–192) | 155 (145–210) | not comparable (SD card) |
| read every sample | 2.29 (2.29–2.32) | 2.15 (2.08–2.17) | **0.93** | 15.5 (15.2–15.8) | 14.4 (14.0–14.7) | **0.93** |
| 2,000 random reads of 4,096 samples | 0.79 (0.79–0.80) | 0.77 (0.76–0.79) | 0.98 | 9.71 (9.66–9.77) | 9.68 (9.46–9.82) | 1.00 |
| exact search, 20 queries | 0.58 (0.57–0.58) | 0.57 (0.57–0.58) | 0.99 | 6.04 (6.04–6.19) | 6.21 (6.03–6.24) | 1.03 |
| `morlet_latest`, every recording | 0.51 (0.49–0.52) | 0.52 (0.51–0.60) | 1.02 | 3.13 (3.08–3.31) | 3.23 (3.05–3.46) | 1.03 |
| `morlet_verify`, every recording | 1.71 (1.70–1.75) | 1.73 (1.70–1.85) | 1.01 | 9.90 (9.85–10.01) | 10.06 (9.66–10.71) | 1.02 |

- Desktop: 5 rounds per server; Pi: 3 rounds, plus one smoke round not counted. 19 ran with its default data
  checksums, 17 without (see below).
- **Reading every sample is 7% faster on 19 on both machines**, in every round. Not attributed: the query decodes
  chunks in Rust and the remaining time is PostgreSQL's executor, where 19 has several improvements.
- **Everything else is within ±3%**, about the spread between rounds.
- JIT: 17 has it on by default, 19 off. A third desktop arm, 17 with `jit = off`, matched 17 with JIT on in every
  task (within 2%), so the setting does not explain the difference.
- **Pi writes:** loads and envelope builds varied by up to 2× between rounds of the same server, so these rows
  compare nothing. Loads on the desktop wrote the same WAL on both versions (74.76 MB on 17, 74.84 MB on 19, three
  rounds each, `load_io.py`). On the Pi, a first `load_io.py` run (5 rounds) again favoured 17 (median 98 s
  against 132 s, with the same WAL, 75.2–75.9 MB); a second run with three servers (5 rounds, below) did not
  (median 93.6 s on 17, 95.7 s on 19 and 95.9 s on 19 without checksums, each varying up to 1.5×). We see no write
  difference between the versions that this SD card lets us establish.
- **Data checksums:** since 18, `initdb` turns them on by default, so the 19 clusters in all these runs had them
  and the 17 clusters did not, as a default installation would. Arms of 19 without checksums (`--no-data-checksums`)
  matched 19 with them: on the desktop within 1% on every task (3 rounds,
  `results/mac_same_session_checksums.json`), on the Pi in load time (`results/pi_load_io3.json`).

**Commit notifications with idle listeners** (`notify_check.py`): one writer commits small appends (1,024 samples,
one transaction each) to a collection with notifications on; one consumer listens on its channel; K further
connections `LISTEN` on unrelated channels and stay idle. Every run delivered exactly one notification per append.

| Machine | Server | Idle listeners | Commits/s, median (range) | p99 append latency |
|---|---|---:|---:|---:|
| Desktop | 17.11 | 0 | 1,511 (1,435–1,526) | 1.00 ms |
| Desktop | 17.11 | 200 | **698** (612–730) | 1.84 ms |
| Desktop | 19.0 | 0 | 1,762 (1,749–1,799) | 0.91 ms |
| Desktop | 19.0 | 200 | **1,738** (1,727–1,777) | 0.92 ms |
| Pi | 17.12 | 0 | 64 (63–66) | 23.0 ms |
| Pi | 17.12 | 50 | **54** (52–54) | 26.8 ms |
| Pi | 19.0 | 0 | 73 (73–74) | 21.0 ms |
| Pi | 19.0 | 50 | **71** (69–75) | 21.0 ms |

- Desktop: 3,000 appends per run, 3 rounds; the CPU time of all postgres processes during the appends was 0.56 s per
  1,000 appends on 17 without idle listeners and **4.62 s** with 200, against 0.47 s either way on 19. Pi: 1,000
  appends, 3 rounds; its `ps` counts CPU in whole seconds, too coarse to report.
- Up to 18, a notifying commit wakes listening backends whatever channel they listen on; 19 wakes only that
  channel's listeners (release notes). This measures what that means for MorletDB's notifications.
- 19 also commits 14–17% more notifying appends per second without idle listeners, on both machines. Not
  attributed.

**Giving space back while appends go on** (`repack_stall.py`): load, remove every other recording, `VACUUM` (the
files keep their size), then rewrite the chunk table while one writer appends 4,096-sample chunks, one transaction
each. One run per row.

| Machine | Rewrite | Size before → after | Rewrite took | Appends during | Longest wait for one append | Old snapshot sees |
|---|---|---|---:|---:|---:|---|
| Desktop | 19.0 `REPACK (CONCURRENTLY)` | 146 → 84 MB | 0.31 s | 365 | 2.6 ms | **0 of 1,744 chunks** |
| Desktop | 17.11 `VACUUM FULL` | 146 → 84 MB | 0.45 s | 1 | 455 ms (all of it) | 1,744 of 1,744 |
| Desktop | 19.0 `VACUUM FULL` | 146 → 84 MB | 0.42 s | 1 | 418 ms (all of it) | 1,744 of 1,744 |
| Pi | 19.0 `REPACK (CONCURRENTLY)` | 72 → 37 MB | 7.2 s | 37 (median 97 ms, before 14 ms) | 2.1 s | **0 of 875 chunks** |
| Pi | 17.12 `VACUUM FULL` | 72 → 37 MB | 6.7 s | 1 | 6.7 s (all of it) | 875 of 875 |
| Pi | 19.0 `VACUUM FULL` | 72 → 37 MB | 12.9 s | 1 | 12.9 s (all of it) | 875 of 875 |

- In every run, the kept chunks were byte for byte unchanged, every concurrent append was stored, and
  `morlet_verify` reported no problems.
- `REPACK (CONCURRENTLY)` kept appends going: on the desktop they did not notice; on the Pi's SD card they slowed
  about 7× and one waited 2.1 s. `VACUUM FULL` stopped them for the whole rewrite.
- **The snapshot problem is real for MorletDB.** A `REPEATABLE READ` transaction that took its snapshot before the
  repack (reading only `pg_class`) and counted the collection's chunks afterwards saw none, on both machines. After
  `VACUUM FULL` it saw all of them. Such a reader would report "no match" where data exists, which is why runbook 8
  requires no open transaction during a repack, and why the steward does not repack by itself (#535).
- The two Pi `VACUUM FULL` times differ by 2×: single runs on an SD card, not a version difference.

**`pg_upgrade` from 17 to 19** (`upgrade_check.py`, desktop only: a collection of 434 recordings with envelopes,
time anchors on four recordings, row security and commit notifications; 241 MB cluster).
- **A default `initdb` of 19 cannot take a 17 cluster:** since 18, `initdb` enables data checksums, and `pg_upgrade
  --check` refuses an old cluster without them ("old cluster does not use data checksums but the new one does").
  Create the new cluster with `--no-data-checksums`, or turn checksums on in the old cluster first
  (`pg_checksums --enable` while it is stopped; it rewrites every page).
- With that, `--copy`, `--clone` and `--link` each upgraded in about 2 s. In each, the new cluster held byte-identical
  chunks, envelopes and time anchors, gave identical search results and anchor times, kept row security and the
  notification setup, and passed `morlet_verify`. `ALTER EXTENSION pg_morlet UPDATE` had nothing to do (0.3.13).
- Optimizer statistics came across (the chunk table's `pg_stats` rows were present before any `ANALYZE`), as
  pg_upgrade does since 18.
- Not tested: the Debian `pg_upgradecluster` wrapper, the container images (the official images do not ship two
  majors), clusters with pgvector probe tables, and large clusters.

**What this changes.**
- Nothing in pg_morlet needs to change for 19. 19 is not slower on any task we measured, and two things are better:
  full reads (7%) and commit notifications with many listeners (17 loses half its notifying commit rate to 200 idle
  listeners; 19 loses none).
- The release image stays on PostgreSQL 17 for 0.4; images and packages for 19 (#537) can now be built on the
  official `postgres:19` image, which is available (checked 2026-11-20).
- Operations: the upgrade guide needs the checksum step, and on 17 many idle `LISTEN` connections cost notifying
  appends throughput and server CPU (docs/operations.md).

**Reproduce.**

```sh
python evaluation/pg19_features/prepare_data.py --out /tmp/b3-20 --every 20      # needs the B3 data (tsdb_b3)
# servers.json: [{"name": "pg17", "dsn": ..., "start": "<shell>", "stop": "<shell>"}, {"name": "pg19", ...}]
python evaluation/pg19_features/same_session.py --data /tmp/b3-20 --servers servers.json --rounds 5 --out same.json
python evaluation/pg19_features/notify_check.py --servers servers.json --listeners 0,200 --out notify.json
python evaluation/pg19_features/repack_stall.py --server pg19 --servers servers.json --data /tmp/b3-20 --mode repack --out r.json
python evaluation/pg19_features/load_io.py --data /tmp/b3-20 --servers servers.json --rounds 3 --out io.json
python evaluation/pg19_features/upgrade_check.py --bin17 <17/bin> --bin19 <19/bin> --data /tmp/b3-40 --work /tmp/upg --out upg.json
python evaluation/pg19_features/summarize.py same.json notify.json
```

## Reproduce

```sh
# a PostgreSQL 19 build with pg_morlet installed (cargo pgrx install --pg-config <its pg_config> --features pg19)
PG19_BIN=/path/to/19.0/bin bash evaluation/pg19_features/repack_check.sh
```

It creates a throwaway cluster in a temporary directory on port 28839 and removes it at the end.
