# F2: keeping the spikes at the edge — wavelet approximation of machine waveforms (FROZEN)

Frozen in the commit that changes this title to FROZEN, before any confirmation data was loaded, after four
development rounds on development data only ([`DEVELOPMENT.md`](DEVELOPMENT.md)). Report: [paper 3](../../research/paper3).
The owner asked on 2026-11-05 to finish the open studies overnight; this freeze was made by the assistant while the
owner was away, and every choice below is recorded here. Deviations are appended below.

Scope changed on 2026-11-05 by the owner after the second development round: the study is about edge
approximation. The label-free relevance score is dropped (on development data it was not better than kurtosis).

## Question

An edge device cannot send or keep every sample of a machine waveform. At a budget of about a tenth of the lossless
size, which approximation keeps the most of what diagnosis needs, and can a small device produce it in real time?

## Method under test

Orthogonal DWT (sym8; 6 levels for vibration, 8 for seismic, 4 for screwdriving cycles). Transform coder
(`shrink.encode_nf`): one absolute threshold T = c·std(x) for all detail levels, step 0.5·T for kept coefficients
and the coarsest band, gaps and zigzag values as varints, per-level RMS of the dropped coefficients as float16, zlib
level 9: actual bytes. Rate control as on the device (`shrink.bisect_c`): log bisection on c in [0.001, 10], 8 tries,
the smallest c whose code fits the budget; a window that does not fit at c = 10 is missed (counted as a failure).
Two decodes of the same bytes: **plain** (dropped coefficients zero) and **noise-filled** (dropped positions filled
with seeded Gaussian noise of the level's RMS).

Comparisons (rate control: the smallest relative error within the budget on each method's grid, which favours
them): SZ3 and ZFP at absolute error bounds of 0.25–512 times the robust noise scale; decimation by 2–64 (FIR, zero
phase) with quantised, delta-coded values and the same varint and zlib coding; and, for H4, the same coder with
coefficients selected by the posterior of an empirical-Bayes spike-and-slab model with a Pareto slab
(`shrink.fit`, tau 0.5 / 0.9 / 0.99). Harness: [`f2_confirm.py`](f2_confirm.py) (with `f2_edge.py`), one run per
dataset; budgets 0.05, 0.1, 0.2, 0.3 bytes per sample.

## Confirmation data (none used in development)

1. **XJTU-SY** run-to-failure bearings with a single outer- or inner-race fault (1_1, 1_2, 1_3, 2_1, 2_2, 2_4, 2_5,
   3_1, 3_3, 3_4, 3_5), horizontal channel, 25.6 kHz, one window per file (32,768 samples). Fault orders from the
   dataset's documented geometry (8 balls, ball 7.92 mm, pitch 34.55 mm, contact angle 0): outer 3.083, inner 4.917
   times the shaft frequency.
2. **Paderborn** bearings with real damage (KA04, KA15, KA16, KA22, KA30, KI04, KI14, KI16, KI18, KI21) and healthy
   bearings (K001–K006), condition N15_M07_F10, files 1–20, `vibration_1` at 64 kHz, three windows of 65,536 per
   file. (B3 uses Paderborn files as a storage workload; that is a different study and never touched F2.)
3. **PyScrew s02** (as F1's confirmation, never used to develop F2): every cycle of at least 128 samples.
4. **Seismic:** the CX held-out gallery of 2021-03 with E5-H's EMSC catalogue (F1's confirmation data, never used
   to develop F2): 16 station-days drawn with seed 20261105, windows of 2^15 samples.
5. **Bosch CNC milling,** machines M02 and M03: every bad run and 200 good runs drawn with seed 20261105, three
   axes, windows of 8,192.

## Hypotheses (decisions at 0.1 bytes per sample unless stated)

- **H1 (error per byte).** At each budget, the coder's plain decode has a lower median relative error than SZ3 and
  ZFP on at least four of the five datasets (a missed window counts as worse than any reached one). Holds or is
  refuted per budget; the comparison with decimation is reported.
- **H2 (diagnosis survives), all of:** (a) XJTU-SY, plain decode: for every bearing on which the original detects
  the fault (envelope band 2–8 kHz, fault order ± 2 bins over the median, healthy baseline the first min(100, 30%)
  files, mean + 4 sd, five consecutive files), the approximation detects it no later than ceil(1% of the run's
  files) after the original; (b) seismic, noise-filled decode: STA/LTA (2–20 Hz, 1 s / 30 s, on 8, off 1.5)
  triggers within 10 s of at least 95% as many catalogued arrivals as the original, with at most 10% more triggers
  elsewhere; (c) PyScrew s02, plain: AUROC of kurtosis for NOK cycles within 0.02 of the original; (d) Bosch,
  plain: AUROC of the label-free run score (largest robust z-score of RMS or kurtosis over the axes, per operation)
  within 0.02; (e) Paderborn, plain: AUROC of kurtosis, damaged against healthy, within 0.02. Refuted if any fails.
  Secondary: isolated alarms before detection (XJTU-SY), RMS deviation (Bosch) for both decodes.
- **H3 (real time on a small device).** On the Raspberry Pi 3B+ of EX2, `bench_pi.py` on Paderborn KA04
  N15_M07_F10 files 1–5 (`vibration_1`, 64 kHz): bisection rate control at 0.1 bytes per sample on one core at least
  1.0 times real time at 64 kHz, peak resident memory below 64 MB. Refuted if not.
- **H4 (no Bayesian machinery needed).** On the first 40 windows of XJTU-SY and the first window of the first 13
  Paderborn files, posterior selection does not lower the median error by more than 5% against the plain threshold
  at any budget. Refuted if it does on both.

## Deviations

(none yet)
