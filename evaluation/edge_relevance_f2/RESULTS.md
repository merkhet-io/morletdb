# F2 results: wavelet approximation at the edge (confirmation)

Protocol: [`PROTOCOL.md`](PROTOCOL.md), frozen in `f66102a` before any confirmation data was loaded. One run per
dataset on 2026-11-05 (23:30–23:47), decisions by [`analyze_f2.py`](analyze_f2.py); raw results in
[`results/`](results/). Two deviations: the Paderborn run was repeated once after a crash in aggregation (no
result had been written), and the seismic recordings' labels are not unique (see below). Frozen and run by the
assistant overnight at the owner's request.

## Outcome

| Hypothesis | Decision |
|---|---|
| **H1** error per byte: lower median error than SZ3 and ZFP on ≥ 4 of 5 datasets | **Holds at every budget** (4/5 at 0.05, 5/5 at 0.1, 5/5 at 0.2, 4/5 at 0.3) |
| **H2** diagnosis survives at 0.1 bytes/sample | **Refuted**: (a) bearings pass, (b) seismic fails narrowly, (c) screwdriving fails, (d) CNC passes, (e) Paderborn passes |
| **H3** real time on a Raspberry Pi 3B+ at 64 kHz | **Holds**: 3.06× real time, 55.5 MB peak |
| **H4** no Bayesian machinery needed | **Holds**: posterior selection was worse at every budget but one |

## H1: median relative error (plain decode; missed windows count as worse than any reached)

| Dataset | Budget | Wavelet | SZ3 | ZFP | Decimation |
|---|---:|---:|---:|---:|---:|
| XJTU-SY bearings (25.6 kHz) | 0.05 / 0.1 / 0.2 / 0.3 | **0.81 / 0.70 / 0.53** / 0.40 | 1.00 / 0.97 / 0.75 / **0.39** | 1.00 | 0.85 / 0.76 / 0.62 / 0.59 |
| Paderborn bearings (64 kHz) | | **0.72 / 0.55 / 0.35 / 0.21** | 1.00 / 0.72 / 0.42 / 0.24 | 1.01 | 0.79 / 0.63 / 0.43 / 0.36 |
| PyScrew s02 cycles | | missed / 0.078 / **0.038** / **0.027** | missed / missed / 1.34 / 0.24 | missed / missed / 3.02 / 0.62 | **0.18 / 0.070** / 0.038 / 0.031 |
| Seismic, CX 2021 | | **0.082 / 0.056 / 0.030 / 0.018** | 0.155 / 0.093 / 0.050 / 0.027 | missed | 0.121 / 0.080 / 0.043 / 0.035 |
| Bosch CNC, M02 + M03 | | **0.53 / 0.39 / 0.24 / 0.17** | 1.00 / 0.76 / 0.44 / 0.28 | missed | 0.60 / 0.49 / 0.43 / 0.42 |

The coder beats SZ3 and ZFP everywhere except XJTU-SY at 0.3 (0.400 against 0.387) and short screwdriving cycles
at 0.05, which it cannot reach (9,592 of 12,499 cycles missed; a cycle of 400–700 samples leaves too few bytes).
Against decimation, which the hypothesis does not decide on, it is better on bearings, seismic and CNC data and
slightly worse on screwdriving cycles at 0.05 and 0.1.

## H2: diagnosis at 0.1 bytes per sample

**(a) XJTU-SY run to failure, plain decode: pass.** The original detects the fault on 7 of 11 bearings (2_1, 2_4,
3_3, 3_5 not detected by the rule, so excluded); the approximation detects every one of them no later than allowed:

| Bearing (files) | Original | Wavelet | SZ3 | Decimation | Isolated alarms, original / wavelet |
|---|---:|---:|---:|---:|---|
| 1_1 (123) | 74 | 73 | 78 | 73 | 1 / 4 |
| 1_2 (161) | 62 | 62 | 63 | 57 | 0 / 4 |
| 1_3 (158) | 81 | 74 | 108 | 74 | 2 / 12 |
| 2_2 (161) | 48 | 48 | 53 | 53 | 0 / 0 |
| 2_5 (339) | 190 | 168 | 217 | 190 | 1 / 7 |
| 3_1 (2,538) | 2,454 | 2,403 | 149 | 2,446 | 1 / 50 |
| 3_4 (1,515) | 1,468 | 1,464 | never | 1,491 | 12 / 10 |

