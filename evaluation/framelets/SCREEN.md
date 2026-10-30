# F1 screen: framelets and multiwavelets for sleep staging by retrieval

**Result: nothing advances; see [RESULTS.md](RESULTS.md).** Status: plan, written and committed before any framelet or multiwavelet feature is
computed.** This is an exploratory screen on development data only. It decides
whether a frozen protocol is worth writing; it is not evidence on its own.

## Why, and why only a screen

[W1](../wavelets/PROTOCOL.md) closed the wavelet question for this project: "further
wavelet work needs new evidence". Five frozen tests found Morlet features tie or lose
to FFT band powers (bearings, CWRU, ECG, seismic, S1 sleep).

Two transform families were never tried:

- **Framelets (tight wavelet frames):** redundant, shift-invariant filter banks. The
  undecimated piecewise-linear B-spline framelet (Ron & Shen, 1997) has two
  high-pass filters per level: a first-difference-like one that responds to edges
  and slopes, and a second-difference-like one that responds to peaks.
- **Multiwavelets:** several scaling functions at once. GHM (Geronimo, Hardin &
  Massopust, 1994) is orthogonal, symmetric and short-supported together, which no
  scalar wavelet is.

The most likely outcome is another tie: pooled band energies carry the same
information whichever transform produces them. The plausible exception is **shape**,
such as how impulsive or bursty a band is (spindles, K-complexes and slow-wave slopes
in sleep EEG). Shape is therefore tested against a classical control that measures
the same thing without a wavelet.

## Data and setup

- S1's 39 **development** subjects only (76 nights, 97,310 epochs), with S1's split
  and epoch rules. **No held-out subject is read.**
- EEG Fpz-Cz, 100 Hz, 30-s epochs, read from the EDF files with S1's reader.
- One classifier for every arm: S1's KNN with **k = 40** (S1's selected value; not
  tuned here), its vote rule, and standardization over all development epochs as in
  S1's selection.
- 5-fold leave-subjects-out CV with S1's fold assignment. The score is per-subject
  Cohen's kappa, averaged.

## Arms

Each arm is 12 band values plus the same three statistics (RMS, crest factor and
kurtosis, from S1's stored Morlet features), unless stated.

| Id | Bands | Extra |
|---|---|---|
| **F** (baseline) | S1's FFT band powers, Morlet layout | – |
| M | S1's Morlet features (`morlet-energy-lowrate-v1`) | – |
| FR-E | Undecimated B-spline framelet, 6 levels, 2 high-pass filters each: log1p of mean squared coefficient | – |
| MW-E | GHM multiwavelet, orthogonal, 6 levels, 2 components each: log1p of mean squared detail coefficient | – |
| FR-S | as FR-E | log kurtosis of the first-difference framelet coefficients, 6 levels |
| B-S (control for FR-S) | as F | log kurtosis of Butterworth band-pass signals (order 4, zero phase) in the same 6 octaves |

Implementation notes, fixed now:
- Framelet filters: h0 = [1, 2, 1]/4, h1 = √2/4 · [1, 0, −1], h2 = [−1, 2, −1]/4,
  à trous (dilated by 2^(j−1) at level j), periodic boundary. Level j covers roughly
  50/2^j to 50/2^(j−1) Hz, so 6 levels reach 0.78 Hz.
- GHM: the input is paired into vectors (x[2n], x[2n+1]) after symmetric extension of
  the epoch from 3000 to 3072 samples. The transform is periodic and decimated.
- Octaves for B-S: (25, 50) as a high-pass, then (12.5, 25), (6.25, 12.5),
  (3.125, 6.25), (1.5625, 3.125), (0.78125, 1.5625).
- Tests before any EEG is read:
  - the framelet satisfies Parseval (energy preserved);
  - GHM is orthogonal and perfectly reconstructs;
  - a sine at a level's centre puts most of its energy in that level.

## Rule for advancing (fixed before running)

A candidate **advances to a frozen protocol** only if both hold:

1. its mean per-subject kappa exceeds **F** by at least **+0.02**;
2. it is ahead of F in at least **4 of 5** folds.

FR-S must also beat its control **B-S** by at least **+0.01**. Otherwise, any gain is
credited to the shape statistic, not the framelet.

If nothing advances, the wavelet question stays closed, as W1 decided. A paired
bootstrap CI over subjects is reported for each Δ but is descriptive: development
data cannot confirm anything.

If a candidate advances, its confirmation set would be the untouched Sleep-EDF
**sleep-telemetry** study (44 nights). That download needs separate approval.

## Outputs

`screen.py` (features, CV), `test_screen.py` (transform tests), `results/screen.json`,
and `RESULTS.md`.
