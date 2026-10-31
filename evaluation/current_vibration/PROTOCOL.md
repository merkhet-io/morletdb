# CV1: does motor current carry vibration information beyond the measured operating conditions?

Status: **gate FROZEN** in the commit that adds this file.
- **Inspected so far:** only the file structure (one development file, `K002/N15_M07_F10_K002_1.mat`).
  No feature or model has been computed.
- **Exploratory:** these Paderborn bearings informed earlier studies (the decision
  evaluation, C5/C6, the compression benchmark). Grouped cross-validation gives honest
  estimates, but none of this is untouched confirmation.

This is **cross-sensor estimation**: contemporaneous current predicting contemporaneous
vibration energy. It is not forecasting, causality or operator learning.

## Data (verified, see "Verification")

- **Paderborn KAt** (Lessmeier et al., 2016), in `data/paderborn/zenodo-15845309/extracted`.
  - 31 of 32 bearings; KA08's archive is missing.
  - 4 settings × 20 recordings of 4 s per bearing.
- **Channels in each `.mat` file:**
  - `vibration_1`, `phase_current_1` and `phase_current_2` on **one shared raster,
    `HostService`** (256,001 samples, 0–4 s, 64 kHz). Current and vibration are
    therefore synchronised by file structure, not by inferred alignment.
  - `speed`, `torque` and `force` on `Mech_4kHz` (16,001 samples, starting at −0.2 ms).
  - Temperature at 1 Hz (not used).
  - No units are stored.
- **Excluded files:** those whose `HostService` raster is not uniform at 64 kHz (±6.4 Hz),
  using the existing loader rule (`evaluation/decision/paderborn_mat.py`). That rule was
  decided on development bearing K002 for an earlier study.
- **Bearing classes** (from `evaluation/decision/DATASET.md`; reported, never used as
  inputs):
  - healthy: K001–K006;
  - artificial damage: KA01, KA03, KA05, KA06, KA07, KA09, KI01, KI03, KI05, KI07, KI08;
  - real damage: KA04, KA15, KA16, KA22, KA30, KI04, KI14, KI16, KI17, KI18, KI21, KB23,
    KB24, KB27.

## Windows and target

- **Windows:** 8 non-overlapping 0.5 s windows per recording (32,000 `HostService`
  samples each).
- **Mechanical channels:** averaged over the same time span, using their own time axis.
- **Target:** vibration log₁₀ band energy in **4 bands fixed now**: 10–1,000, 1,000–5,000,
  5,000–15,000 and 15,000–30,000 Hz.
  - **Why these bands:** the accelerometer chain is low-passed at 30 kHz and Nyquist is
    32 kHz. The bands are roughly octave-spaced, with the lowest excluding DC.
  - **Computation:** remove the mean, apply a Hann window, take the rFFT, and sum |X|²
    over each band. log₁₀(E + 10⁻¹²); the floor is far below any real energy.
  - **Amplitude** is kept: there is no per-recording normalisation.

## Feature sets (fixed now)

| Set | Features |
|---|---|
| A | none: the training mean of each band |
| **B: operating conditions** | one-hot nominal setting (4), plus per-window mean and standard deviation of measured speed, torque and force (6) |
| C: current | per phase (2): log₁₀ RMS of the mean-removed signal, crest factor, kurtosis, and log₁₀ band energy in 0–200, 200–1,000, 1,000–5,000, 5,000–15,000 and 15,000–30,000 Hz. 16 features in total |
| **D: B + C** | 26 features |

**Model:**
- multi-output ridge regression on standardised features, with the scaling fitted on
  training data only;
- α ∈ {10⁻³, 10⁻², 10⁻¹, 1, 10, 100}, the same 6 values for every set;
- one α per model, selected by **5-fold grouped cross-validation over training
  bearings**, using the primary metric.

**Shuffled-current control (D-shuffled):**
- D trained on data where each training recording's whole current block (all 8 windows)
  is swapped with that of another training recording **with the same nominal setting**;
- evaluated on the held-out bearing's true features;
- 5 seeds, averaged.

This breaks the link between current and vibration and keeps everything else, including
operating conditions.

## Splits

- **Leave one bearing out:** 31 outer folds.
- **Isolation:** every window of a held-out bearing is excluded from training, tuning
  and scaling. This is asserted.
- **Windows** are never split randomly. Inner tuning folds group by bearing.

## Primary metric and gate

- **Per held-out bearing:** the RMSE over its windows, per band, averaged over the 4
  bands.
- **Primary metric:** the mean over bearings, so each bearing weighs equally.
- **Uncertainty:** a 95% bootstrap over bearings (2000 resamples, seed 7) of the ratio of
  means.

**CONTINUE** to the retrieval step (written as a separate protocol then) only if all
hold:
1. D / B ≤ **0.90** (current removes at least 10% of the error left by operating
   conditions), with the CI upper bound below 1;
2. D / D-shuffled has a CI upper bound below 1;
3. D is better than B on at least 2/3 of bearings (21 of 31).

**Otherwise:**
- **PAUSE** if the CI is wide (it includes both 0.90 and 1);
- **REJECT** if D / B's CI lower bound is above 0.95.

## Reported descriptively (decides nothing)

- **Decomposition** of held-out errors into between-recording and within-recording
  parts:
  - **between:** the RMSE of recording-mean predictions against recording-mean targets;
  - **within:** the RMSE and correlation of the deviations from each recording's mean.

  **Expected limitation, stated now:** every recording is 4 s at fixed settings, so the
  within-recording variation is small and mostly estimation noise. A null within-recording
  result says the data can't test tracking. It is not evidence against a relationship.
- **Other models:** C alone and A.
- **Breakdowns:** per band, per setting, and per bearing class (healthy, artificial, real).
- **Cross-channel residual correlation** (a by-product for the multichannel-storage
  question): per window, the Pearson correlation between the first differences of
  `phase_current_1` and of `vibration_1`, summarised over all windows.

## Not in this task

- No retrieval (that needs a separate protocol after a pass);
- no neural networks, wavelets or learned representations;
- no MorletDB integration;
- no changes to production code.
