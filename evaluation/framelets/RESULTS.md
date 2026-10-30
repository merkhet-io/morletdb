# F1 screen results: framelets and multiwavelets for sleep staging

Run once on 2026-10-30 as planned in [`SCREEN.md`](SCREEN.md) (committed at 0f20b71
before any feature was computed). Development subjects only; no held-out subject was
read. Exploratory: this decides only whether a frozen protocol is worth writing.

**Short version: nothing advances. The wavelet question stays closed, as W1 decided.**

- The undecimated B-spline framelet stages sleep *worse* than FFT bands: Δ kappa −0.041.
- The GHM multiwavelet is also worse: −0.011.
- Framelet shape statistics did not help the framelet: FR-S is 0.037 below its classical
  control B-S, which uses the same statistic on Butterworth band-pass signals.
- The only arm ahead of FFT uses no wavelet: FFT bands plus band-pass kurtosis (B-S),
  +0.009, below the +0.02 bar.

## Harness check

At k = 40, F and M reproduce S1's development scores (0.564 and 0.568 in
`evaluation/sleep/results/selection.json`), so the arms below sit on S1's exact setup.

## Arms (39 development subjects, 97,310 epochs, 5-fold leave-subjects-out, KNN k = 40)

| Arm | Dims | Mean per-subject kappa | Per fold |
|---|---|---|---|
| **F** (FFT bands, baseline) | 15 | **0.564** | 0.620 0.525 0.587 0.595 0.484 |
| M (Morlet, product) | 15 | 0.568 | 0.624 0.548 0.581 0.592 0.485 |
| FR-E (framelet energies) | 15 | 0.523 | 0.593 0.478 0.507 0.544 0.489 |
| MW-E (GHM multiwavelet energies) | 15 | 0.553 | 0.615 0.511 0.565 0.570 0.499 |
| FR-S (framelet energies + framelet kurtosis) | 21 | 0.536 | 0.598 0.508 0.525 0.558 0.485 |
| B-S (FFT bands + band-pass kurtosis, control) | 21 | 0.573 | 0.618 0.539 0.603 0.603 0.493 |

## Against the rule

The rule: Δ ≥ +0.02 vs F, ahead in ≥ 4 of 5 folds, and for FR-S also ≥ +0.01 vs B-S.
The CIs are paired bootstraps over subjects and are descriptive only.

| Comparison | Δ kappa | 95% CI (descriptive) | Folds ahead | Subjects ahead | Advances |
|---|---|---|---|---|---|
| FR-E vs F | −0.041 | [−0.056, −0.026] | 1/5 | 5/39 | no |
| MW-E vs F | −0.011 | [−0.020, −0.001] | 1/5 | 13/39 | no |
| FR-S vs F | −0.028 | [−0.044, −0.011] | 1/5 | 11/39 | no |
| FR-S vs B-S | −0.037 | [−0.050, −0.024] | 0/5 | 7/39 | no |
| *M vs F (reference)* | +0.004 | [−0.003, +0.010] | 3/5 | 26/39 | – |
| *B-S vs F (control, reference)* | +0.009 | [−0.002, +0.020] | 4/5 | 24/39 | – |

## Reading

- **Why the framelet loses** (an interpretation, not tested):
  - It has octave resolution: 6 levels, and the two high-pass filters at each level
    overlap heavily. The FFT and Morlet layouts use 12 half-octave bands. In sleep EEG,
    where alpha, sigma and beta sit close together, coarse bands lose information.
  - GHM, which is decimated and orthogonal, has the same octave layout and loses less.
- **Shape helps a little, but not through a wavelet.** Band-pass kurtosis added to FFT
  bands is the only addition that moves in the right direction (24 of 39 subjects
  ahead, 4 of 5 folds). The same statistic computed from framelet coefficients does
  not. A frozen test of "FFT + shape statistics" could be written, but +0.009 is
  small, and the mechanism is a classical filter, not a wavelet.
- **This repeats every earlier result:** the transform is not where the value is.

## Limitations

- One task (sleep staging by KNN), one channel, k fixed at 40, and no tuning of
  levels, filters or prefilters. A tuned multiwavelet might do better; the screen
  was deliberately not a search.
- Only one framelet (piecewise-linear B-spline) and one multiwavelet (GHM) were tried.
- Development data only; nothing here is confirmatory.

Outputs: `results/screen.json` (per-subject and per-fold kappas, all comparisons).
