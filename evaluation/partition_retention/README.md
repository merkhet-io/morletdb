# Removing a month of chunks while appends go on (#211)

**Development measurement, 2026-11-20.** Not a frozen protocol and not a benchmark: two rounds on a Raspberry Pi,
plus one smoke round on a desktop. It is the first step proposed in
[#211](https://github.com/merkhet-io/morletdb/issues/211): before changing MorletDB's schema, does removing old data
by dropping a time partition behave as well on an edge box as expected, compared with what MorletDB can do today?

## Question

A collection's chunks hold two "months" (part 1, the oldest, and part 2) of real encoded chunks. While a writer keeps
appending chunks into part 3, remove part 1 in one of three ways:

| Arm | What it runs | Available |
|---|---|---|
| `delete_vacuum` | `DELETE` of part 1, `VACUUM` | today, any version (retention, memory level 4) |
| `delete_vacuum_repack` | the same, then `REPACK (CONCURRENTLY)` | PostgreSQL 19 (#535, runbook 8) |
| `detach_drop` | a table partitioned by part: `DETACH PARTITION ... CONCURRENTLY`, `DROP TABLE` | needs time-partitioned chunk tables (#211) |

Measured: how long the removal takes, how much disk space comes back to the filesystem, the WAL it writes, how long
appends wait, and what an older `REPEATABLE READ` snapshot sees afterwards.

## Setup

- **Machine:** Raspberry Pi 3B+ (4 × Cortex-A53, 1 GB RAM, SD card), Debian 11, Docker. Official `postgres:19.0`
  image with pg_morlet SQL 0.3.13 (`evaluation/pg19_features/Dockerfile.same`). `shared_buffers = 128MB`,
  `max_wal_size = 1GB`, data checksums on (the default since 18). Nothing else ran on the Pi.
- **Data:** the B3 Paderborn subset of the PostgreSQL 19 measurements (every 40th acquisition, 434 recordings,
  50,853,629 samples), loaded once with pg_morlet: 1,748 encoded chunks, 72.8 MB. Each arm copies these chunks into a
  fresh table of its own as part 1 and again as part 2 (about 69 MB each).
- **Tables:** the chunk table's columns (`recording_id`, `start_sample`, `sample_count`, `encoded`) plus `part`, with
  `encoded` pinned to pglz as in pg_morlet. These are plain tables, not pg_morlet collections: the comparison is
  between storage operations.
- **Writer:** one connection inserts copies of real encoded chunks into part 3 at 10 chunks per second (about
  0.45 MB/s), one transaction each, during and around the removal, and logs every insert.
- **Two passes per arm and round:**
  - a **space pass** with no other reader;
  - a **snapshot pass**, in which a `REPEATABLE READ` transaction takes its snapshot before the removal (reading only
    `pg_class`) and counts parts 1 and 2 ten seconds after the removal started.

  They are separate because an open older snapshot changes the removal itself: `VACUUM` cannot reclaim rows it may
  still see.
- **Order:** the arms alternate between rounds (A, B, C, then C, B, A).
- **Script:** `retention_check.py`; results in `results/pi_retention.json` and `results/mac_retention.json` (the
  desktop smoke round, Apple M5 Max).

## Results (Raspberry Pi, two rounds, both passes)

Ranges over the four runs of each arm (two rounds × two passes):

| | `delete_vacuum` | `delete_vacuum_repack` | `detach_drop` |
|---|---|---|---|
| Removal took | 17–33 s (the `DELETE`: 16–31 s) | 44–59 s (`REPACK`: 33–46 s) | **0.17–0.30 s** |
| Disk space returned to the filesystem (after `CHECKPOINT`, net of what the writer added meanwhile) | **none**: free space fell 4–8 MB while the writer wrote 3–11 MB | 50–59 MB (+8–17 MB written) | 61–65 MB (+3–6 MB written) |
| Chunk tables' size | grew 3–8 MB | −53 to −63 MB | −65 to −69 MB (the whole part) |
| WAL written | 80–89 MB | 165–180 MB | **3.6–8.0 MB** |
| Longest wait for one append | 1.7–10.5 s | 5.3–8.4 s | **18–72 ms** |
| Median append during the removal (14–19 ms before) | 83–427 ms | 58–230 ms | 16–18 ms |
| Part 2 unchanged (recording, start and encoded bytes of every chunk), every append stored | yes | yes | yes |

- **Dropping a partition** gave the whole month back to the filesystem in a quarter of a second, wrote a tenth of
  the WAL of a `DELETE` or less, and appends did not notice it (median 16–18 ms, as before).
- **A `DELETE` of a month is itself the problem on an SD card.** It took 16–31 s, appends waited up to 10.5 s
  (median during the removal 83–427 ms), it wrote 80–89 MB of WAL, and it gave nothing back to the disk. That is what
  retention and memory-level-4 moves do today, on every PostgreSQL version.
- **`REPACK (CONCURRENTLY)`** gives the space back, but only after the `DELETE`, and it doubled the time and the WAL;
  appends waited up to 8.4 s.

**Older snapshots.** After `detach_drop`, a transaction whose snapshot predates the removal saw none of part 1 and
all 1,748 chunks of part 2 (four runs on the Pi, one on the desktop): what was dropped is gone for it, what was kept
is intact. After `DELETE` it still saw both parts (MVCC). `DETACH ... CONCURRENTLY` did not wait for that transaction
(0.03–0.10 s): it waits only for transactions that have used the partitioned table, and this one had read only
`pg_class`. For `REPACK` the Pi runs say nothing about snapshots: the reader counted 10 s after the removal began,
before `REPACK` started, so it saw both parts. On the desktop, where the whole removal took 0.5 s, the reader saw
**neither** part afterwards, the hazard reproduced in the PostgreSQL 19 measurements (#550).

## What this means for #211

- Removing old data by dropping a time partition is the right direction for edge boxes: on the Pi it was about 100
  times faster than a `DELETE`, gave the space back at once, wrote a tenth of the WAL or less, and did not hold up
  appends.
  It works on PostgreSQL 17 and 18 too (`DETACH ... CONCURRENTLY` exists since 14); `REPACK` is not needed for it.
- It also changes the snapshot story in our favour: an old snapshot loses only the data that was removed, never the
  data that was kept (unlike `REPACK`).
- Today's retention path (row `DELETE`) is costly on an SD card, independent of space: multi-second append stalls
  while it runs. Batching deletes into smaller transactions would soften the stalls but not the WAL or the space.
- Still open (#211): how memory levels fit (survivors of a partition, moved to a kept partition), row security on
  partitions, and the schema change with its upgrade path. This check does not test any of those.

## Limits

- Two rounds on one Pi and one SD card; ranges, not statistics. The desktop round is a smoke test (its filesystem
  numbers are noise from other activity on a shared disk).
- One table size (69 MB per month) and one append rate (10 chunks/s). Larger months make `DELETE` and `REPACK`
  slower; a partition drop's cost does not grow with the data.
- Plain tables with the chunk table's columns, no pg_morlet triggers, foreign keys, envelopes or probe tables.
- `DETACH ... CONCURRENTLY` was not tested against a reader that had used the partitioned table (it would wait for
  it), nor its failure and retry path.

## Reproduce

```sh
python evaluation/pg19_features/prepare_data.py --out /tmp/b3-40 --every 40
python evaluation/partition_retention/retention_check.py --data /tmp/b3-40 \
    --dsn "host=... user=postgres dbname=postgres" --fs-path / --rounds 2 --rate 10 --out pi_retention.json
```

It needs PostgreSQL 19 with pg_morlet, and works only in the schema `morlet_retention_check`, which it creates and
drops (and refuses to drop if it did not create it or if anything outside it depends on it).

After the runs above, from review: the part-2 check also compares `sample_count`, a run counts as `ok` only if the
removal raised no error, left nothing of part 1, and the writer stored at least one append, and `--rate` must be
positive. The recorded runs had no removal error, no part-1 rows left, and 83 to 471 appends each.
