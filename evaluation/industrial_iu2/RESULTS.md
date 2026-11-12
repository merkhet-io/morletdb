# IU2 results: bounded-lag envelope distance and tool wear

Protocol: [`PROTOCOL.md`](PROTOCOL.md), frozen before the raw signal files were read; deviations 1 to 4 are listed
there. One run on 2026-11-11. Harness `iu2.py`, decisions by `iu2_analyze.py`, every number in
`results/summary.json`, per-cycle scores in `results/cycle_scores.json`.

Data: Piecuch and Żabiński 2025 (Figshare 10.6084/m9.figshare.28589216, CC BY), 967 cycles of 13 end mills run to
failure (tool 11 excluded), three spindle accelerometers at 25 kHz, 2,901 recordings, 4.88 billion samples,
about 2.38 million envelope windows. Development tools 2, 3, 7, 8, 101, 102; confirmation tools 4, 5, 6, 9, 10,
103, 105. History: each cycle is compared only with the first layer of its own tool, same position.
MorletDB 088f03b (SQL 0.3.8), PostgreSQL 17.6 in Docker Desktop on a Windows laptop.

## W1: end of life across tools (confirmation, 48 cycles in the last tenth of life against 381 earlier)

AUROC with a 95% interval from a bootstrap over the seven tools.

| Score | AUROC | BL minus this score | Decision |
|---|---|---|---|
| Bounded lag (BL) | 0.639 [0.539, 0.751] | | detects the end of life (interval above 0.5) |
| Zero lag (ZL) | 0.580 [0.510, 0.649] | +0.059 [−0.004, 0.131] | no difference |
| RMS per window | 0.791 [0.731, 0.849] | −0.152 [−0.253, −0.016] | BL worse |
| Vibration level (std ratio) | 0.831 [0.694, 0.938] | −0.192 [−0.305, −0.001] | BL worse |
| Spindle current (mean ratio) | 0.813 [0.705, 0.909] | −0.174 [−0.253, −0.082] | BL worse |
| Time in use (cycle count) | 0.778 [0.721, 0.913] | −0.139 [−0.296, −0.075] | BL worse |
| Cycle length | 0.580 [0.445, 0.689] | +0.059 [−0.034, 0.173] | no difference |

Development tools agree: BL 0.652, ZL 0.628, RMS window 0.756, vibration level 0.768, spindle current 0.704, time in
use 0.874. There BL was better than ZL (+0.023 [0.010, 0.054]).

Per confirmation tool, BL ranges from 0.17 (tool 10, 8 cycles) to 0.95 (tool 103); vibration level from 0.46
(tool 103) to 0.98.

## W2: trend inside a tool (Spearman with time in use, median over tools and range)

| Score | Confirmation | Development |
|---|---|---|
| Bounded lag | 0.44 (−0.24 to 0.68) | 0.59 (−0.11 to 0.72) |
| Zero lag | 0.26 (−0.48 to 0.40) | 0.45 (−0.08 to 0.57) |
| RMS per window | 0.70 (0.07 to 0.86) | 0.73 (0.50 to 0.88) |
| Vibration level | 0.53 (0.02 to 0.91) | 0.51 (−0.31 to 0.68) |
| Spindle current | 0.65 (−0.14 to 0.82) | 0.65 (0.20 to 0.85) |
| Cycle length | 0.16 (−0.48 to 0.44) | 0.11 (−0.05 to 0.25) |

## W3: early warning with a threshold from the development tools

| Score | Tools flagged before breaking | Remaining life at first flag (median) | First-half cycles flagged |
|---|---|---|---|
| Bounded lag | 2 of 7 | 41% | 0.5% |
| Zero lag | 3 of 7 | 47% | 0.5% |
| RMS per window | 7 of 7 | 53% | 19.8% |
| Vibration level | 7 of 7 | 78% | 12.0% |
| Spindle current | 2 of 7 | 28% | 0.0% |
| Cycle length | 2 of 7 | 60% | 1.0% |

## W4: lag use

Median distance to the baseline in the first tenth of life: BL 0.450, ZL 0.522; in the last tenth: BL 0.500, ZL 0.557.
In 79% of query windows BL is lower than ZL by more than 0.01.

## Reading

- **The envelope distance carries a wear signal, but a weak one.** BL is above chance across tools (0.64) and
  rises inside most tools, yet every level value does better: per-window RMS, the overall vibration level, the
  spindle current, and simply counting cycles.
- **The reason is visible in W4.** Even a new tool's cycles are far from their own baseline (0.45 on a scale where
  0 is the same envelope shape), and wear moves the distance only to 0.50. Steady milling has little envelope
  structure to repeat from cycle to cycle; what changes with wear is the level, which the normalised envelope
  removes by design.
- **Lag tolerance still helps the scorer** (BL lower than ZL in 79% of windows, higher AUROC and trend than ZL on
  both tool sets), but on the confirmation tools the difference in AUROC is not significant.
- **The simple values are not a finished detector either.** With one threshold from other tools, vibration level
  and RMS flag every tool before it breaks, but also 12 to 20% of cycles in the first half of life.
- **Storage:** 4.88 billion samples take 6.47 GB of chunks (10.6 bits per sample) against 39.1 GB as float64;
  envelopes add 2.37 GB. The source archive is 25.3 GB zipped CSV for all 20 channels (111.8 GB unzipped).
  The first 902 cycles loaded in about 17 minutes with five threads, the rest in 2 minutes with two.

## Limits

One dataset, 13 tools, seven of them for confirmation, so intervals are wide. No wear measurement, only cycles
to failure. Only the three spindle accelerometers; envelopes with product defaults (0.16 s windows at 25 kHz),
not tuned to the tooth-passing frequency. The baseline is one cycle per position; a wider baseline was not tried.
Nothing was tuned after the development tools were seen.
