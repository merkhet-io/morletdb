# Study B7: MorletDB 0.3.13-alpha.1 vs ModelarDB-RS (1b90706): results

**Unpublished draft: not to be pushed or cited until the owner approves.**

Run 2026-11-13 22:54 to 2026-11-14 02:04 under the protocol frozen in `dd756e6` (revision of 2026-11-13, written
before any load of the benchmark set). Runner: `run_all.sh` (`18f2cb6`). Stress vector once, then three rounds. All
runs exited 0, inside the 5-hour budget. Benchmark set: 117,749,610 float32 samples in three groups:

| Group | Samples | Units × channels |
|---|---:|---|
| force | 107,495,250 | 3 channels |
| ctrl | 4,301,616 | 6 channels |
| pmid | 5,952,744 | 3 channels |

Machine: one-minute load average 2.4–3.2 at the starts of the rounds. Docker's VM still ran a langfuse stack and
three idle PostgreSQL containers, recorded in `results/machine/state.txt`.

## Correctness

| | MorletDB m-g1 | MorletDB m-g21 | ModelarDB mdb-l (lossless) |
|---|---|---|---|
| force, full read-back | exact, all series, 3 rounds | exact | **1 series not bit-exact, in all 3 rounds** (see below) |
| ctrl, full read-back | exact | exact | exact |
| pmid, full read-back | exact | exact | exact |
| 240 interval reads per round | all exact | — | all exact |
| Column min, max, count | equal to ModelarDB's, every channel and round | — | |

- **Sums** differ in the last digits, for example 9787439227.230589 vs …587. That is float64 summation order, not
  data.
- **Stress vector:** ModelarDB returned 510 of 510 values; 509 were bit-identical. NaN payloads, ±inf and
  subnormals were kept, but **-0.0 inside a run of 0.0 came back as +0.0**. MorletDB received the 504 finite values
  (6 non-finite values left out by design) and returned them all bit-identical.
- **The force series ModelarDB changes:** unit M2T4R785, channel force_sensor_z.
  - Found by a diagnostic load after the run (not a measurement; script `results/diag_force_series.py`).
    The same series fails in all three rounds.
  - **Two values come back 1 ulp off and not value-equal:**
    - sample 125: 0xbfa5b2a1 → 0xbfa5b2a2 (-1.2945138 → -1.2945139);
    - sample 126: 0xbfa5b387 → 0xbfa5b388.
  - The force input contains no -0.0, so this is not the signed-zero case. It is data change in the
    lossless mode, on 2 of 107.5 M force values.
  - The likely cause is the one found for the lossy arms: residuals decoded against a recomputed model value that
    can differ from the encoder's by an ulp. This is not confirmed in the code for this case.
  - **Decision rule:** mdb-l does not count as lossless for force.
- **Lossy arms violate their bounds** in every round, with the same counts each time:

| Arm | force values outside the bound | ctrl | pmid | largest absolute error (force) |
|---|---:|---:|---:|---:|
| mdb-r1, `FIELD(1%)` | 402,824 of 107.5 M (0.37 %) | 143 | 1,122 | 8.6e4 |
| mdb-r5, `FIELD(5%)` | 936,120 of 107.5 M (0.87 %) | 415 | 89 | 3.4e38 |

As the protocol says, the lossy arms' storage is not valid lossy compression while values fall outside the bound.
It is shown below for reference only.

## Storage

Measured after the settled state, with the server stopped. MorletDB counts the chunk table with TOAST and indexes,
plus the recordings table; ModelarDB counts Parquet plus `_delta_log`. Storage was the same across loads: identical
bytes for ModelarDB, within 0.01 % for MorletDB.

| Group | MorletDB m-g1 | B/sample | ModelarDB mdb-l | B/sample | Smaller lossless store |
|---|---:|---:|---:|---:|---|
| force | 559.3 MB | 5.20 | 392.3 MB | 3.65 | none by the rule (mdb-l not lossless on force); ModelarDB 1.43× smaller |
| ctrl | 21.1 MB | 4.91 | 14.1 MB | 3.27 | ModelarDB, 1.50× |
| pmid | 9.2 MB | 1.54 | 13.1 MB | 2.19 | MorletDB, 1.42× |

- **Raw sizes for reference:** float32 is 4 B/sample, float64 8 B/sample.
- **MorletDB stores the float32 force and ctrl data above float32 size.** It upcasts to float64, and the codec
  finds no value grid in these channels. Its encoded chunk bytes are 6.59 B/sample (force) and 6.51 (ctrl); PostgreSQL's
  TOAST pglz then gets force down to 5.20. This is the gap described in #326, and it is MorletDB's main loss in B7.
- **m-g21 (Grizzbit 2.1)** is within 1 % of m-g1 on force, and 3 % / 9 % smaller on ctrl / pmid.
- **Whole volume:** MorletDB 1,225 MB, of which `pg_wal` is 604 MB. ModelarDB 427 MB, of which WAL is 8 MB.
- **Transfer volume** (what an edge would ship): MorletDB's encoded chunks are 708.5 MB force, 28.0 MB ctrl and
  7.5 MB pmid. ModelarDB's Parquet is 392.3, 14.1 and 13.0 MB.
- **Lossy arms, for reference (bounds violated):** mdb-r1 1.50 / 1.44 / 0.67 B/sample; mdb-r5 1.41 / 1.19 / 0.45.

## Ingestion

The whole load from float32 arrays in memory, as the median over the loads of an arm, per round:

