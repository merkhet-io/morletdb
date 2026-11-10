# CedarDB v2026-11-04: a table scan with a time range returned one wrong row

Found by B3's read-back check in round 3 of Amendment B3.19 (2026-11-10, 01:39), diagnosed the same day on the preserved
database. This note describes the fault on its own; the benchmark context is in [RESULTS.md](RESULTS.md) ("Amendment
B3.19"). Everything here is observed behaviour of a closed-source system; where in CedarDB the fault arises is not known.

## Summary

On a database whose stored data is correct, every query that CedarDB executes as a **table scan with a range predicate on
the time column** returns one wrong row for one acquisition: row 1023 is replaced by a row with an invalid time and the
values of row 1020. Index scans over the same range, point lookups, and a table scan without a time predicate return the
correct row. On the preserved database the wrong result is deterministic for a given plan. It was seen once: 1 of 34
fresh loads of the same data with the same image (11 in the benchmark sessions, 23 in a reproduction run) failed the
check, and a fresh load has not reproduced it.

## Environment

- CedarDB v2026-11-04, `cedardb/cedardb@sha256:737ce1bf2ef3c9f2ba7e599ae4b2a0ae0399125e3f63a417a51141976a40518e`
  (arm64), Community Edition, default settings. Sessions set `async_commit = on`; `synchronous_commit` and `fsync`
  report `on`.
- Docker Desktop (engine 29.1.3, linux/arm64, LinuxKit 6.12.54) on macOS 27.0.1, Apple M5 Max. Container limits 8 CPUs
  and 16 GB; data directory on a Docker volume.
- Client: Python 3.13, psycopg 3.2.10, loopback TCP.

## Schema and load

```sql
CREATE TABLE b3_high (
  acquisition TEXT NOT NULL, t BIGINT NOT NULL,
  vibration_1 DOUBLE PRECISION NOT NULL, phase_current_1 DOUBLE PRECISION NOT NULL,
  phase_current_2 DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (acquisition, t));
```

124 acquisitions of the public Paderborn bearing dataset (Lessmeier et al. 2016, KAt; CC BY-NC 4.0), loaded one after
another on one connection, one `COPY b3_high (...) FROM STDIN (FORMAT BINARY)` of 256,001 rows per acquisition
(31,877,792 rows in all, in time order within each acquisition). Times are integers in nanoseconds on a 64 kHz grid:
`t = start + i × 15625`. Two smaller tables were loaded the same way and are not affected. Loader:
[`cedar_adapter.py`](cedar_adapter.py), `load_one`.

The affected acquisition is `N15_M07_F04_KA06_1`, the 43rd loaded, `start = 1767228120000000000`.

## What is wrong

| | t | vibration_1 | phase_current_1 | phase_current_2 |
|---|---|---|---|---|
| Row 1023 as written (and as returned by a point lookup) | 1767228120015984375 | 0.1647949217660928 | −2.233030394032081 | −0.1480880736326025 |
| Row returned in its place by the faulty plans | **1767228120197362652** | 0.0122070312419328 | −2.3280822738520768 | −0.10125091546043055 |
| Row 1020 as written | 1767228120015937500 | 0.0122070312419328 | −2.3280822738520768 | −0.10125091546043055 |

- The returned time is not on the grid: it lies 12,631.21 sample periods after the start, about 2.8 s later than row
  1023. No point lookup finds a row at that time, in any acquisition.
- The returned values are exactly row 1020's, bit for bit. No other row of the dataset has these three values. Row 1020
  is also returned correctly, at its own time, in the same result.
- The row count is right (256,001), and the other 255,999 rows, with their times and values, are exact. Sorted by time,
  the stray row sits between rows 12,631 and 12,632, which shifts rows 1023–12,631 by one position.

## Which queries are affected

All queries run in one server session on a byte-identical copy of the round-3 data directory
([`diag_cedar_r3i.py`](diag_cedar_r3i.py), output in
[`results/b319-cedardb-s3/diagnosis/9_plans_and_results.json`](results/b319-cedardb-s3/diagnosis/9_plans_and_results.json)).
`R` is `acquisition = 'N15_M07_F04_KA06_1' AND t >= 1767228120000000000 AND t < 1767228124000015625` (the
acquisition's full range).

| Query | Plan (`EXPLAIN`) | Result |
|---|---|---|
| `SELECT t, vibration_1, phase_current_1, phase_current_2 FROM b3_high WHERE R ORDER BY t` | SORT ← TABLESCAN | **wrong** |
| `SELECT t, vibration_1 FROM b3_high WHERE R ORDER BY t` | SORT ← TABLESCAN | **wrong** |
| `SELECT t, vibration_1 FROM b3_high WHERE R` | TABLESCAN | **wrong** |
| `SELECT t, vibration_1 FROM b3_high WHERE t >= … AND t < …` (no acquisition filter) | TABLESCAN | **wrong** |
| `SELECT t FROM b3_high WHERE R` and `… ORDER BY t` | INDEXSCAN b3_high_pkey | correct |
| `SELECT count(*) FILTER (WHERE t = <row 1023>), count(*) FILTER (WHERE t = <stray>) FROM b3_high WHERE R` | GROUP BY ← INDEXSCAN | correct (1, 0) |
| `SELECT t, vibration_1 FROM b3_high WHERE acquisition = 'N15_M07_F04_KA06_1'` (no time predicate) | TABLESCAN | correct |
| `SELECT * FROM b3_high WHERE acquisition = '…' AND t = 1767228120015984375` | INDEXSCAN | correct |

The planner's choice between the two scans depends on the selected columns and can differ between sessions. In an
earlier session the three-aggregate count `count(*) FILTER (WHERE t = <row 1023>), count(*) FILTER (WHERE t =
<stray>), count(*)` over `R` returned (0, 1, 256001), computed entirely in the server; in the session above it ran as
an index scan and returned the correct (1, 0, …). Diagnosis steps 6 and 7, which did not record the plan of each
query, are superseded by step 9.

For the table-scan plans the wrong result is deterministic: identical in 5 of 5 repeats within a session
(`8_repeats.json`), across restarts, in binary and text result format, and on the copy as in the benchmark run itself.

## How the database was preserved

The benchmark runner removed the container but not its volume (created at the start of round 3). The volume was copied
byte for byte (131 files, 4,332,810,435 bytes, identical checksums), and all diagnosis ran on the copy; the original is
kept unchanged. Because the run's password had not been stored, a new password was set on the copy with CedarDB's
offline shell (`cedardb -i`, `ALTER USER`); no other statement was run in that shell.

## Reproduction from scratch

Not reproduced: 23 further fresh loads of the same data with the same image, adapter and read-back check, run
2026-11-10 13:04–13:28 ([`run_b319_repro.sh`](run_b319_repro.sh), `results/b319-repro/`), all passed, as did 10 of
the 11 loads in the benchmark sessions. The check reads every acquisition with the benchmark's query (the first row
of the table above), so a recurrence would have been caught. Round 3 ran after a MorletDB load at the end of a
long benchmark session, with a busier machine (median load average 5.0 against 2–4 for the reproduction); whether that
matters is not known.

## Open questions

- Is the fault in how the time column's block was written during the load (so that only the table scan's filtered
  path decodes it wrongly), or in the filtered scan itself? The preserved data directory would let CedarDB answer
  this; it holds only the public dataset and can be shared.
- Does it occur on other versions?

## Files

Diagnosis scripts `diag_cedar_r3.py` and `diag_cedar_r3b.py` … `diag_cedar_r3i.py`; outputs 1–9 in
`results/b319-cedardb-s3/diagnosis/`; the failing load's record in `results/b319-cedardb-s3/load_cedardb_D2_r1.json`.
