# C5: does comparing a bearing with its own earlier, like-for-like history warn earlier?

Status: **FROZEN** in the commit that adds this line, before the download. Read so far:
the dataset's description file (`LDM_UPB_Run-to-failure_data_set_of_ball_bearings.pdf`,
v2 of 2024-03-25) and its paper (Javanmardi et al., PHME 2024,
doi:10.36001/phme.2024.v8i1.4101), both in `data/paderborn-rtf/docs/` with SHA-256 in
`SHA256SUMS`, and the Zenodo file list. **No data file has been read.** Changes after
the freeze are logged under "Changes after freeze".

## Why

C1–C3 found that comparing a segment with earlier segments of the same context ranks
anomalies well. C4 and PR #6 found that matching *across* units mostly finds the same
unit. The remaining product claim is the one the viewer and `before_only` serve:
**compare a unit with its own earlier recordings under the same operating conditions,
and you see degradation coming.**

That has not been tested where conditions change. On FEMTO, XJTU-SY and IMS each
bearing runs at one constant condition, so a context-restricted history equals a
context-free one. This dataset re-draws speed and load at random for every recording.

## Questions

- **Q1 (product value).** Does the product's history score, with history restricted to
  earlier recordings in the same operating context, raise a **stable alarm earlier**
  than a plain RMS alarm under the same alarm rule?
- **Q2 (context rule).** Does restricting history to the same context raise stable
  alarms earlier than using all earlier recordings?

Each question has its own decision. Neither result is combined with the other or with
an earlier evaluation.

## Data

- **Run-to-failure data set of ball bearings subjected to time-varying load and speed
  conditions** (Aimiyekagbon, Chair of Dynamics and Mechatronics (LDM), Paderborn
  University).
  - Zenodo record 10868257 (concept 10805042), version of 2024-04-02, CC BY 4.0.
    49 files, 152,008,932,259 bytes: 17 experiments in 48 ZIP parts plus the
    description PDF. Per-file MD5s from the record are checked at download, and
    SHA-256 is recorded.
  - Cite the record and Javanmardi et al. 2024.
- **Content (description file):**
  - 17 run-to-failure experiments (B01–B17) on 61806-2RS deep-groove ball bearings;
    no defect was initiated before the test.
  - Load: a static pre-load (lever) plus a dynamic load (electrodynamic shaker),
    sinusoidal 2 Hz for B01–B06, Gaussian noise up to 200 Hz for B07–B17.
  - Rotating speed and dynamic-load amplitude (for some experiments also the static
    load) are drawn at random from a uniform interval **for every measurement**.
    B14–B16 keep speed and static load constant.
  - Each measurement: 1.6 s, about every 12 s. Two horizontal housing accelerometers
    (A rear, C frontal), in volts, at **128 kHz for B01–B09 and 64 kHz for B10–B17**.
    Temperatures at 10 Hz, given as per-measurement means.
  - Per experiment: `vibrationData/data_BXX_MXXXX.mat` (variables `accHorizRear_A`,
    `accHorizFrontal_C`, `measTime`), `BXX_operatingConditions.csv` (dateTime,
    set and measured loads and speed), `BXX_meanTemperatures.csv`,
    `BXX_extractedFeatures.csv` (not used), `BXX_log.pdf` (conversion factors).
- **End of an experiment:** when the housing vibration exceeds a threshold between 6
  and 10 g (`T_vibration`, based on mean RMS) or the bearing temperature exceeds
  110 °C (`T_temperature`). Durations, file counts and criteria (Table 3):

  | Exp. | Duration | Files | Criterion | Defects found |
  |---|---|---|---|---|
  | B01 | 2 h | 377 | none set | IR, OR, B |
  | B02 | 6 h | 1,116 | vibration | IR |
  | B03 | 3 h | 614 | temperature | IR |
  | B04 | 6 h | 1,114 | vibration | IR, B |
  | B05 | 3 h | 572 | interrupted deliberately | B |
  | B06 | 67 h | 12,965 | vibration | IR, B |
  | B07 | 37 h | 6,393 | temperature | IR |
  | B08 | 11 h | 1,827 | temperature | IR, B |
  | B09 | 13 h | 1,813 | vibration | OR, B |
  | B10 | 19 h | 3,224 | vibration | OR, B |
  | B11 | 8 h | 1,953 | vibration | IR, OR, B |
  | B12 | 3 h | 767 | vibration | IR, B |
  | B13 | 66 h | 19,417 | vibration | IR, B |
  | B14 | 41 h | 12,317 | vibration | B |
  | B15 | 76 h | 22,567 | temperature | IR, B |
  | B16 | 20 h | 5,891 | none listed | IR, OR, B |
  | B17 | 17 h | 2,733 | vibration | B |

  95,660 measurements in total.
