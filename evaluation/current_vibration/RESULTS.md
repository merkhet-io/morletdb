# CV1 results: does motor current carry vibration information beyond operating conditions? — PAUSE

Run once on 2026-10-31 as frozen in [PROTOCOL.md](PROTOCOL.md):
- protocol committed at 9f65a11;
- features, harness and tests committed at 4cec87f, before fitting;
- one change after the freeze (dropping incomplete 8th windows), logged in the protocol.

**Exploratory:** these bearings informed earlier studies.

**Verdict: PAUSE**, under the frozen rule:
- **Small, not separable from control:** adding motor current to the measured operating
  conditions lowers the band-energy error by 2.5%. That can't be distinguished from
  adding shuffled current.
- **Useful improvement ruled out:** the CI's lower bound (0.905) is above the 0.90
  threshold, so an improvement of 10% or more is ruled out on this data. A small one is
  not.
- **No retrieval step:** retrieval and MorletDB integration were not run (gated).

## Data actually used

- **Bearings:** 31 (KA08 is missing from the archive).
- **Recordings:** 1,751 of 2,480 four-second recordings. The other 729 (29%) were
  excluded by the existing non-uniform-sampling rule, the same 729 as the decision
  evaluation. Every bearing keeps at least 29 recordings, and every bearing × setting at
  least 4.
- **Windows:** 13,955 non-overlapping 0.5 s windows.
- **Alignment:** current and vibration share one 64 kHz raster in every file.
  Mechanical channels were averaged over the same span (1,999–2,001 samples per window).
  No lag was fitted.

## Gate (leave one bearing out, 31 folds; grouped inner tuning)

| Model | Mean per-bearing error (RMSE of log₁₀ band energy, averaged over 4 bands) |
|---|---|
| A: training mean | 0.474 |
| B: measured operating conditions | 0.396 |
| C: current only | 0.395 |
| **D: operating conditions + current** | **0.386** |
| D with shuffled current (control) | 0.395 |

| Comparison | Ratio | 95% CI (bootstrap over bearings) | Bearings better |
|---|---|---|---|
| **D / B** (gate: ≤ 0.90, CI below 1) | **0.975** | [0.905, 1.055] | 18/31 (gate: 21) |
| D / D-shuffled (gate: CI below 1) | 0.976 | [0.902, 1.055] | 18/31 |
| C / B | 0.998 | [0.870, 1.127] | 14/31 |
| B / A | 0.835 | [0.722, 0.955] | 23/31 |

None of the three gate conditions holds. The CI isn't entirely above 0.95, so the verdict
is PAUSE, not REJECT.

## Where the error is

- **Between recordings, mostly between bearings:**
  - between-recording RMSE is 0.390 for B and 0.372 for D;
  - within-recording, the target varies by only 0.033 (log₁₀) around its recording
    mean.
- **No tracking of within-recording variation:**
  - the correlation between predicted and actual within-recording deviations is 0.03 for
    both B and D;
  - adding current makes within-recording error *worse* (0.081 against 0.045), because it
    adds fluctuation the target doesn't have.
  - As stated in the protocol, 4 s recordings at fixed settings barely vary, so this
    data cannot test tracking.
- **What dominates:** bearings differ in vibration energy by about a factor of 2.4
  (0.38 in log₁₀) in ways neither the mechanics nor the current explain.

## Descriptive (decides nothing)

**Per band** (mean per-bearing RMSE):

| Band | A | B | C | D | D-shuffled |
|---|---|---|---|---|---|
| 10–1,000 Hz | 0.668 | 0.564 | 0.686 | 0.613 | 0.566 |
| 1–5 kHz | 0.424 | 0.295 | 0.311 | 0.321 | 0.295 |
| 5–15 kHz | 0.392 | 0.291 | 0.301 | 0.307 | 0.289 |
| **15–30 kHz** | 0.412 | 0.433 | **0.282** | **0.303** | 0.431 |

- **Current is informative only in the top band,** where current alone beats the
  operating conditions by about 35%. In the three lower bands, adding current makes the
  error slightly *worse*.
- **A likely explanation, not verified:** electrical coupling of the inverter's switching
  frequency (typically around 10–20 kHz) into the accelerometer chain. That would be
  crosstalk between sensors, not mechanical information about the bearing. Checking it
  would need the drive's switching frequency, which the dataset does not document.
