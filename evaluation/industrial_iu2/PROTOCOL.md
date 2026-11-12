# IU2: bounded-lag envelope distance and tool wear (FROZEN 2026-11-11)

Frozen before the raw signal files were read. Written by the assistant for Semir Maslo (Sulzer-BDiM). Deviations
are appended at the end, never edited in place. Harness `iu2.py`, decisions by `iu2_analyze.py`. One run per task.

Known when this was written: the article, the two metadata files, the archive's directory (file names and
sizes) and the first 3,000 bytes of the smallest file (header and seven rows). No signal statistics.

## Why

IU1 (Bosch) showed that bad runs there are mostly runs of unusual length, that the good history must be recent,
and that run length and RMS do as well as envelope distance. IU2 asks the question on data where the state
changes gradually and the cycle is the same: a tool milled until it breaks.

## Data

Piecuch and Żabiński, "A new open dataset from a milling process", Scientific Data 12, 650 (2025), Figshare
doi 10.6084/m9.figshare.28589216, CC BY. 968 milling cycles of 14 end mills run to failure on a Haas VF-1
(42CrMo4), 8 accelerometers at 25 kHz and 12 current channels at 0.5 kHz in raw counts. `raw_data.zip`
(25.3 GB, 111.8 GB of CSV) is read from the archive without extracting it.

- **Recordings:** one per cycle and spindle accelerometer (`Spindle +Y`, `-Z`, `-X`), 25,000 Hz, unit `counts`.
  The five table accelerometers and the current channels are not loaded.
- **Context of a cycle:** tool, tool type, ADOC, RDOC and the cycle's position in its layer (`C1` to `C7`; the path
  gets shorter towards the centre, so cycles are compared only with the same position).
- **Order in time:** by `CycleToFailure` from the metadata (cycles remaining until the tool breaks).
- **Tools:** tool 11 broke in its first cycle and is excluded (the authors call it an outlier).
- **Split by tool, fixed now from the metadata only:** per cutting condition (tool type, ADOC, RDOC) the tool with
  the lowest index is development, the others are confirmation.
  Development: tools 2, 3, 7, 8, 101, 102. Confirmation: tools 4, 5, 6, 9, 10, 103, 105.
  Development tools serve to check the harness. Nothing below is changed after looking at them.

## History: only the past

For every tool, the **baseline** is its first layer: its first 4 cycles (RDOC 8 mm) or 7 cycles (RDOC 4.5 mm),
one per position. Every later cycle is compared only with the baseline cycle of the same tool and position.
No cycle sees a later cycle, another tool or a label.

## Scores per cycle

Envelopes with the product defaults (windows of 4,096 samples, stride 2,048, `max_lag_fraction` 0.075 for BL, 0
for ZL). Up to 16 query windows per cycle and axis, evenly spaced on the envelope grid, chosen deterministically.

- **BL / ZL:** median over query windows and axes of the distance to the nearest window of the baseline cycle
  (same tool, position and axis).
- **RMS-window:** the same with nearest windows by absolute RMS difference (`morlet_interval_stats`).
- **Vibration level:** standard deviation of the three spindle accelerometers from the authors' feature table,
  mean of the three ratios to the baseline cycle, as absolute log ratio.
- **Spindle current:** mean of `Current - Spindle L1` from the feature table, absolute log ratio to the baseline
  cycle.
- **Time in use:** number of cycles the tool has completed. Always known to the operator.
- **Cycle length:** absolute log ratio of the cycle's sample count to the baseline cycle's.

## Tasks and decision rules (confirmation tools)

**W1 End of life across tools.** Positive: the last 10% of a tool's life (`CycleToFailureNormalized` <= 0.1,
including the cycle in which it breaks). Negative: every earlier cycle after the baseline. AUROC over the pooled
cycles of all confirmation tools, per score. 95% interval by a bootstrap over tools (10,000 resamples of the
seven tools, seed 20261111).
- "BL detects the end of life" if the interval of its AUROC lies above 0.5.
- "BL better than X" for X in ZL, RMS-window, vibration level, spindle current, time in use: if the interval of
  the AUROC difference lies above 0; "worse" if below; else "no difference".

**W2 Trend inside a tool.** Spearman correlation of each score with time in use, per tool; reported as the
median over tools with the range. Descriptive. Time in use itself is 1 by construction and is not an arm here.

**W3 Early warning.** Threshold per score: the 95th percentile of that score over the first half of life of the
**development** tools (fixed before confirmation). On confirmation tools: share of tools flagged before they
break, the median remaining life (in percent of life) at the first flag, and the share of first-half cycles
flagged (false alarms). Descriptive.

**W4 Lag use.** Median BL and ZL score in the first and in the last tenth of life, and the share of query windows
where BL is lower than ZL by more than 0.01. Descriptive.

## Environment

MorletDB main at 088f03b (SQL 0.3.8) with pgvector, PostgreSQL 17.6 in Docker Desktop on a Windows 11 laptop
(16 logical CPUs, 7.2 GB for Docker), port bound to 127.0.0.1. Exact search only; every gallery here is one
cycle.

## Not claimed

Wear measurement (the dataset has no wear values, only cycles to failure), diagnosis, other tools or materials,
real-time use. Seven confirmation tools give wide intervals; they are reported as they are.

## Deviations

1. 2026-11-11, before the raw files were read, from the metadata only. The baseline rule is implemented as "the
   tool's earliest cycle at each position". For two tools the first layer is incomplete in the dataset: tool 6
   has no file for layer 1, position 2, and tool 102 none for layer 1, position 5; their baseline for that
   position is the cycle of layer 2 (the tool's 5th and 11th cycle). Tool 2 has two cycles with the same
   `CycleToFailure` (16); their order is taken from the file name.
2. Class sizes on the confirmation tools, known from the metadata before loading: 429 cycles after the baseline,
   48 in the last tenth of life (tools 4, 5, 6, 9, 10, 103, 105: 5, 6, 2, 14, 2, 6, 13).
3. 2026-11-11, before the full load. A smoke run on the first 8 cycles of development tool 3, in a separate
   collection (`public.toollife_smoke`), checked loader and scoring. Its scores were not looked at beyond
   "4 cycles scored". `raw_data.zip` matches the Figshare MD5 (615fef19c51344a163acea913d3f7f22).
4. 2026-11-11, during the full load. The load stopped after 902 of 967 cycles with
   `OSError: [Errno 12] Cannot allocate memory` while five threads read the largest CSV files. No score had been
   computed. The loader was made resumable (a cycle counts as loaded if its three recordings exist with equal
   length and all envelope windows; anything else is appended again, which the database verifies against the
   stored chunks) and reads the CSV in blocks with two threads. Only the missing cycles were loaded again.