- **Relation to consumed data.** The same chair published the bearing diagnosis set
  (KAt) used by PR #6 and compression H2. This is a different rig (shaker-loaded),
  bearing type (61806 instead of 6203) and question; none of its recordings are in
  KAt. FEMTO (W1) and IMS (compression stage 1, W1 development) are already consumed
  for early warning and are used here only to test the harness.
- **Handling:** the data stays in `data/paderborn-rtf/`, separate from code. `.mat`
  files are read with `scipy.io.loadmat` (or `h5py` for MATLAB v7.3), in an isolated
  interpreter (`-I`). Nothing is unpickled.

## Units (fixed now)

- **Unit:** the bearing (experiment). **Segment:** one measurement, channel A, as
  stored in volts (the conversion factor is constant within an experiment, so
  relative deviations do not depend on it). Channel C is reported, not decided on.
- **Time:** the measurement's `dateTime` from `operatingConditions.csv`. The k-th
  `.mat` file by measurement number is matched to the k-th row. If the counts differ
  or the times are not increasing, the experiment's alignment is fixed and logged
  **before** any value is computed; if it cannot be fixed, the experiment is excluded
  and reported.
- **Context** (set values, known before each measurement):
  - speed band: `floor((setSpeed − 1500) / 500)` (500 rpm bands from 1500 rpm);
  - static-load band: `floor(setStatLoad / 500)`;
  - dynamic-load band: `floor(setDynLoad / 500)`;
  - plus the excitation type (fixed per experiment).
- **Primary bearings:** the **14** experiments that ended on a predefined criterion
  (all except B01, B05 and B16), 88,820 measurements. All 17 are reported as a
  sensitivity analysis. **End of life** = time of the last measurement.

## Arms (one indicator per segment)

Values are `simple-v1` as pg_morlet 0.3.1 computes them (sequential sums, population
std, Pearson kurtosis, the negligible-median floor). Relative deviation of value v =
`|x_v − median_h(v)| / |median_h(v)|` over the history h; values whose median is
negligible are skipped, as in the product.

| Id | Indicator | History |
|---|---|---|
| **H-CTX** (candidate) | product summary score: maximum relative deviation over `duration_s`, `std`, `crest`, `kurtosis` (the default `score_values`) | earlier segments of the same bearing **in the same context**; scored only with ≥ 5 such segments (`min_history` 5) |
| **H-ALL** | same score | **all** earlier segments of the same bearing (≥ 5) |
| **B-RMS** (baseline) | RMS of the segment | none (the alarm rule calibrates it) |
| H-RMS (reported) | relative deviation of `rms` only | same context, ≥ 5 |

`duration_s` is constant (1.6 s) and contributes 0; it is kept because it is the
product's default.

## Alarm rule (identical for every arm, as in W1)

Indicators are computed over the arm's **scored** segments in time order; unscored
segments (too little history) are skipped.

1. Smooth with a **causal** moving median of 5 scored segments.
2. **Calibration:** the scored segments among the first 150 measurements (about
   30 min). If fewer than 30 are scored, calibration extends to the first 30 scored
   segments; the extension is reported per arm and bearing.
   - Threshold: calibration median + 6 × (1.4826 × MAD) of the smoothed indicator.
3. **Alarm:** the first scored segment after calibration that starts 6 consecutive
   smoothed exceedances.
