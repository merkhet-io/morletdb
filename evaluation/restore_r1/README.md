# R1: restore drill (#192, restore part)

`drill.py` exercises the backup path an operator would use, end to end, with Docker:

1. A database from the release image (data checksums, `archive_mode=on`, WAL archived to a volume) gets 120 batches
   of deterministic samples with one context row each (`evaluation/power_cut_p1/writer.py`), then envelopes.
2. `pg_basebackup` (plain format, WAL streamed).
3. 40 more batches; the time T is read from the database; 40 more batches; every WAL segment archived.
4. **The disaster:** the database container and its data volume are removed.
5. **Restore to the latest state:** a new data volume from the base backup, `restore_command` from the archive. It
   must hold all 200 batches.
6. **Restore to T** (`recovery_target_time`): it must hold exactly the 160 batches written before T, and none after.

Each restore is checked: recording end, every sample against the generator, interval statistics, one context row per
batch, envelopes not beyond the end, `morlet_verify` without problems, the extension version.

```sh
pip install "psycopg[binary]==3.2.10" "numpy==2.2.6"
python evaluation/restore_r1/drill.py --image ghcr.io/merkhet-io/morletdb:<version> --out result.json
```

CI (`.github/workflows/restore-drill.yml`) runs it weekly and when it changes, against the current release on amd64
and arm64, and keeps the JSON result.

## Result

2026-11-08, release 0.3.9-alpha.1 (linux/arm64, Docker on a laptop): **pass.** Both restores hold exactly the expected
batches (200 and 160), bit for bit, with no problem from `morlet_verify`; each restore took about 2.5 s at this size.

## Not covered

Replication and failover (the other half of #192), restores of large databases (time), backups to another machine.
