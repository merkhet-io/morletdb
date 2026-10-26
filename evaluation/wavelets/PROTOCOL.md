# W1: do wavelet features detect bearing degradation earlier than standard methods?

Status: **FROZEN** in the commit that adds this line, before the FEMTO archive was
downloaded. Development (the FEMTO learning set and IMS) may only make the choices
listed under "Development". Changes after the freeze are logged under "Changes
after freeze".

## Why

Every frozen test so far found no material advantage for Morlet or wavelet
features:
- bearing retrieval, Paderborn H2: not supported;
- seismic timing: +1.6 km over a spectrogram, below the margin; envelope
  cross-correlation was better;
- compression: decomposition codecs paused.

Textbook theory places wavelets' strength in **short impulsive transients
in noise**, such as incipient bearing defects. That use case has not been tested
against its proper baseline, the spectral kurtosis (kurtogram) family. This is the
final fair test.
- If wavelets win, they keep a defined niche (an opt-in detector).
- If not, the wavelet question is closed for this project.

## Question

On run-to-failure bearings, does a wavelet-based health indicator raise a
**stable alarm earlier** than the matching standard indicator, under one shared,
fixed alarm rule?

## Data

- **Evaluation: FEMTO/PRONOSTIA** (IEEE PHM 2012), NASA PCoE repository copy
  `10.+FEMTO+Bearing.zip`.
  - 1,157,035,288 bytes; ETag `66bd15a6…-68`, checked 2026-10-26.
  - Cite Nectoux et al., PHM 2012, and the NASA repository.
  - Never used by this project.
  - 17 bearings, 3 operating conditions:
    - 1800 rpm / 4000 N;
    - 1650 rpm / 4200 N;
    - 1500 rpm / 5000 N.
  - Horizontal and vertical acceleration at 25.6 kHz; 2560-sample snapshots every
    10 s, run until vibration exceeds 20 g.
  - **Development:** the 6 learning-set bearings (1_1, 1_2, 2_1, 2_2, 3_1, 3_2).
  - **Evaluation:** the 11 full-test-set bearings, read **once** after development.
    The truncated test set is not used.
- **Development only: IMS** (NASA), already on disk.
  - Its failing bearings were consumed by compression stage 1 (alarm times for
    kurtosis, envelope and RMS), so IMS cannot be evidence here. It is used to
    check the harness on a different rig.
  - Failing: set 1 bearings 3 and 4, set 2 bearing 1, set 3 bearing 3. Other
    bearings serve as stability checks.
- **Archive integrity:** the archive's SHA-256 and per-file hashes are recorded
  at download. The file layout and snapshot counts are verified against the
  documentation before any feature is computed.
- **Channel:** horizontal is primary (the load direction); vertical is reported.

## Indicators (one value per snapshot)

All indicators use the snapshot as stored. There is no per-bearing tuning, and
no frequency band or parameter is chosen from evaluation data.

**Impulsiveness family:**

| Id | Indicator |
|---|---|
| **B-SK** (baseline) | STFT kurtogram (Antoni 2006). Spectral kurtosis `E|X|⁴/(E|X|²)² − 2` per frequency bin, for Hann windows of 16, 32, 64, 128 and 256 samples (50% overlap); the maximum over all windows and bins above 200 Hz. |
| **W-WPK** | Wavelet-packet kurtogram: `sym8` packet decomposition to level 4 (16 nodes); kurtosis of each terminal node's coefficients; maximum over nodes. |
| **W-MSK** | Morlet spectral kurtosis: complex Morlet CWT (ω₀ = 6) at 32 log-spaced centre frequencies from 200 Hz to 10 kHz; spectral kurtosis of each scale's coefficients; maximum over scales. |

**Novelty family** (a feature vector; the indicator is the Mahalanobis distance
from the bearing's own calibration snapshots, using Ledoit-Wolf shrinkage
covariance):

