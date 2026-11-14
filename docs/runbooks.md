# Runbooks: operating MorletDB

Something is wrong and you do not know where to start: go to [10. First hour](#10-first-hour-triage).

| Runbook | How it was checked |
|---|---|
| [1. Backup with WAL archiving](#1-backup-with-wal-archiving), [2. Restore](#2-restore-to-the-latest-state-or-to-a-point-in-time), [3. Streaming replica](#3-streaming-replica), [4. Failover](#4-failover) | executed step by step by a drill in CI (`evaluation/restore_r1/drill.py`, `evaluation/replica_r2/drill.py`, weekly on amd64 and arm64) |
| [5. Disk full](#5-disk-full-or-nearly-full), [6. WAL growth](#6-wal-growth), [7. Rebuilding search data](#7-rebuilding-envelopes-probe-rows-and-indexes), [8. Retention and archiving](#8-retention-and-archiving-to-parquet-and-s3), [9. Upgrade](#9-upgrading-the-extension-and-the-image) | run by hand once, on 2026-11-14, against disposable containers of the release image `ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1` (PostgreSQL 17.6); each says what was simulated and what was not exercised. Not run by CI |

MorletDB is a PostgreSQL extension, so most of these are PostgreSQL's own procedures with MorletDB's checks added.
If a step here and a drill, or the software, disagree, that is a bug; please report it.

Names used below: the database `morlet`, the user `morlet`, a collection `line`. Replace them with your own. The data
directory is `/var/lib/postgresql/data`, as in the release image. `steward.py` is
`python tools/steward/steward.py` from a checkout (`pip install -r tools/steward/requirements.txt`) or
`python steward.py` inside the steward's sidecar container.

## Check after every restore or failover

Run this on the restored or promoted database before letting writers or readers back in:

```sql
SELECT extversion FROM pg_extension WHERE extname = 'pg_morlet';     -- the version you expect
SELECT recording_id, chunks, samples, problems                        -- must return no problems
  FROM morlet_verify('line') WHERE problems <> '{}';
SELECT recording_id, (checkpoint->>'end')::bigint AS stored_end        -- compare with what your writers last
  FROM line ORDER BY 1;                                                --   saw acknowledged
```

`morlet_verify` decodes every stored chunk (structure and CRC-32C) and checks that summaries, envelopes, roll-ups and
context agree with the samples. Derived data that fails a check can always be rebuilt from the samples; the samples,
context and labels cannot.

## 1. Backup with WAL archiving

1. In `postgresql.conf`: `archive_mode = on`, `archive_command = 'test ! -f /archive/%f && cp %p /archive/%f'` (or
   the command of your backup tool), `archive_timeout = 60`. Restart PostgreSQL.
2. Take a base backup: `pg_basebackup -U morlet -D /backup/base -Fp -X stream -c fast`.
3. Take a new base backup regularly (for example weekly) and keep the WAL since the oldest base backup you keep.
4. Watch `SELECT last_archived_wal, failed_count FROM pg_stat_archiver;` — a growing `failed_count` means the archive
   is not working.

Logical dumps (`pg_dump -Fc`, see [operations](operations.md#backup-and-restore)) are a good addition but cannot
restore to a point in time.

## 2. Restore to the latest state or to a point in time

1. Stop every writer. Keep the broken data directory aside if there is one; do not delete it until the check passed.
2. Copy the base backup into an empty data directory owned by the PostgreSQL user, mode 700.
3. Create an empty `recovery.signal` file in it.
4. Set `restore_command = 'cp /archive/%f %p'` and `recovery_target_action = 'promote'`. For a point in time also set
   `recovery_target_time = '2026-11-08 14:05:00+01'` (the last moment you want to keep).
5. Start PostgreSQL. Wait until `SELECT pg_is_in_recovery();` returns `false`.
6. Run the check above. With a point-in-time target, everything committed after the target is absent; that is
   intended.
7. Take a new base backup: the restored server starts a new timeline.

Measured by drill R1 (Docker, 200 batches of 4,096 samples): every sample, context row and envelope identical after
both restores, `morlet_verify` without problems.

## 3. Streaming replica

1. On the primary, allow replication connections in `pg_hba.conf`:
   `host replication morlet <replica address> scram-sha-256`, then `SELECT pg_reload_conf();`.
2. On the replica host, into an empty data directory:
   `pg_basebackup -h <primary> -U morlet -D <datadir> -X stream -R -c fast`. `-R` writes `standby.signal` and the
   connection settings.
3. Start PostgreSQL on the replica, with the same MorletDB version installed as on the primary.
4. Watch the lag on the primary: `SELECT application_name, state, replay_lag FROM pg_stat_replication;`.
5. Choose the durability you need:
   - **Synchronous** (`synchronous_standby_names = '*'` on the primary): a `COMMIT` returns only when the replica has
     the transaction, so a failover loses nothing that was acknowledged. Writes stop while no replica is connected.
   - **Asynchronous** (the default): writes never wait for the replica; a failover can lose the last transactions
     that were acknowledged but not yet sent. Drill R2 measures that window.

The replica answers every read and search; it holds the same samples, envelopes and context, and search returns
the same matches and distances as on the primary (checked by drill R2).

## 4. Failover

1. Make sure the old primary is really down and stays down (power it off, stop its container or service). Two
   primaries accepting writes is the one mistake that cannot be repaired afterwards.
2. On the replica: `SELECT pg_promote();` Wait until `SELECT pg_is_in_recovery();` returns `false`.
3. Run the check above. Compare each recording's `stored_end` with what your writers last saw acknowledged; with an
   asynchronous replica the difference is the data lost.
4. Point the writers and readers at the new primary. MorletDB's writers resume from the stored end
   (`checkpoint->>'end'`), as the collectors do after any restart.
5. Rebuild the old primary as a new replica from a fresh base backup (step 3.2); do not start it as it was.

For automatic failover use a cluster manager (for example Patroni); the steps it performs are the same.

## 5. Disk full or nearly full

**Symptoms**

- Metrics (`steward.py metrics`, see [operations](operations.md#diagnostics)): `morlet_disk_free_bytes` falls towards
  the reserve, `morlet_ingest_hold 1`, `morlet_steward_state 2`. The disk values are the steward's latest sample.
  Run `metrics` from a checkout: the sidecar image built from `deploy/steward/Dockerfile` lacks `metrics.py` in this
  release and fails with `No module named 'metrics'`.
- `steward.py status` prints `ACTION REQUIRED` with "the disk reserve is reached"; `steward.py check` exits 2.
- Writers get SQLSTATE 53100 (`disk_full`). Two different errors use this code:
  - `new samples are refused since …: <reason>`: the ingest hold. Nothing was written and the server is fine.
  - `could not extend file "base/…": No space left on device`: the disk is really full. The server still answers.
- In the PostgreSQL log, `PANIC: could not write to file "pg_wal/…": No space left on device`: the write-ahead log
  could not grow, and the server stops or restarts until space is free.

**Check**

```sh
df -h /var/lib/postgresql/data       # the filesystem that holds the data directory (in the container or on the host)
```

```sql
SELECT * FROM morlet_ingest_hold;                                    -- a hold: its reason, who set it, since when
SELECT pg_size_pretty(pg_database_size(current_database())) AS database;
SELECT count(*) AS wal_files, pg_size_pretty(sum(size)) AS wal FROM pg_ls_waldir();   -- superuser or pg_monitor
SELECT c.oid::regclass AS relation, pg_size_pretty(pg_total_relation_size(c.oid)) AS size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
 ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 10;
SELECT recordings, samples, pg_size_pretty(chunk_table_bytes) AS chunks, pg_size_pretty(total_bytes) AS total
  FROM morlet_collection_storage('public.line');
```

If the WAL is a large part of the disk, follow [6. WAL growth](#6-wal-growth) first: an inactive replication slot
or a failing archive is fixed without touching any data.

**Act**

1. **Stop new samples before PostgreSQL runs out of space.** With the steward this has already happened. Without it,
   set the hold yourself:

   ```sql
   SELECT morlet_set_ingest_hold('disk nearly full: extending the volume (ticket 42)');
   ```

   Appends that would store new samples then fail with 53100 and this reason; identical replays still pass. `COPY` or
   `INSERT` straight into chunk tables bypasses the hold.
2. **Make sure the writers keep their data.** Collectors that spool to their own disk (the
   [SeedLink](../examples/collectors/seedlink/README.md) and [Shelly](../examples/collectors/shelly/README.md) example
   collectors, [Akhet](integrations.md)) replay it later from where the database ended. Check that their spool has room
   for the outage. A writer without a spool must keep its samples and retry from the stored end
   (`checkpoint->>'end'`). The general collector spooler is a design ([collector-spooler.md](collector-spooler.md)),
   not shipped.
3. **Add space.** Grow the volume and its filesystem (for ext4 on a grown device, `resize2fs <device>` works while the
   database runs), move the data directory to a larger disk, or remove files on the same filesystem that are not the
   database's.
4. **If the server stopped with a PANIC:** free space first (step 3), then start it. It runs crash recovery and comes
   back with every committed transaction. Run nothing else on the full disk before that.
5. **Free space inside the database only through the documented paths:** [8. Retention](#8-retention-and-archiving-to-parquet-and-s3)
   with a verified copy. Envelopes and probe rows can be deleted and rebuilt ([7](#7-rebuilding-envelopes-probe-rows-and-indexes)),
   but they are usually small next to the chunks; check the sizes above first.
6. **Release a hold you set** once free space is above the reserve again: `SELECT morlet_release_ingest_hold();`.
   The steward releases a hold it set by itself.

**Do not**

- delete files in `pg_wal` or anywhere else in the data directory, or run `pg_resetwal`: both lose committed data or
  break the database;
- delete chunks or recordings to make room without a verified copy;
- run `VACUUM FULL`, `REINDEX`, `CHECKPOINT`, `ALTER SYSTEM` or other maintenance on a full disk: they need space
  themselves. In the check below, `ALTER SYSTEM` failed and a manual `CHECKPOINT` made the checkpointer PANIC;
- release the hold while the disk is still at the reserve.

**Verify afterwards**

- `SELECT * FROM morlet_ingest_hold;` returns no row, `morlet_ingest_hold 0`, and `steward.py status` is `NORMAL` or
  `ATTENTION`.
- Every writer has resumed. Compare each recording's `stored_end` with what the writers last saw acknowledged (the
  last query of [Check after every restore or failover](#check-after-every-restore-or-failover)): nothing
  acknowledged may be missing.
- `morlet_verify` finds no problems (same check).
- Size the reserve for your ingest rate: in the check below an unpaced writer at a 1-second tick ended about 40 MB
  below a 100 MB reserve. The default is the larger of 5% and 2 GB.

**How this was checked.** 400 MB filesystems in Docker and a writer appending full-entropy samples, 2 MB per commit,
as fast as it could:

- **With the steward** (tmpfs volume, the sidecar image built from `deploy/steward/Dockerfile`, reserve 100 MB, tick
  1 s): appends were refused by the hold, with 79 MB still free, and the server answered throughout. After a
  150 MB ballast file was removed (standing in for added space), the steward released the hold within seconds and
  appends resumed without a restart.
- **Without it** (an ext4 file system in a loop file): the first error was `could not extend file`, and the server
  kept answering. The filesystem was grown while the server ran (`truncate`, `losetup -c`, `resize2fs`), and the writer
  resumed from the stored end. When the disk was full again, a manual `CHECKPOINT` made the checkpointer PANIC; the
  server restarted, ran crash recovery and accepted connections again within a second, before any space was added.
- **Each time** every acknowledged sample read back identical (SHA-256 over the recording), and `morlet_verify` found
  nothing.
- A WAL PANIC (`pg_wal/xlogtemp…`) also happened once, on the tmpfs volume, but that volume lost its contents when the
  container stopped, so recovery from it was not checked here. FD1 checked it ([results](../evaluation/full_disk_fd1/RESULTS.md),
  3 runs on 0.3.9-alpha.1); see also the [Raspberry Pi disk fill](../evaluation/disk_fill/RESULTS.md).
- A real disk, other filesystems, and space taken by another process: not exercised.

## 6. WAL growth

**Symptoms**

- The `pg_wal` directory grows well beyond `max_wal_size`, and free disk falls while `morlet_database_bytes` stays
  flat.
- `failed_count` in `pg_stat_archiver` grows; the log says `archive command failed with exit code …`.
- A replica that has been gone for a while.

Up to about `max_wal_size` of WAL is normal: PostgreSQL keeps old segments for reuse, and `pg_wal` does not shrink
right after the cause is fixed.

**Check**

```sql
SELECT count(*) AS wal_files, pg_size_pretty(sum(size)) AS wal FROM pg_ls_waldir();
SELECT name, setting, unit FROM pg_settings
 WHERE name IN ('max_wal_size', 'wal_keep_size', 'max_slot_wal_keep_size', 'archive_mode', 'archive_command');
-- 1. replication slots that hold WAL
SELECT slot_name, slot_type, active, wal_status,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained_wal
  FROM pg_replication_slots;
-- 2. the archive
SELECT archived_count, failed_count, last_archived_wal, last_failed_wal, last_failed_time FROM pg_stat_archiver;
SELECT count(*) AS waiting_to_archive FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready';
-- 3. long transactions
SELECT pid, usename, application_name, state, now() - xact_start AS xact_age, backend_xmin
  FROM pg_stat_activity WHERE xact_start IS NOT NULL AND pid <> pg_backend_pid() ORDER BY xact_start;
SELECT gid, prepared, owner FROM pg_prepared_xacts;
```

**Act**

- **An inactive replication slot** (`active = f`, a large `retained_wal`) keeps every segment its replica has not
  received yet.
  - If the replica comes back soon and the disk lasts until then, start the replica; it catches up and the slot lets
    go of the WAL.
  - If the replica is gone for good, drop its slot: `SELECT pg_drop_replication_slot('old_replica');`. Rebuild the
    replica later from a new base backup ([3](#3-streaming-replica)).
  - To cap this in future: `ALTER SYSTEM SET max_slot_wal_keep_size = '10GB'; SELECT pg_reload_conf();`. A slot that
    falls further behind is given up (`wal_status = lost`), and its replica must be rebuilt.
- **A failing `archive_command`** keeps every segment that has not been archived (`waiting_to_archive`). Repair the
  destination (mount, permissions, space, or your backup tool's credentials). The archiver retries by itself; no
  restart is needed. If it cannot be repaired before the disk is full, add space ([5](#5-disk-full-or-nearly-full)).
  A setting given on the server's command line (`postgres -c …`, as in `deploy/steward/compose.yaml`) wins over
  `ALTER SYSTEM`: change it where it is set, and check the result in `pg_settings`.
- **Long transactions** do not keep WAL themselves in PostgreSQL. They keep dead rows: `VACUUM` cannot remove rows
  deleted after the oldest open snapshot, so tables grow. `REINDEX CONCURRENTLY` also waits for them to finish.
  - Ask the owner of the session, then end it with `SELECT pg_terminate_backend(<pid>);`.
  - A terminated append rolls back completely, and its writer resumes from the stored end.
  - To prevent it, set `idle_in_transaction_session_timeout` for the roles of reports and tools.
  - A prepared transaction (`pg_prepared_xacts`) must be finished by its owner with `COMMIT PREPARED` or
    `ROLLBACK PREPARED` (not exercised).
- **Ingest itself writes much WAL** with default checkpoint settings (about 55 GB per hour at the tested pilot rate).
  See the WAL settings in [operations](operations.md#configuration).

**Do not**

- delete WAL files by hand, run `pg_resetwal`, or point `pg_archivecleanup` at `pg_wal` (it is for the archive);
- make `archive_command` succeed without archiving (for example `true`): a point-in-time restore across that gap
  becomes impossible. If it has been done, take a new base backup at once ([1](#1-backup-with-wal-archiving)).

**Verify afterwards**

- No slot is left with `active = f` that nobody needs, and `retained_wal` of the others is small.
- `failed_count` stops rising, `waiting_to_archive` is 0, `last_archived_wal` moves on. The archive holds every
  segment since the oldest base backup you keep, without a gap.
- After ending a long transaction, `VACUUM (VERBOSE) <table>` reports `0 are dead but not yet removable`.
- Over the next checkpoints `pg_wal` falls back to about `max_wal_size` or less.

**How this was checked.** One container with WAL archiving to a local directory and `max_wal_size = 256MB`:

- **Slot:** a physical slot, never used, retained 130 MB after 120 MB of appends. After `pg_drop_replication_slot`
  it retained nothing; `pg_wal` stayed at 144 MB as segments for reuse. With `max_slot_wal_keep_size = 64MB`, a new
  slot became `lost` after 100 MB of appends.
- **Archive:** the archive directory was made unwritable. `failed_count` reached 8 with 8 segments waiting. After the
  permissions were repaired, every segment was archived within 65 s, 44 segments without a gap.
- **Long transaction:** an idle session in a `REPEATABLE READ` transaction kept 31 deleted rows
  `dead but not yet removable`. After `pg_terminate_backend`, `VACUUM` removed them.

## 7. Rebuilding envelopes, probe rows and indexes

Envelopes, probe rows and their indexes are derived from the chunks and can always be rebuilt from them. Chunks,
context and labels cannot. There is no `build_search_index` function; the tools are `morlet_build_envelopes`, the
probe-row statement below, and PostgreSQL's `REINDEX`.

**Symptoms**

- Metrics: `morlet_collection_unbuilt_samples`, `morlet_collection_recordings_without_envelopes` or
  `morlet_collection_probe_backlog` growing; `morlet_steward_breaker_open{job="envelopes"} 1`.
- `steward.py status`: `envelopes failed 3 times: …`.
- A build fails, for example `envelopes of window 4096 for recording p6 were built with stride 1024, not 2048`.
- `could not resize shared memory segment … No space left on device` during `REINDEX` or a probe index build in a
  container (Docker's `/dev/shm` is 64 MB by default).
- Searches miss windows that you know exist.

**Check**

```sql
SELECT window_size, recordings, envelopes, recordings_without_envelopes, unbuilt_samples, probe_rows, probe_backlog
  FROM morlet_collection_status('public.line');
-- what a search can find, per state (pass recording_ids for large collections; about 1.3 µs per window)
SELECT state, count(*) AS ranges, sum(end_sample - start_sample) AS samples, sum(windows) AS windows
  FROM morlet_coverage('public.line', NULL, 4096) GROUP BY state ORDER BY state;
SELECT recording_id, start_sample, end_sample, state, windows
  FROM morlet_coverage('public.line', ARRAY['p5', 'p6'], 4096) ORDER BY 1, 2;
-- indexes left invalid by a failed REINDEX CONCURRENTLY or CREATE INDEX CONCURRENTLY
SELECT c.oid::regclass AS index, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
 WHERE NOT i.indisvalid;
```

Keep the coverage output: it is the "before" of this runbook. Until the build finishes, report every negative
search together with what was `not_indexed` ([search contract](search-contract.md#not-found-versus-not-searched-sql-036)).

**Act**

- **A partial or aborted build.** An aborted call leaves nothing behind; run the builder again. It continues after the
  last stored window. To finish every recording, one committed batch at a time:

  ```sql
  DO $$
  DECLARE r text; n bigint; before bigint; after bigint;
  BEGIN
    FOR r IN SELECT recording_id FROM public.line ORDER BY recording_id LOOP
      LOOP
        SELECT count(*) INTO before FROM public.line_envelopes WHERE recording_id = r AND window_size = 4096;
        n := morlet_build_envelopes('public.line', r, 4096, 2048, 10000);
        COMMIT;
        SELECT count(*) INTO after FROM public.line_envelopes WHERE recording_id = r AND window_size = 4096;
        EXIT WHEN n = 0 OR after = before;
      END LOOP;
    END LOOP;
  END $$;
  ```

  Use the window size and stride the collection was built with. The second exit condition is needed only before SQL
  0.3.12: there, on samples compacted by episodic memory the builder returns the number of windows it skipped, not 0,
  so "until it returns 0" never ends (#488, fixed in 0.3.12). Run it outside an explicit transaction (`COMMIT` inside
  `DO` needs that). With the steward running, you can instead let its envelope job catch up.
- **A build with the wrong stride, or a change of window grid.** The error's hint is the procedure: delete that
  recording's envelopes of that window size and build again. Deleting envelopes deletes their probe rows too.

  ```sql
  DELETE FROM line_envelopes WHERE recording_id = 'p6' AND window_size = 4096;
  ```

  Then run the loop above. Search finds fewer windows for that recording until the build has finished.
- **A representation change.** Every release so far has one envelope representation, `xc-envelope-f16-v1`
  (`SELECT morlet_envelope_version();`). Distances are comparable only within one version. If a release changes it,
  its notes will say how to rebuild. Until then the procedure is the one above, per recording. A real representation
  change: not exercised (none exists).
- **A probe backlog** (`probe_backlog > 0`: envelopes without a probe row, for example written by an external worker
  or built before probe search was enabled). Fill it with the statement `morlet_build_envelopes` uses itself:

  ```sql
  INSERT INTO line_probes_4096
  SELECT e.recording_id, e.window_size, e.start_sample, morlet_envelope_decode(e.envelope)::halfvec
    FROM line_envelopes e
   WHERE e.window_size = 4096
     AND NOT EXISTS (SELECT 1 FROM line_probes_4096 p
                      WHERE p.recording_id = e.recording_id AND p.window_size = e.window_size
                        AND p.start_sample = e.start_sample)
  ON CONFLICT DO NOTHING;
  ```

  `morlet_envelope_probe_relation('public.line', 4096)` names the probe table. If pgvector is installed in another
  schema, qualify `halfvec` with it.
- **A probe index or another index to rebuild** (bloat after deletions, an invalid index, a pgvector update that
  asks for it), while searches keep running:

  ```sql
  SET maintenance_work_mem = '2GB';                -- see the configuration table in operations.md
  REINDEX INDEX CONCURRENTLY line_probes_4096_v_idx;
  REINDEX TABLE CONCURRENTLY line_chunks;          -- the same for every index of a table
  ```

  If it fails, it leaves an invalid index named `…_ccnew`. Drop it with
  `DROP INDEX CONCURRENTLY line_probes_4096_v_idx_ccnew;`, fix the cause, and run the `REINDEX` again. In a container
  with the default 64 MB `/dev/shm`, a parallel HNSW build fails to get shared memory. Start the container with
  `--shm-size`, or build without parallel workers: `SET max_parallel_maintenance_workers = 0;`.
- **A breaker the steward opened** for a job: fix the cause first, then `steward.py reset envelopes` as a
  `morlet_approver` (not exercised).

**Do not** delete or rewrite chunks, context or labels to repair search data, and do not run `REINDEX` without
`CONCURRENTLY` on a live system: it blocks writes to the table.

**Verify afterwards**

- `morlet_collection_status`: `unbuilt_samples` is at most about one stride per recording plus the ingest delay, and
  `probe_backlog = 0`. Samples removed at memory level 4 still count as unbuilt there (levels 2 and 3 not checked);
  `morlet_coverage` shows them as `gone`.
- `morlet_coverage` again: every range that was `not_indexed` is now `searchable`, apart from tails shorter than a
  window. Compare with the "before".
- The index query returns no invalid index.
- A search you ran before the rebuild returns the same rows. Exact search does not depend on the probe index, and
  returned distances are always exact.

**How this was checked.** Six recordings of 65,536 samples plus two of 16 and 31 M samples, window 4096, stride 2048,
pgvector 0.8.7 added to the release image's container (the image has no pgvector):

- Envelopes built for four recordings, ten windows of a fifth, none of the sixth; coverage showed `searchable` and
  `not_indexed` ranges per recording. A build rolled back left no rows. A build with stride 1024 made the next build
  fail with the stride error; after the `DELETE` the loop built every recording: 23,224 windows, `unbuilt_samples` 0,
  every range `searchable`.
- 31 deleted probe rows showed as `probe_backlog` 31; the statement above filled them.
- `REINDEX INDEX CONCURRENTLY` on the HNSW index with `maintenance_work_mem = 256MB` failed on the 64 MB
  `/dev/shm` and left `line_probes_4096_v_idx_ccnew` invalid. After the `DROP INDEX CONCURRENTLY` it succeeded with
  `max_parallel_maintenance_workers = 0`. Probe search returned the same five rows and distances before, with the
  invalid index present, and after. `REINDEX TABLE CONCURRENTLY line_chunks` succeeded.

## 8. Retention and archiving to Parquet and S3

MorletDB has **no retention setting yet**: nothing expires by itself, and there is no cold tier that serves reads
from object storage (#204). Chunks are not shipped to object storage as chunks either (#457). What exists:

- `tools/ingest/morlet_export.py`: recordings to a Parquet [exchange file](exchange-format.md), with `--verify`;
- `tools/ingest/morlet_s3.py export`: the same file uploaded to S3-compatible storage, read back and compared
  ([object storage](object-storage.md));
- episodic memory, level 4 (`gone`): removes an interval's samples and keeps the episode and a ledger row, so
  `morlet_coverage` still reports what was removed ([memory design](memory-design.md));
- the steward's plans: a person proposes the move, another person approves it, and it is applied in one
  transaction ([steward](../tools/steward/README.md)).

Raw data is authoritative until it is removed this way. Remove nothing before its copy is verified.

**Symptoms**

- The steward's status: `disk full in N days at the current rate`, with the action "approve a retention plan or add
  storage".
- Recordings past the retention period your data owner has set.

**Check**

```sql
SELECT recording_id, (checkpoint->>'end')::bigint AS samples, metadata FROM line ORDER BY recording_id;
SELECT recordings, samples, pg_size_pretty(chunk_table_bytes) AS chunks, pg_size_pretty(total_bytes) AS total
  FROM morlet_collection_storage('public.line');
SELECT * FROM morlet_coverage('public.line', ARRAY['p1']);          -- what is still stored of the candidates
```

**Act**

1. **Export and verify the copy.** Select recordings by id locally, or by metadata for S3:

   ```sh
   python tools/ingest/morlet_export.py line-day1-2.parquet --collection public.line --recording p1 --recording p2
   python tools/ingest/morlet_export.py --verify line-day1-2.parquet --collection public.line --recording p1 --recording p2
   # to S3-compatible storage (credentials from boto3's usual sources, never on the command line):
   export AWS_ENDPOINT_URL=https://s3.example.internal
   python tools/ingest/morlet_s3.py export s3://archive/line/day1.parquet --collection public.line \
       --metadata-filter '{"day":1}'
   ```

   `morlet_s3.py` selects by metadata only. Keep its output: the object, `sha256` and `content_sha256`. To check the
   object itself, download it and run `morlet_export.py --verify` on the download.
2. **Prove that the copy restores**, into a scratch collection, then drop that collection:

   ```sh
   python tools/ingest/morlet_import.py line-day1-2.parquet --collection public.line_restore_test --create
   python tools/ingest/morlet_export.py --verify line-day1-2.parquet --collection public.line_restore_test
   ```

   An exchange file carries samples, rates, channels, units, metadata and quanta, not context, labels or search
   data ([what is preserved](exchange-format.md#what-is-preserved-and-what-is-not)). Export context and labels
   separately if you need them; there is no tool for that yet.
3. **Remove the samples at memory level 4**, so the database keeps a record of what was there:

   ```sql
   SELECT morlet_enable_memory('public.line');                         -- once per collection
   SELECT morlet_add_episodes('public.line',
     '[{"recording_id":"p1","start_sample":0,"end_sample":65536,"context":{"retention":"day 1"}}]');
   ```

   With the steward installed, run `steward.py grant` again (memory adds tables), then propose the move as a plan. Put
   the copy's location and checksum in the move's `reason`; the ledger keeps it. (The ledger's `cold_copy` column is
   not filled by `morlet_set_level` in this release.) `moves.json`:

   ```json
   {"policy": "retention-manual",
    "moves": [{"episode_id": 1, "level": 4,
               "reason": {"why": "older than the retention period",
                          "cold_copy": "s3://archive/line/day1.parquet",
                          "content_sha256": "<content_sha256 from the export>"}}]}
   ```

   ```sh
   steward.py propose-levels public.line moves.json --dsn "postgresql://<you>@host/morlet"   # prints the bytes freed (dry run)
   steward.py approve 1 --dsn "postgresql://<approver>@host/morlet"   # another person, with morlet_approver
   steward.py apply 1 --dsn "postgresql://<you>@host/morlet"          # with your privileges, in one transaction
   ```

   Without the steward, call `morlet_start_memory_run`, `morlet_set_level` (first with `dry_run => true`) and
   `morlet_finish_memory_run` yourself ([memory design](memory-design.md#moving-episodes-one-primitive); not
   exercised here).
4. **Space:** the removed rows become free space inside the tables after `VACUUM` (autovacuum does it). The files do
   not shrink, so the disk's free space does not grow; new samples reuse the space. After many removals, rebuild the
   probe index ([7](#7-rebuilding-envelopes-probe-rows-and-indexes)).

**Deleting whole recordings** (`DELETE FROM line WHERE recording_id = …`) also removes their chunks, envelopes and
probe rows, but leaves no trace: `morlet_coverage` no longer lists the recording, so a later search cannot tell
"removed" from "never recorded". Prefer level 4. If you delete, keep the export and its checksums as your record.

**Restoring archived data:** import the exchange file into another collection (step 2). Restoring into the same
recording after level 4 is not supported: the importer refuses it, and nothing changes (#204).

**Do not** remove samples whose copy was not verified, skip the second person's approval, or `TRUNCATE` or drop
collection tables.

**Verify afterwards**

```sql
SELECT recording_id, start_sample, end_sample, state FROM morlet_coverage('public.line', ARRAY['p1', 'p2']);  -- p1: gone
SELECT ledger_id, episode_id, recording_id, to_level, chunks_removed, bytes_freed, removed_md5, reason
  FROM line_ledger ORDER BY ledger_id DESC;
SELECT recording_id, chunks, samples, problems FROM morlet_verify('public.line') WHERE problems <> '{}';  -- no rows
```

A read of a removed interval fails with `samples of recording p1 in 0..65536 were compacted to level 4 (episode 1,
ledger 1)`.

**Fixed problem (found while writing this runbook, 0.3.14-alpha.1; #488, fixed in SQL 0.3.12):** after level 4,
`morlet_build_envelopes` on that recording kept returning the number of skipped windows and wrote nothing, so the
steward's envelope job picked the same recording on every tick and never reached the recordings after it, and
`morlet_collection_status` counted the removed samples as `unbuilt_samples`. From SQL 0.3.12 the builder returns 0
there and the status counts only samples at level 0; `morlet_coverage` still reports the removed range as `gone`. A
steward from the same release also moves on when the database is still at SQL 0.3.11. On 0.3.14-alpha.1 itself (SQL
0.3.11 and its steward), watch `morlet_collection_unbuilt_samples` after a retention plan and build the other
recordings with the loop in [7](#7-rebuilding-envelopes-probe-rows-and-indexes).

**How this was checked.** On the collection of runbook 7, with a local SeaweedFS 3.73 S3 server (generated keys) and
boto3 1.35.36:

- Two recordings were exported and verified, then imported into a scratch collection and verified identical.
- One recording was exported to S3. A second run skipped the upload (same content). The downloaded object verified
  identical.
- A level-4 plan was proposed (dry run: 57,066 bytes freed). The proposer's own approval was refused; a second role
  with `morlet_approver` approved it, and it was applied. Afterwards: coverage `gone`, a ledger row with the copy in
  `reason`, `morlet_verify` without problems, and reads refused with the level-4 error.
- Re-importing the exchange file into the compacted recording was refused, with nothing changed. A `DELETE` of a
  whole recording removed its chunks and envelopes, and `morlet_coverage` then returned no rows for it.
- Amazon S3 and other services, versioned buckets, and `morlet_set_level` called directly: not exercised.

## 9. Upgrading the extension and the image

A release brings a new library and, usually, a new SQL version. The new library runs with the old SQL objects until
`ALTER EXTENSION pg_morlet UPDATE`, which is transactional: it applies completely or not at all (tested in CI from
every released SQL version; [operations](operations.md#upgrades)). The release notes ([CHANGELOG](../CHANGELOG.md),
[releases](releases.md)) say what each update changes.

**Read before upgrading to 0.3.14-alpha.1 or later:** chunk format version 6 is written by default for float32
samples. **pg_morlet 0.3.13-alpha.1 and earlier cannot read version-6 chunks.** After a downgrade, `morlet_verify`
reports them as `chunk does not decode: corrupt encoded chunk: unknown format version` and reads of them fail; the
data is not corrupt, the old reader does not know the format. If you may need to go back, see "Keeping a way back"
below.

**Symptoms** (an upgrade is due)

- `steward.py status`: `plan N (an extension update) waits for a decision`; the steward proposes it when the server
  has a newer pg_morlet than the database.
- `SELECT default_version, installed_version FROM pg_available_extensions WHERE name = 'pg_morlet';` shows two
  different versions.

**Check**

```sql
SELECT extversion FROM pg_extension WHERE extname = 'pg_morlet';     -- in every database that uses it
SELECT default_version, installed_version FROM pg_available_extensions WHERE name = 'pg_morlet';
SELECT recording_id, chunks, samples, problems FROM morlet_verify('public.line') WHERE problems <> '{}';
SELECT get_byte(encoded, 0) AS chunk_version, count(*) FROM line_chunks GROUP BY 1;   -- the formats stored now
```

The first byte of a stored chunk is its format version ([chunk format](chunk-format.md)).

**Act**

1. **Take a backup** you can restore on the old release: `pg_dump -Fc` of each database ([operations](operations.md#backup-and-restore)),
   or a base backup ([1](#1-backup-with-wal-archiving)).
2. **Stop the writers**, or set the ingest hold ([5](#5-disk-full-or-nearly-full)), and stop the steward.
3. **Replace the image**, keeping the data volume:

   ```sh
   docker stop morletdb && docker rm morletdb
   docker run -d --name morletdb -v morlet-data:/var/lib/postgresql/data ... ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1
   ```

   With the edge stack, set `MORLET_IMAGE` to the new tag and run `docker compose -f deploy/steward/compose.yaml up -d`
   (not exercised). With Debian packages, install the new package and restart PostgreSQL (not exercised here; the
   release trials packages).
4. **Update the SQL objects** in every database that has the extension:

   ```sql
   ALTER EXTENSION pg_morlet UPDATE;
   SELECT extversion FROM pg_extension WHERE extname = 'pg_morlet';
   ```

   With the steward, approve and apply its plan instead: `steward.py plans`, then `steward.py approve N` (another
   person, with `morlet_approver`) and `steward.py apply N`. Then run `steward.py init` again (after updating the
   steward) and `steward.py grant`.
5. **pgvector** is a separate extension with its own `ALTER EXTENSION vector UPDATE`.
6. **Start the writers** and the steward again, or release the hold.

**Keeping a way back** (for 0.3.14-alpha.1 and later)

- **Before any new data:** to stay readable by 0.3.13-alpha.1 for a while, turn version 6 off right after the update:
  `ALTER DATABASE morlet SET morlet.float32_chunks = off;` (it applies to new sessions). Turn it on again with
  `ALTER DATABASE morlet RESET morlet.float32_chunks;` when you no longer need the way back. Existing chunks keep their
  format either way.
- **Going back:** there are no downgrade scripts. Start the old image with a new, empty data volume and restore the
  backup from step 1 (`pg_restore`). Data written after the upgrade is not in it. To carry it over, export those
  recordings with the new release (`morlet_export.py`, which reads every version) and import them into the old one
  (`morlet_import.py`); the old release writes them in its own formats. Context, labels and search data are not in
  the exchange file.
- **Do not** start an old image on a data directory that the new release has updated: the catalog says the new SQL
  version, and the old library cannot read version-6 chunks.

**Verify afterwards**

- `extversion` is the release's SQL version (0.3.11 for 0.3.14-alpha.1) in every database.
- Run [Check after every restore or failover](#check-after-every-restore-or-failover). Read a few recordings and
  compare them with what you read before the update (for example `sha256(morlet_read_interval_raw(...))`).
- `steward.py status` shows no waiting extension plan.

**How this was checked.**

- **Upgrade:** a database on `0.3.13-alpha.1` (SQL 0.3.10) with a float32-valued recording and envelopes was dumped,
  and its container replaced by `0.3.14-alpha.1` on the same volume. Before the update the old objects still read
  the recording; `ALTER EXTENSION pg_morlet UPDATE` moved it to 0.3.11. The recording's SHA-256 was the same before,
  between and after, and `morlet_verify` found nothing.
- **The setting:** with `morlet.float32_chunks = off` at database level, a new float32 recording was stored as
  version 2 (234,793 bytes); after `RESET`, as version 6 (86,810 bytes).
- **Downgrade:** `0.3.13-alpha.1` started on the updated volume read the version-2 chunks, and `morlet_verify` reported
  the version-6 chunk as above. The pre-upgrade dump restored into a new database on `0.3.13-alpha.1` verified
  identical. The version-6 recording, exported by 0.3.14-alpha.1 and imported there, was verified identical and stored
  as version 2.
- **Steward plan:** on a database still at SQL 0.3.10 under the new image, the steward proposed the update on its
  first tick. A second role approved it, and `steward.py apply` updated the extension to 0.3.11.

## 10. First hour: triage

1. **Is the server up?** `pg_isready -h <host> -U morlet -d morlet`; metric `morlet_up`. If not, read the PostgreSQL
   log. `No space left on device`: [5](#5-disk-full-or-nearly-full). It answers but refuses writes:
   `SELECT pg_is_in_recovery();` returns true on a replica and during a restore
   ([2](#2-restore-to-the-latest-state-or-to-a-point-in-time), [4](#4-failover)).
2. **What does the steward say?** `steward.py status` (each item has its cause, consequence, action and who), or the
   metrics `morlet_steward_state`, `morlet_steward_heartbeat_age_seconds` and `morlet_ingest_hold`.
3. **Disk:** `df -h` on the data directory. Below the reserve or falling fast: [5](#5-disk-full-or-nearly-full). The
   WAL is a large share: [6](#6-wal-growth).
4. **Writers refused?** SQLSTATE 53100 with `new samples are refused`: the ingest hold ([5](#5-disk-full-or-nearly-full)).
   With `No space left on device`: [5](#5-disk-full-or-nearly-full). With a conflicting or gapped append, the writer
   sent different samples for a stored range, or skipped samples. That is a writer problem; MorletDB refuses it on
   purpose ([durable ingestion](durable-ingestion.md)).
5. **Search finds nothing, or less than before:** `morlet_collection_status` and `morlet_coverage`
   ([7](#7-rebuilding-envelopes-probe-rows-and-indexes)). A negative result counts only for what was searched.
6. **Integrity:** `morlet_verify` ([operations](operations.md#integrity-scrub-sql-035)) and the metric
   `morlet_verify_last_pass_problems`. A problem in a chunk is damage to raw data: change nothing, keep a copy of the
   data directory, and recover the recording from a backup restored on a separate server
   ([2](#2-restore-to-the-latest-state-or-to-a-point-in-time); not exercised for a single recording). Problems in
   derived data are rebuilt ([7](#7-rebuilding-envelopes-probe-rows-and-indexes)).
7. **Old data has to go, or the disk will be full in days:** [8](#8-retention-and-archiving-to-parquet-and-s3).
8. **An update waits, or a new release is out:** [9](#9-upgrading-the-extension-and-the-image).
9. **The primary is lost:** [4](#4-failover), after making sure it stays down.

Before you change anything, write down the time, what you saw (the outputs above) and what you are about to do. The
steward's `morlet_steward.actions` keeps its own record; yours goes next to it.