- **Band choice:** no band was chosen after testing. The gate used the frozen average.

**Per setting (D / B):**

| Setting | D / B |
|---|---|
| N09_M07_F10 | 0.926 |
| N15_M01_F10 | 1.001 |
| N15_M07_F04 | 0.970 |
| N15_M07_F10 | 1.006 |

**Per bearing class (B → D):**

| Class | B | D |
|---|---|---|
| Healthy | 0.425 | 0.397 |
| Artificial damage | 0.346 | 0.322 |
| Real damage | 0.423 | 0.431 |

**Cross-channel residual correlation** (for the multichannel-storage question): per
window, the correlation of first differences of `phase_current_1` and `vibration_1` has a
median of 0.13 (10th–90th percentile −0.01 to 0.29).
- **Implication:** joint coding of current and vibration could save at most about
  ½·log₂(1/(1 − 0.13²)) ≈ 0.01 bits per sample. Current and vibration give no reason for
  joint chunk coding.

## Limitations

- **Grid edge:** every model chose the largest α on the shared grid (100) in every fold.
  Stronger regularisation might help D more than B in the lower bands, where current
  appears to overfit bearing-specific patterns. It was not tried, because the protocol
  forbids re-tuning.
- **One rig:** one motor and inverter; results don't transfer to other machines.
- **Data:** about 29% of recordings are excluded by the sampling-uniformity rule.
- **Units:** the stored channels have none.

## Answers

1. **Does current carry predictive information beyond operating conditions?** Not
   usefully for vibration band energy overall: at most a small gain, with 10% or more
   ruled out. The exception is the 15–30 kHz band, which is probably electrical rather
   than mechanical.
2. **Does it track within-recording vibration?** This data can't test it. The
   recordings are steady, and no model tracks the small residual variation.
3. **Does retrieval improve on simple regression?** Not tested: the gate did not pass.
4. **Does MorletDB add a useful systems capability?** Not tested. The cross-channel
   residual correlation argues against native joint current-and-vibration chunk coding.
5. **Is a neural operator warranted?** No.
   - This data has steady 4 s recordings at four fixed settings, with no varying
     excitation, so it offers no function-to-function map to learn.
   - The cross-sensor signal that exists is largely in a band that is probably
     electrical.
   - A task that would justify one needs time-varying excitation, such as speed or load
     ramps with synchronised current and vibration, and a response richer than band
     energies.

## Related work (short note; from the cited works' abstracts and memory, not re-read for this task)

- **Motor-current signature analysis for bearing damage** is established:
  - Schoen, Habetler, Kamran & Bartheld, "Motor bearing damage detection using stator
    current monitoring", IEEE Trans. Industry Applications 31(6), 1995;
  - Benbouzid's review of induction-motor signature analysis, IEEE Trans. Industrial
    Electronics 47(5), 2000;
  - Lessmeier et al. (2016) built this dataset as a current-based fault-classification
    benchmark.

  CV1 asks something different and narrower: whether current predicts vibration *energy*
  beyond the measured mechanics, not whether it classifies damage.
- **Cross-sensor estimation (virtual or soft sensing)** is a standard regression setting.
  Nothing here is methodologically new; the contribution is the controlled negative
  result on this rig.
- **Operator learning** (DeepONet, Lu et al., Nature Machine Intelligence 2021; Fourier
  Neural Operator, Li et al., ICLR 2021; wavelet and multiwavelet operators, see
  `evaluation/forecasting/RELATED_WORK.md`) learns maps between functions. It needs
  inputs and outputs that vary as functions, which this data doesn't have (see answer 5).

## Reproduce

```sh
.venv-seismic/bin/python -I evaluation/current_vibration/test_cv1.py
.venv-seismic/bin/python -I evaluation/current_vibration/features.py data/paderborn/zenodo-15845309/extracted evaluation/current_vibration/results/features.npz
.venv-seismic/bin/python -I evaluation/current_vibration/gate.py evaluation/current_vibration/results/features.npz evaluation/current_vibration/results
```

- **Not committed:** `features.npz` (about 3 MB, derived; regenerated by the second
  command).
- **Committed:** `results/gate.json`, with per-bearing errors, per-band and per-setting
  results, the selected α, and the decomposition.
- **Data:** Paderborn KAt (Lessmeier et al., 2016). Acquisition and checksums as in
  `evaluation/decision/DATASET.md`.
