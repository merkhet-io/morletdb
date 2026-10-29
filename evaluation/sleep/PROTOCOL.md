# S1: explainable sleep staging by retrieval of similar epochs

Status: **DRAFT.** Not frozen, and no data has been downloaded or read. It is frozen in a later
commit, before any recording is read. Read so far: the PhysioNet pages of Sleep-EDF Expanded
1.0.0 and Apnea-ECG 1.0.0, and the morlet-extractor contract.

## Why

For a sleep app, MorletDB's possible contribution is **explanation by example**. Each 30-second
epoch gets a stage together with "it looks like these expert-scored epochs", and the person can
look at those epochs. That is only worth building if retrieval is not much worse than an ordinary
classifier on the same features. It is also worth knowing whether Morlet features beat plain FFT
band powers here. Our earlier evaluations found pooled Morlet features no better than simpler
ones.

This is a research and wellness question, not diagnosis. Nothing here is a medical device claim.

## Questions

- **Q1 (representation).** Does k-nearest-neighbour staging on Morlet features (M-KNN) agree with
  the experts better than the same k-nearest-neighbour staging on FFT band powers with the same
  band layout (F-KNN)?
- **Q2 (cost of explanation).** Is M-KNN non-inferior to a logistic-regression classifier on the
  same Morlet features (M-LR), within a margin of 0.05 in Cohen's kappa?

Each question has its own decision. Neither is combined with the other or with an earlier
evaluation.

## Data

- **Sleep-EDF Expanded 1.0.0** (PhysioNet), **sleep-cassette study only**: 153 whole-night
  recordings of healthy adults aged 25–101 (1987–1991), two nights per subject for most
  subjects. Licence: Open Data Commons Attribution 1.0. Cite Kemp et al. 2000 (IEEE-BME 47(9))
  and PhysioNet.
  - **Download:** `sleep-cassette/` only, 306 files (153 PSG and 153 hypnogram EDF files),
    about 7.24 GB, from physionet.org. SHA-256 checked against the release's `SHA256SUMS.txt`.
    **Not downloaded yet; needs approval.**
  - The sleep-telemetry study (44 recordings, temazepam vs placebo) is not used here. It may serve
    a later within-person anomaly protocol.
- **Signal:** EEG Fpz-Cz at 100 Hz, in µV as stored. Pz-Oz, EOG and EMG are not used in the
  primary analysis.
- **Labels:** the hypnograms (Rechtschaffen and Kales, scored by technicians). Mapping:
  - W → W, 1 → N1, 2 → N2, 3 and 4 → N3, R → REM;
  - M (movement) and ? (not scored) are dropped.
- **Scope of each night:** the recordings run about 20 hours. Keep epochs from 30 minutes before
  the first non-W epoch to 30 minutes after the last one, the usual convention, so that daytime
  wake does not dominate.
- **Handling:** the data stays in `data/sleep-edfx/` and is read with a numpy EDF reader in an
  isolated interpreter (`python -I`). Nothing is pickled.

## Split (fixed now)

- **Unit:** the subject. Both nights of a subject always fall on the same side.
- **Split:** subjects are shuffled with seed 7. The first half (rounded down) is **development**
  and the rest is **held-out**.
  - Held-out recordings are read once, after the freeze, by `evaluate`.
  - The split is written to `split.json` and committed before any recording is read.
- **Gallery for retrieval:** development subjects' epochs only. A held-out epoch never retrieves
  epochs of its own subject.

## Representation (one feature vector per 30-s epoch, 3,000 samples)

- **Morlet:** `morlet_features(epoch, 100, 'morlet-energy-lowrate-v1')` with **no**
  normalization, because amplitude defines N3. That gives 12 band energies (log1p, centres
  1.56–40 Hz), AC RMS, crest factor and kurtosis. SQL and the harness must agree to 1e-9
  relative on 20 epochs (product check).