| Id | Feature vector |
|---|---|
| **B-FFT** (baseline) | Log energies in 12 log-spaced FFT bands (same band edges as `morlet-energy-v1`), plus RMS, crest factor and kurtosis. |
| **W-MDB** | MorletDB's own `morlet-energy-v1` features: log1p of 12 Morlet band energies (fs/64 to 0.4 fs), RMS, crest factor, kurtosis. These come from the Rust `features` example at the frozen commit. |
| **W-WPN** | Log energies of the 16 level-4 `sym8` packet nodes, plus RMS, crest factor and kurtosis. |

All indicators use the snapshot after mean removal, without amplitude
normalization (`normalization: none`), because absolute vibration level is part of
bearing health.

**Reported, not decided on:**
- RMS;
- wideband kurtosis;
- the envelope-spectrum peak ratio (max/median over 10–1000 Hz of the Hilbert
  envelope, 2–12 kHz band).

## Alarm rule (identical for every indicator, fixed now)

1. Smooth the indicator with a **causal** moving median of 5 snapshots.
2. **Calibration:** the first 120 snapshots of each bearing (20 min for FEMTO;
   IMS uses its first 120 snapshots, about 20 h).
   - Threshold: calibration median + 6 × (1.4826 × MAD).
   - The novelty family fits its mean and covariance on the same snapshots.
3. **Alarm:** the first snapshot after calibration that starts 6 consecutive
   smoothed exceedances (1 min for FEMTO).
4. **Stable alarm:** after the alarm, the smoothed indicator is above the
   threshold in ≥ 50% of the remaining snapshots. Otherwise the alarm counts as
   **unstable**.
   - An unstable alarm is the proxy for a false alarm. Without a labelled onset,
     no exact false-alarm rate exists.
5. **Stable lead** = (end of life − alarm time) / (end of life − end of
   calibration), for a stable alarm. It is **0** for no alarm or an unstable
   alarm, so noisy early alarms earn nothing.

## Development (FEMTO learning set + IMS only)

Development may only:
- fix implementation errors, each logged;
- choose **one** wavelet indicator per family as the family's candidate: the one
  with the higher mean stable lead on the 6 FEMTO learning bearings. Ties go to
  the simpler one: W-WPK over W-MSK, and W-MDB over W-WPN.

The alarm rule, the threshold factor (6), persistence (6), smoothing (5),
calibration length (120), bands and wavelets are **not** tunable. If the rule
produces no stable alarm for any indicator on the development data, W1 stops
there and reports it, without changing the rule.

## Primary comparisons (evaluation, 11 bearings, horizontal channel)

For each family, Δ = mean over bearings of stable lead (candidate − baseline),
with a bootstrap over bearings (2000 resamples, seed 7). Unstable alarms are
counted per indicator.

## Decision rules

- **Niche (continue wavelets as an opt-in detector)** if, in at least one family:
  - the CI lower bound of Δ is > 0;
  - Δ ≥ 0.05, i.e. alarms at least 5% of the remaining life earlier on average;
  - the candidate has no more unstable alarms than the baseline + 1.
- **Close** (wavelets add nothing here; no further wavelet work in this project
  without new evidence) if, in both families, the CI upper bound of Δ is < 0.05.
- **Inconclusive** otherwise. Report it, and do not tune further. Eleven bearings
  may be too few; a next test would need new run-to-failure data, frozen first.

The thresholds are researcher-chosen and provisional until the freeze.

## Controls and limitations

- **Same for every arm:** the alarm rule, calibration, smoothing and channel. The
  baseline and candidates are compared on the same snapshots.
- **No ground-truth fault onset.** Stable lead is a proxy: an earlier stable
  alarm is better only if degradation had begun. Inspecting raw waveforms at
  disputed alarms is reported descriptively.
- **Accelerated life tests at 3 conditions on one rig.** End of life is a
  vibration threshold (20 g), not a defect inspection.
- **Short snapshots.** 0.1 s at 25.6 kHz is about 3 shaft revolutions, which
  limits envelope-spectrum resolution. It affects the reported indicators more
  than the primary ones.
- **Family maximum.** The kurtogram baseline is the STFT variant. The fast
  kurtogram (1/3-binary filter bank) is close to it, but is not implemented.

## Development run

(filled in after development)

## Changes after freeze

(none)
