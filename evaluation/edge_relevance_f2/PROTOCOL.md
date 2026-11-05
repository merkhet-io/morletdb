# F2: keeping the spikes at the edge — wavelet approximation of machine waveforms (DRAFT, not frozen)

Status: draft. Development on development data only ([`DEVELOPMENT.md`](DEVELOPMENT.md)); this file is frozen, with
its title changed to FROZEN, in a commit before any confirmation data is loaded. Report: [paper 3](../../research/paper3).

Scope changed on 2026-11-05 by the owner after the second development round: the study is about edge
approximation. The label-free relevance score (former H3) is dropped: on development data it was not better than
kurtosis. The Bayesian prior is kept only as a comparison arm (H4).

## Question

An edge device cannot send or keep every sample of a machine waveform. At a budget of about a tenth of the lossless
size, which approximation keeps the most of what diagnosis needs, and can a small device produce it in real time?

## Method under test

`shrink.encode`: orthogonal DWT (sym8; 6 levels for vibration, 8 for seismic, 4 for screwdriving cycles); per detail
level, coefficients with |d| > λσ_j are kept (σ_j: robust MAD of that level, so coloured background such as
microseism is handled per level), quantised with step qσ_j; the coarsest band with step qσ_1. The kept positions
(as gaps) and values (zigzag) are written as varints and compressed with zlib level 9: actual bytes, no estimates.
Rate control as on a device: per window, the setting (λ, q from fixed grids) with the smallest relative error among
those within the byte budget.

Comparisons, each with its own grid and the same rate control: SZ3 and ZFP at an absolute error bound (multiples of
the robust noise scale); decimation (FIR, zero phase) with quantised, delta-coded values and the same coder; the
same wavelet coder with one global σ; and the same coder with Bayesian posterior selection (spike and heavy-tailed
slab, `shrink.fit`).

## Hypotheses

- **H1 (error per byte).** At 0.05, 0.1, 0.2 and 0.3 bytes per sample, the level-wise wavelet coder has lower median
  relative error than SZ3, ZFP and decimation on every dataset. Refuted at a budget if SZ3 or ZFP is as good or
  better on two of three datasets.
- **H2 (diagnosis survives).** At 0.1 bytes per sample, analysis on the approximation finds what it finds on the
  original: (a) bearings, run to failure: the fault becomes detectable in the envelope spectrum (fault order, healthy
  baseline + 4 sd, five consecutive files) no more than 1% of the run's files later than on the original; (b)
  seismic: STA/LTA (1 s / 30 s, on 3.5, off 1.5) triggers within 10 s of at least 95% of the catalogued arrivals that
  the original triggers on, with at most 10% more false triggers; (c) screwdriving: the AUROC of kurtosis for NOK
  cycles within 0.02 of the original. Refuted if any fails.
- **H3 (real time on a small device).** On a Raspberry Pi 3B+ (as EX2), the encoder keeps up with a 64 kHz channel on
  one core, with peak memory below 64 MB. Refuted if not.
- **H4 (no Bayesian machinery needed).** Posterior selection with a heavy-tailed slab does not lower the error by
  more than 5% against the level-wise hard threshold at any budget. Refuted (and the Bayesian arm recommended) if it
  does, on two of three datasets.

## Data

Development: CWRU 12 kHz drive end; IMS set 2 bearing 1 (run to failure, outer race); PyScrew s01; the CX gallery of
2025-03 with the EMSC catalogue. Confirmation (proposed): XJTU-SY run-to-failure bearings and Paderborn (real
damage); PyScrew s02; the CX held-out gallery of 2021-03. s02 and the 2021 gallery were F1's confirmation data and
were never used to develop F2; the reuse is disclosed.

## Open before freezing

Grids for λ, q and the baselines; window lengths; the Paderborn and XJTU-SY episode and fault-order definitions;
the run-to-failure detection rule's healthy baseline; whether H2's thresholds are right; Pi access for H3.

## Deviations

(none; not frozen)
