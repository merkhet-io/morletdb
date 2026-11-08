# Runbooks: backup, point-in-time restore, replica and failover

MorletDB is a PostgreSQL extension, so these are PostgreSQL's own procedures. Nothing here is specific to MorletDB
except the check at the end of each one. Each runbook is executed step by step by a drill in CI
(`evaluation/restore_r1/drill.py`, `evaluation/replica_r2/drill.py`, weekly on amd64 and arm64). If a step here and a
drill disagree, that is a bug; please report it.

Names used below: the database `morlet`, the user `morlet`, a collection `line`. Replace them with your own.

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
