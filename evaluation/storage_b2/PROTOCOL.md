# B2: ClickHouse and TileDB storage baselines on B1's samples — protocol (FROZEN)

Frozen 2026-11-02 in the commit that adds this file, before any B2 size was computed. No B2 tool had been
installed when it was written. Deviations are appended below.

## Question

B1 compared pg_morlet's raw codec with zstd, Parquet, DuckDB (ALP) and FLAC. Two storage engines that
practitioners use for signal data were missing: ClickHouse with a tuned codec chain, and TileDB with a tuned
filter pipeline. On B1's identical samples:

1. On **calibrated floats** (Paderborn physical, Sleep-EDF), is pg_morlet with the nominal quantum still
   smaller than every metadata-free float arm once ClickHouse and TileDB are added?
2. How far is pg_morlet from the best **integer** arm once ClickHouse and TileDB integer codecs are added?

## Inputs

B1's six inputs, unchanged (`evaluation/storage_baselines/PROTOCOL.md`): cx, ims, pad-c, pad-p, sleep, ecg,
about 4.19 M samples each, from `data/b1-runs/{inputs,p2}` (B1's `measure.py` loader).

## Tools (pinned)

- ClickHouse through chDB: `chdb` 4.4.0 with `chdb-core` 26.9.0 (in-process ClickHouse 26.9).
- TileDB: `tiledb` (TileDB-Py) 0.36.1; the embedded TileDB core version is recorded.
- Installed into a separate virtual environment (`.venv-b2`); versions are written to the results.

## Arms (fixed now; each verified lossless by reading back and comparing bit patterns or integers)

Each engine gets a pre-declared set of codec chains and is credited with the **smallest** member per input
("tuned": the best of the set, chosen after measuring, which favours the baselines). All members are
reported.

**ClickHouse** (MergeTree, `ORDER BY i` on a separate UInt32 row-number column, one insert, `OPTIMIZE TABLE
... FINAL`, one part, default compression block and granule settings). Size: the value column's
`column_data_compressed_bytes` from `system.parts_columns` (its `.bin` data; marks and the row-number column
are not counted, which favours ClickHouse).
- **CH-f** (Float64 column): `ZSTD(19)`; `Gorilla, ZSTD(19)`; `FPC, ZSTD(19)`; `Delta(8), ZSTD(19)`.
- **CH-i** (Int64 column, exact counts): `ZSTD(19)`; `Delta, ZSTD(19)`; `DoubleDelta, ZSTD(19)`;
  `T64, ZSTD(19)`; `Gorilla, ZSTD(19)`.

**TileDB** (dense 1-D array, domain 0..n−1, space tile 4,096 cells, one write, consolidated and vacuumed).
Size: the bytes of the attribute's data file(s) in the remaining fragment (metadata and offsets are not
counted, which favours TileDB).
- **TDB-f** (float64 attribute): `Zstd(19)`; `ByteShuffle, Zstd(19)`; `BitShuffle, Zstd(19)`;
  `XOR, Zstd(19)` (if the installed version provides the XOR filter, else reported as unavailable).
- **TDB-i** (int64 attribute): `Zstd(19)`; `Delta, Zstd(19)`; `DoubleDelta, Zstd(19)`;
  `BitWidthReduction, Zstd(19)`; `ByteShuffle, Zstd(19)`.

**Carried over from B1** (`evaluation/storage_baselines/results/b1.json`, deterministic sizes): Z, PQ-f, ALP,
FLAC, PQ-i, DDB-i, M and M-i. **Consistency check:** pg_morlet (M) is re-measured on the current `main` and
must equal B1's bytes on every input; a difference is reported and the re-measured value used.

## Comparisons (decided now)

1. **Calibrated floats (pad-p, sleep):** M / min(Z, PQ-f, ALP, CH-f, TDB-f). "pg_morlet with a quantum is
   smaller than every tested metadata-free float storage" only if < 1 on both inputs; the ratios are
   reported either way, together with M / CH-f and M / TDB-f.
2. **Integer ceiling (all six):** M / min(FLAC, PQ-i, DDB-i, CH-i, TDB-i), reported per input; B1's
   threshold (≤ 1.15) is reapplied with the larger baseline set.
3. **Decimal floats (ims, ecg):** M / CH-f and M / TDB-f reported, no claim.

## Reported, deciding nothing

Every chain's bits/sample per input; which chain won; total on-disk bytes of the ClickHouse part and TileDB
fragment (including what the primary measure excludes); write and read times (single run, descriptive).

## Not in B2

No database-level comparison of pg_morlet tables, no lossy coding, no other chains after the first run, no
tuning of compression levels or block sizes beyond the sets above. If an arm fails to run, the failure is
reported, not fixed by changing other arms.

## Deviations

(none yet)