| | Round 1 | Round 2 | Round 3 |
|---|---:|---:|---:|
| MorletDB m-g1 | 26.4 s (4.46 M samples/s) | 27.3 s (4.32 M/s) | 28.4 s (4.15 M/s) |
| ModelarDB mdb-l | 12.5 s (9.42 M/s) + flush 0.82 s | 12.8 s (9.20 M/s) + 0.82 s | 12.1 s (9.73 M/s) + 0.83 s |

**Decision rule:** ModelarDB is faster, lower by at least 10 % in every round: about 2.1×, or 2.0× including its
flush.

- **Durability differs.** MorletDB acknowledges durable commits; ModelarDB returns once its WAL holds the data (its
  default). This is stated, not corrected for.
- **Per group** (M samples/s, rounds 1–3):

| Group | MorletDB | ModelarDB | Faster |
|---|---|---|---|
| force | 4.41 / 4.26 / 4.09 | 9.88 / 9.64 / 10.23 | ModelarDB |
| ctrl | 2.82 / 2.79 / 2.74 | 7.04 / 6.86 / 6.89 | ModelarDB |
| pmid | 14.7 / 14.3 / 14.1 | 5.91 / 5.82 / 6.11 | MorletDB, about 2.4× |

- **MorletDB's settle step** (`CHECKPOINT`, `VACUUM (ANALYZE)`) was not timed by the harness: the recorded 0.0 s
  means "not measured".

## Memory

Container `memory.peak` over start and ingestion:

| | Peak | Anonymous at end | Page cache at end |
|---|---:|---:|---:|
| MorletDB m-g1 | 1,368–1,370 MiB | 12.5 MiB | 1,309 MiB |
| ModelarDB mdb-l | 3,693–3,777 MiB | 1,572–1,622 MiB | 408 MiB |

MorletDB's peak is almost all reclaimable page cache. ModelarDB holds about 1.6 GiB of anonymous memory after
ingestion. Its lossy arms peak at 2.6–2.8 GiB.

## Aggregates

Whole column: min, max, sum and count over every unit of a channel. Times are ms per round, rounds 1 / 2 / 3.

| Channel | MorletDB | ModelarDB | Ratio |
|---|---|---|---:|
| force_sensor_x | 12.5 / 10.8 / 10.7 | 1250 / 1243 / 1246 | about 115× |
| force_sensor_y | 8.9 / 9.5 / 8.9 | 1289 / 1280 / 1290 | about 140× |
| force_sensor_z | 8.8 / 8.9 / 8.7 | 1307 / 1277 / 1290 | about 147× |
| ctrl (6 channels) | 5.3–6.8 | 22.9–26.7 | about 4× |
| pmid (3 channels) | 2.6–3.8 | 36–55 | 13–17× |

**Decision rule:** MorletDB is faster on every channel in every round.

## Interval statistics and extraction

40 seeded 1-s intervals and 40 whole-unit intervals per group. Times are median ms in rounds 1 / 2 / 3.

| Group, interval | MorletDB read | MorletDB stats | ModelarDB read | ModelarDB stats |
|---|---|---|---|---|
| force, 1 s | 3.7 / 3.9 / 3.8 | 0.9 / 0.9 / 0.9 | 1273 / 1270 / 1252 | 1270 / 1269 / 1250 |
| force, unit | 13.5 / 13.7 / 13.6 | 0.9 / 0.7 / 0.6 | 1286 / 1280 / 1268 | 1290 / 1271 / 1258 |
| ctrl, 1 s | 0.8 / 0.7 / 0.9 | 0.5 / 0.5 / 0.5 | 24.1 / 23.6 / 24.8 | 24.3 / 23.8 / 24.8 |
| ctrl, unit | 0.9 / 0.9 / 1.1 | 0.4 / 0.4 / 0.5 | 24.1 / 24.0 / 24.5 | 24.1 / 23.8 / 24.6 |
| pmid, 1 s | 1.2 / 1.0 / 1.2 | 0.6 / 0.5 / 0.6 | 47.1 / 46.5 / 48.9 | 47.2 / 46.0 / 48.3 |
| pmid, unit | 2.3 / 2.1 / 2.2 | 0.5 / 0.5 / 0.5 | 49.3 / 47.6 / 47.8 | 48.2 / 46.9 / 48.3 |

**Decision rule:** MorletDB is faster on every group and interval kind in every round: 20–330× for reads and
50–2,000× for statistics.

ModelarDB takes about the same time for a 1-s interval as for a whole column of the same table. That points to the
time and unit filters not pruning segments in this build; it is an observation, not tested further.

## Summary per group (no overall winner, per the protocol)

- **force:**
  - ModelarDB stores it smaller (1.43×) and ingests it faster (about 2.3×), but its lossless mode changed 2 values
    of one series by 1 ulp (not value-equal), so by the rule it is not lossless on force.
  - MorletDB is exact, reads and aggregates 100–1,000× faster, and uses about a third of the memory.
- **ctrl:** ModelarDB is smaller (1.50×) and ingests faster (about 2.5×). MorletDB answers queries 4–50× faster.
- **pmid:** MorletDB is smaller (1.42×), ingests faster (about 2.4×) and answers queries 15–100× faster.
- **ModelarDB's lossy modes** returned values outside their stated bounds in every round. Its "lossless" mode turned -0.0
  into +0.0 in the stress vector and changed 2 force values by 1 ulp.