The rule only bounds lateness. The approximation is never late, but on four bearings it detects earlier than the
original and raises more isolated alarms in the healthy part (50 against 1 on 3_1): thresholding makes the
envelope spectrum peakier. SZ3 is late on five bearings, raises a false detection at file 149 of 3_1, and never
detects 3_4.

**(b) Seismic, noise-filled decode: fail, narrowly.** 16 station-days, 154 catalogued arrivals; the original
triggers on 96 with 2,990 triggers elsewhere. Noise-filled: 91 (94.8% of 96; the criterion is 95%) with 2,813
elsewhere (within 10%). Plain decode: 105 arrivals but 7,467 elsewhere (2.5×). SZ3: 90 / 3,040. Decimation:
90 / 4,038. At 0.2 bytes per sample the noise-filled decode triggers on 97 with 2,883 elsewhere.

**(c) PyScrew s02, plain decode: fail.** Kurtosis barely separates s02's NOK cycles even on the original (AUROC
0.566; s02 varies surface friction, which changes the level of the curve more than its shape). On the approximation
0.529 (noise-filled 0.500, decimation 0.506); SZ3 cannot reach 0.1 bytes per sample on these cycles. The
diagnostic proxy is weak on this dataset, and the approximation weakens it further.

**(d) Bosch CNC, plain decode: pass.** AUROC of the label-free run score 0.671 against 0.682 (noise-filled 0.679;
SZ3 0.740 and decimation 0.716 are higher than the original). RMS deviation (median, wear proxy): plain 7.5%,
**noise-filled 0.05%**, SZ3 6.9%, decimation 11.7%.

**(e) Paderborn, plain decode: pass.** AUROC of kurtosis, damaged against healthy (200 against 120 files):
0.220 against 0.201 (kurtosis is lower on these damaged bearings; the separation is preserved). Noise-filled
0.275, SZ3 0.136, decimation 0.278.

## H3: Raspberry Pi 3B+ (frozen `bench_pi.py`, Paderborn KA04 files 1–5, 64 kHz, one core)

| Rate control | Real time at 64 kHz | Bytes/sample | Median error |
|---|---:|---:|---:|
| none (fixed setting) | 16.8× | 0.153 | 0.31 |
| **bisection** | **3.06×** | 0.099 | 0.43 |
| grid | 0.13× | 0.093 | 0.44 |

Peak resident memory 55.5 MB. Supplementary (not the frozen measure): the exact frozen coder including the
per-level RMS values (`bisect_c` + `encode_nf`) runs at 2.53× real time, 46.4 MB. [`results/f2_confirm_pi_20261105.txt`](results/f2_confirm_pi_20261105.txt).

## H4: Bayesian selection against the plain threshold (median error)

| | 0.05 | 0.1 | 0.2 | 0.3 |
|---|---|---|---|---|
| XJTU-SY, 40 windows: Bayes / plain | 0.85 / 0.83 | 0.76 / 0.72 | 0.64 / 0.54 | 0.63 / 0.41 |
| Paderborn, 13 windows | missed / 0.61 | missed / 0.44 | 0.32 / 0.24 | 0.12 / 0.13 |

Better by more than 5% only once (Paderborn at 0.3, 0.118 against 0.127), so not on both datasets: H4 holds.

## Reading

- **What holds:** as a code per byte, a plain wavelet threshold with a varint and deflate stream is better than
  SZ3, ZFP and decimation for machine vibration and seismic data from 0.05 to 0.3 bytes per sample, it keeps a
  bearing fault visible at least as early as the original, and it runs at three times real time for 64 kHz on one
  core of a Raspberry Pi 3B+ in a Python prototype. The Bayesian machinery is not needed.
- **What does not:** "diagnosis survives" does not hold in general. Energy-based seismic detection misses a few
  arrivals even with noise filling (just below the 95% bar), and on short screwdriving cycles a weak shape statistic
  gets weaker. Thresholding also makes envelope spectra peakier, which raises isolated alarms before a real fault.
- **For the product:** an approximated level is worth building for vibration (bearings, CNC) as a long-retention
  and narrow-uplink tier, always with exact per-chunk statistics (or the noise-filled decode) for trends, and with
  alarm thresholds calibrated on approximated data. It should not be the only tier for trigger-based event
  detection or for short cycles, where exact data or decimation is the better choice.

## Deviations

1. **Paderborn rerun after a crash in aggregation** (see PROTOCOL.md).
2. **Seismic recording labels are not unique.** The preparation script labels recordings by station and segment
   index without the day, so the 16 station-days appear under 4 labels in the result file. Every recording was
   coded and scored separately; all totals above are sums over the 16, so no measure is affected.