4. **Stable alarm:** after the alarm, the smoothed indicator exceeds the threshold in
   ≥ 50% of the remaining scored segments. Otherwise the alarm is **unstable** (the
   false-alarm proxy; there is no labelled onset).
5. **Stable lead** = (end of life − alarm time) / (end of life − end of calibration)
   for a stable alarm; **0** for no alarm or an unstable alarm.

No parameter is tunable. Harness development on FEMTO and IMS (consumed) may only fix
implementation errors, each logged.

## Decision rules (14 primary bearings)

For each question, Δ = mean over bearings of stable lead (H-CTX − comparator), with a
bootstrap over bearings (2000 resamples, seed 7). The comparator is B-RMS for Q1 and
H-ALL for Q2.

- **Supported** if Δ ≥ 0.05, its CI lower bound is > 0, and H-CTX has no more unstable
  alarms than the comparator + 1.
- **Not supported** if the CI upper bound of Δ is < 0.05.
- **Inconclusive** otherwise. Report it and do not tune.

## Reported, not decided on

- Per bearing and arm: alarm time (minutes after calibration and fraction of life),
  stable or not, the top value behind the alarm, the scored-segment count, and the
  bearing temperature at the alarm.
- **Temperature-ended bearings** (B03, B07, B08, B15). Their end of life does not
  depend on vibration, so their leads are not inflated by the vibration threshold.
  Four bearings are too few for a decision.
- **Without `crest`** (score over `std` and `kurtosis`), because peak-driven values are
  closest to the vibration stop criterion.
- All 17 bearings; channel C; H-RMS.
- **Product check:** for 3 primary bearings, at least 3 scored segments each, the
  harness's H-CTX score must match `morlet_compare_summary(..., before_only => true,
  min_history => 5)` on pg_morlet 0.3.1 to 1e-9 relative. Each measurement is loaded
  as a raw-only recording in its own acquisition (origin = measurement time) with
  its context; occurrence key = measurement. A mismatch stops the evaluation until the
  harness is fixed and logged.

## Controls and limitations

- **Same for every arm:** segments, channel, alarm rule and end of life.
- **Circularity.** Ten primary bearings stop on a vibration threshold, which every
  vibration indicator approaches near the end. This inflates every arm's lead and
  favours B-RMS (the threshold is RMS-based). The questions compare arms on the same
  bearings; the temperature-ended subset and the no-crest analysis are reported to
  show how much the stop rule matters.
- **Run-in and thermal drift.** W1 found novelty alarms minutes after calibration on
  FEMTO, most likely from drift. Alarm minutes after calibration and temperature at
  the alarm are reported for every arm.
- **No labelled onset.** Stable lead is a proxy: an earlier stable alarm is better
  only if degradation had begun.
- **One rig, accelerated life, 61806 bearings.** Results say nothing about other
  machines.
- **Context bands are fixed, not tuned.** A finer or coarser banding could change Q2.

## Changes after freeze

1. **Timestamp column name** (2026-10-28, before any value was computed). The operating
   condition and temperature CSVs name their timestamp column `Time` (e.g.
   `19-Oct-2021 08:46:54`), not `dateTime` as written above. `c5.py` reads `Time`, and
   parses timestamps as UTC so results do not depend on the machine's time zone (only
   differences are used). Found while checking B01, B03 and B16 against Table 3: file and
   row counts match (377, 614, 5,891), times strictly increase, every row has a temperature
   row. `.mat` files hold `measTime` relative to the measurement start, so the k-th file is
   matched to the k-th row by measurement number, as frozen. Some ZIPs exceed 4 GB (zip64)
   and are extracted with Python's `zipfile`.
2. **Pauses: running time reported** (2026-10-28, before any arm or alarm was computed).
   B07's measurements pause for 31.2 h at about 47% of its measurements (wall-clock span
   67.6 h; Table 3 gives 37 h), and B01's for 0.3 h. Table 3's durations therefore count
   running time. The decision rules stay as frozen, on measurement timestamps. Every
   decision is also **reported** with running time: gaps over 10 minutes shortened to the
   bearing's median interval (`running_time` in `c5.py`). If the two disagree, the
   frozen verdict stands and the disagreement is stated.