- **FFT, same layout:** Welch power spectrum of the epoch (4-s Hann segments, 50% overlap). Band
  power is summed between the geometric midpoints of the same 12 centres, then log1p. The arm
  keeps the same three statistics (RMS, crest, kurtosis). This isolates the transform.
- **Scaling:** every arm standardizes each feature with the development set's mean and standard
  deviation. The product's raw feature distance mixes log energies with RMS in µV, so it is
  reported separately (below), not used in the decision.

## Arms

| Id | Features | Model |
|---|---|---|
| **M-KNN** (candidate) | Morlet | k nearest development epochs by Euclidean distance; majority vote, ties broken by the nearest neighbour |
| **F-KNN** (Q1 comparator) | FFT, same layout | same |
| **M-LR** (Q2 comparator) | Morlet | multinomial logistic regression, L2 penalty, class-balanced weights |

- **Fixed on development data only,** by leave-subjects-out cross-validation (5 folds of
  development subjects):
  - k for each KNN arm, from {5, 10, 20, 40};
  - the L2 strength for M-LR, from {0.01, 0.1, 1, 10}.
- **Selection criterion:** mean per-subject Cohen's kappa.

## Metric and decision rules

- **Per held-out subject:** Cohen's kappa over that subject's epochs (both nights), five stages.
- **Δ** = mean over held-out subjects of (candidate − comparator), with a bootstrap over subjects
  (2,000 resamples, seed 7).
- **Q1:**
  - **supported** if Δ ≥ 0.02 and its CI lower bound is > 0;
  - **not supported** if the CI upper bound is < 0.02;
  - **inconclusive** otherwise.
- **Q2 (non-inferiority):**
  - **supported** if the CI lower bound of (M-KNN − M-LR) is > −0.05;
  - **not supported** if the CI upper bound is < −0.05;
  - **inconclusive** otherwise.
- No parameter changes after the held-out data is read. Results are reported whatever they are.

## Reported, not decided on

- Macro-F1 and per-stage F1 for every arm. N1 is known to be scored poorly by humans too.
- An FFT arm with classic sleep bands (0.5–4, 4–8, 8–12, 12–16, 16–30 Hz). The Morlet layout
  starts at 1.56 Hz and misses much of slow-wave activity (0.5–1.5 Hz), and this arm shows how
  much that costs.
- A context variant for every arm: features of epochs t−1, t and t+1 concatenated.
- The product's raw distance (`morlet_feature_vector_distance`, unscaled) for M-KNN.
- **Explanation quality, descriptively:** for each held-out epoch, the share of the k neighbours
  with the predicted stage, and how often the neighbours come from a single development subject.
- **Personal gallery (exploratory):** each held-out subject's night 2 staged from their own night 1
  with M-KNN. This is the "your own history" setting. It needs labelled nights, so it decides
  nothing.

## Controls and limitations

- **Same for every arm:** epochs, labels, split, scaling and selection procedure.
- **Old, healthy, one-site data** (1987–1991 cassette recordings, Fpz-Cz). Results say little about
  consumer headbands or sleep disorders.
- **R&K scoring with S3/S4 merged** approximates the modern AASM N3. Inter-scorer agreement
  limits every arm.
- **Single channel.** Clinical and published automatic staging uses EEG, EOG and EMG; published
  numbers on this dataset are not comparable and are not used as comparators.
- **Not a medical device.** No diagnostic claim follows from any result.

## Next protocol (not part of S1)

**S2: per-minute apnea detection from a single-lead ECG** on Apnea-ECG 1.0.0 (ODC Attribution
1.0, 70 recordings, 580.6 MB). It has a predefined learning/test split (a/b/c vs x records);
test-set labels now appear in the file listing and need checking. External reference: the 2000
challenge, whose entrants reached 85–93% per-minute accuracy. A minute (6,000 samples) exceeds the
4,096-sample window, so the window design must be fixed first.

## Changes after freeze

None (not frozen).
