# S1 results: explainable sleep staging by retrieval of similar epochs

Run once on 2026-10-29, as frozen in `PROTOCOL.md` (158c153), with one logged change after freeze
(the split, regenerated before any recording was read). Held-out subjects were read once, by
`evaluate`.

**Short version.**

- **Q1 is not supported.** Morlet features stage sleep no better than FFT band powers with the
  same band layout.
- **Q2 is supported.** Staging by the 40 most similar expert-scored epochs is not worse than a
  logistic-regression classifier on the same features. It scores higher in kappa, with equal
  macro-F1.
- **"Explanation by example" costs nothing here, but Morlet adds nothing over FFT.** Absolute
  agreement is moderate: kappa about 0.58 from one EEG channel.

## Data

- Sleep-EDF Expanded 1.0.0, sleep-cassette study: 306 files, 7.1 GB.
  - All SHA-256 checksums match the release's `SHA256SUMS.txt`.
  - 78 subjects, 153 nights. Subjects 36 and 52 (development) and 13 (held-out) have one night,
    as PhysioNet documents.
- 39 development subjects (76 nights, 97,310 epochs) and 39 held-out subjects (77 nights,
  98,159 epochs). EEG Fpz-Cz at 100 Hz in µV; 30-s epochs; 30 minutes kept either side of the
  sleep period.
- **Product check:** 20 random development epochs recomputed in separate pg_morlet calls match the
  stored vectors exactly (difference 0.0).

## Selection (development subjects only, 5-fold leave-subjects-out)

| Arm | Chosen | Development kappa |
|---|---|---|
| M-KNN | k = 40 | 0.568 |
| F-KNN | k = 40 | 0.564 |
| M-LR | C = 10 | 0.532 |
| F5-KNN (classic bands) | k = 40 | 0.550 |

Every KNN arm chose the largest k in the frozen grid, so a larger k might score higher. The grid
was not extended.

## Verdicts (39 held-out subjects, per-subject Cohen's kappa)

| Question | Δ kappa | 95% CI | Verdict |
|---|---|---|---|
| **Q1** M-KNN vs F-KNN | **−0.006** | [−0.015, 0.002] | **not supported** |
| **Q2** M-KNN vs M-LR (non-inferiority, margin 0.05) | **+0.042** | [0.016, 0.068] | **supported** |

- **Q1:** Morlet is better than FFT on 19 of 39 subjects. The two transforms give practically the
  same staging. This matches our earlier evaluations: pooled Morlet features did not beat simpler
  spectral features there either.
- **Q2:** the retrieval arm is ahead on 30 of 39 subjects, so it is not only non-inferior but
  higher in kappa.
  - Part of that comes from M-LR's class-balanced weights (fixed in the protocol). They push
    predictions toward the rare N1 stage, which costs kappa.
  - Macro-F1 is about equal: 0.613 for M-KNN and 0.610 for M-LR.

## All arms (held-out)

| Arm | Kappa | Macro-F1 | F1 W | F1 N1 | F1 N2 | F1 N3 | F1 REM |
|---|---|---|---|---|---|---|---|
| M-KNN | 0.583 | 0.613 | 0.84 | 0.21 | 0.77 | 0.75 | 0.49 |
| F-KNN | 0.589 | 0.611 | 0.85 | 0.20 | 0.77 | 0.74 | 0.49 |
| M-LR | 0.541 | 0.610 | 0.79 | 0.35 | 0.72 | 0.65 | 0.53 |
| F5-KNN (classic sleep bands) | 0.573 | 0.597 | 0.83 | 0.17 | 0.78 | 0.75 | 0.44 |
| M-KNN, context t−1..t+1 | 0.594 | 0.618 | 0.86 | 0.22 | 0.77 | 0.73 | 0.51 |
| F-KNN, context | 0.596 | 0.616 | 0.86 | 0.22 | 0.77 | 0.72 | 0.51 |
| M-LR, context | 0.585 | 0.646 | 0.83 | 0.39 | 0.74 | 0.67 | 0.60 |
| F5-KNN, context | 0.583 | 0.601 | 0.85 | 0.18 | 0.77 | 0.73 | 0.47 |

- **M-KNN per subject:** kappa from 0.31 to 0.82, median 0.60.
- **Classic sleep bands** (0.5–30 Hz) did slightly *worse* than the Morlet layout. Losing
  0.5–1.5 Hz did not cost much at this level of agreement.
- **Context** (neighbouring epochs) helps every arm a little; it helps logistic regression most.
- **The product's raw distance** (`morlet_feature_vector_distance` on unscaled features) gives
  kappa 0.570 against 0.583 standardized. That is a small cost from mixing log energies with RMS
  in µV.
- **Absolute level.** These arms use one EEG channel and 15 features per epoch. Published
  automatic staging uses EEG, EOG and EMG with deep models and is not comparable. N1 (F1 about
  0.2) and REM (about 0.5) are the weak stages, as expected without EOG.

## Explanation and personal history (reported, decides nothing)

- **Neighbour agreement:** on average 74% of an epoch's 40 neighbours carry the predicted stage.
  That number could be shown next to each prediction.
- **Mixed sources:** only 0.4% of neighbourhoods come from a single development subject. The
  examples are drawn from many people, not one look-alike.
- **Personal gallery:** a held-out subject's night 2 staged from their own night 1 reaches kappa
  0.517 (38 subjects), *below* the 0.583 from 39 other people. One labelled night is a small
  gallery, and k = 40 was chosen for a large one.

## What this means

- **For a sleep app, retrieval is a sound way to explain a stage:** "this epoch looks like these
  40 expert-scored ones, 74% of them N2". It costs no accuracy against a simple classifier.
- **Morlet is not the reason it works.** FFT band powers with the same layout do as well, so the
  value is in retrieval with provenance, not the wavelet transform. This repeats the earlier
  finding.
- **Personal history is not a shortcut to accuracy.** It may still matter for spotting what is
  unusual for one person, which would be a separate, frozen anomaly protocol (for example on the
  sleep-telemetry temazepam/placebo nights).
- **Not a medical device.** Nothing here supports a diagnostic use.

## Limitations

- One site, old recordings (1987–1991), healthy subjects, one EEG channel, R&K scoring with S3
  and S4 merged.
- k = 40 was at the edge of the frozen grid.
- Q2's margin partly reflects the class-weighting choice for M-LR.

Outputs: `results/s1.json` (per-subject kappas, all arms), `results/selection.json`,
`results/product_check.json`. Cite Kemp et al. 2000 and PhysioNet.
