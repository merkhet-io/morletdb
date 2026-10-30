# W2: does a Legendre multiwavelet expose bearing fault frequencies better than the kurtogram?

**Result: CLOSE.** The gain over the kurtogram comes from frequency-aware band selection, not the multiwavelet: under the same rule the kurtogram bands do better (Δ −0.185). See [RESULTS.md](RESULTS.md).

Status: **FROZEN** in the commit that adds this line. The harness and its synthetic tests
are in the same commit. No XJTU-SY envelope spectrum has been computed. Changes after the
freeze are logged under "Changes after freeze".

## Why

Zheng, Lei, Feng & Chen (*Applied Sciences* 14(1):219, 2024; doi:10.3390/app14010219)
propose a Legendre multiwavelet transform (LMWT) with envelope spectrum analysis for
bearing faults, including **compound faults**. A "relative energy ratio" picks the
decomposition branch most related to the fault frequencies. They report better spectrum
segmentation and detection than the empirical wavelet transform (EWT).

Project context:
- [W1](../wavelets/PROTOCOL.md) closed the wavelet question for early fault alarms: the
  STFT kurtogram beat Morlet spectral kurtosis and wavelet packets.
- [F1](../framelets/RESULTS.md) found GHM multiwavelets and framelets below FFT bands for
  sleep staging.

This paper is a different task: **identifying which fault frequency is present**. W1's
rule allows wavelet work with new evidence. W2 is a single decisive test against the
standard method, Antoni's (fast) kurtogram, not against EWT.

**Not reproduced exactly.** The full text could not be retrieved (the publisher blocks
automated access), so the multiplicity, levels and the exact relative energy ratio are
our definitions below, built from the standard Legendre multiwavelet (Alpert, 1993). This
tests the method family, not the paper's exact numbers.

## Data

- **XJTU-SY** (Wang, Lei, Li & Li, IEEE Trans. Reliability 2018), on disk at
  `data/xjtu-sy/extracted` (checksums in `data/xjtu-sy/SHA256SUMS`).
  - 15 LDK UER204 bearings.
  - 25.6 kHz, snapshots of 32,768 samples (1.28 s) every minute.
  - Horizontal and vertical channels.
- **Prior use:** C7 used XJTU-SY for alarm timing from window features. No envelope
  spectrum or fault-frequency analysis of XJTU-SY has been run in this project.
- **CWRU is not on disk** and is not used. A single-fault sanity check on it would need
  a separate download approval.
- **Ground truth:** the fault elements in the dataset's documentation (Table 2):

| Bearing | Speed | Fault elements |
|---|---|---|
| 1_1, 1_2, 1_3 | 35 Hz | outer race |
| 1_4 | 35 Hz | cage |
| **1_5** | 35 Hz | **inner race, outer race** |
| 2_1 | 37.5 Hz | inner race |
| 2_2, 2_4, 2_5 | 37.5 Hz | outer race |
| 2_3 | 37.5 Hz | cage |
| 3_1, 3_5 | 40 Hz | outer race |
| **3_2** | 40 Hz | **inner race, ball, cage, outer race** |
| 3_3, 3_4 | 40 Hz | inner race |

  That gives 19 bearing-element pairs over 15 bearings, 2 of them compound.
- **Characteristic frequencies** from the documented geometry (8 balls, d = 7.92 mm,
  D = 34.55 mm, contact angle 0) at the nominal shaft speed fr:
  - BPFO = 4 fr (1 − d/D);
  - BPFI = 4 fr (1 + d/D);
  - BSF = D/(2d) · fr · (1 − (d/D)²);
  - FTF = fr/2 · (1 − d/D).

  At 35 Hz these are 107.91, 172.09, 72.33 and 13.49 Hz, matching the dataset paper.
- **Snapshots:** the **last 10** of each bearing, when the documented damage is present.
- **Channel:** horizontal is primary; vertical is reported.

## Pipeline (identical for every arm)

1. Pick a band signal according to the arm (below).
2. **Envelope spectrum:** |Hilbert| of the band signal, mean removed, Hann window,
   magnitude FFT. Analysed range 5–500 Hz; resolution 0.78 Hz.
3. **Window** around a target frequency f: |ν − f| ≤ max(0.02 f, 1.0 Hz). This allows for
   speed slip and resolution.

## Arms

| Id | Band signal | Selection |
|---|---|---|
| RAW | whole snapshot | – |
| **FK** (baseline) | Butterworth band-pass (order 4, zero phase) on the 1/3-binary kurtogram grid: levels 1, 1.6, 2, 2.6, 3, 3.6, 4, 4.6 (bandwidth ≥ 533 Hz). Bands with a lower edge below 500 Hz are excluded | maximum spectral kurtosis E\|c\|⁴/(E\|c\|²)² − 2 of the complex band signal |
| FK-RER (control) | same bands as FK | maximum RER |
| LMWT-K | Legendre multiwavelet, multiplicity r = 4, orthonormal, levels 1–4: the signal reconstructed from one level's detail coefficients | maximum spectral kurtosis |
| **LMWT-RER** (candidate) | as LMWT-K | maximum RER |
| EWT-K, EWT-RER | EWT (Gilles, 2013), 5 modes. Boundaries are midpoints between the 5 largest local maxima of the magnitude spectrum, smoothed over 64 bins; Meyer transitions; modes entirely below 500 Hz excluded | kurtosis / RER |

- **LMWT** (Alpert's construction):
  - The scaling filters H0, H1 are the exact two-scale relations of orthonormal Legendre
    polynomials of degree < 4 on [0, 1].
  - The wavelet filters G0, G1 span the orthogonal complement, so they have 4 vanishing
    moments.
  - Each 4-sample block is first mapped to discrete orthonormal polynomial coefficients
    (an orthogonal prefilter).
  - The transform is orthogonal and exactly invertible.
- **RER (relative energy ratio, our definition):** the sum, over BPFO, BPFI, BSF and FTF
  and their second harmonics, of the squared maximum envelope amplitude in each window,
  divided by the total envelope power over 5–500 Hz.
  - It uses the bearing's kinematics, which a practitioner always knows. It does **not**
    use which element failed.
- **What the controls isolate.** FK-RER gives the kurtogram the same frequency-aware
  selection as LMWT-RER, so a gain due to the selection rule is not credited to the
  multiwavelet. LMWT-K compares the transforms under the kurtosis rule.

## Measures

Per snapshot and documented element with characteristic frequency f_c:

- **Fault peak ratio** R = (largest envelope amplitude in the windows around f_c and
  2 f_c) / (median envelope amplitude over 5–500 Hz). Score = log10 R.
- **Detected** if one of the 5 highest envelope-spectrum peaks (peaks at least 1 Hz
  apart) lies in the window around f_c or 2 f_c.

Per bearing-element pair:
- the **score** is the median over its 10 snapshots;
- it is **detected** if detected in at least 6 of 10 snapshots.

## Primary comparison and decision (fixed now)

Δ = mean over the 19 bearing-element pairs of score(LMWT-RER) − score(FK). The 95% CI is
a bootstrap over **bearings** (all of a bearing's pairs resampled together; 2000
resamples, seed 7).

- **Niche** (the multiwavelet adds something for fault-frequency identification) only if
  all hold:
  1. Δ ≥ **0.10** (a 26% higher peak ratio) with CI lower bound > 0;
  2. LMWT-RER beats **FK-RER** with CI lower bound > 0, so the gain comes from the
     transform and not the selection rule;
  3. LMWT-RER detects at least as many pairs as FK.
- **Close** if the CI upper bound of Δ < 0.10, or if 1 holds but 2 fails. In the second
  case the report credits the selection rule, not the multiwavelet.
- **Inconclusive** otherwise. Report it and do not tune.

The compound bearings (1_5, 3_2; 6 pairs) are reported separately and descriptively; two
bearings cannot decide anything. EWT, RAW, LMWT-K and the vertical channel are also
descriptive.

## Not tunable

Snapshots (last 10), channel, multiplicity (4), levels (1–4), the kurtogram grid, filter
order, the 500 Hz exclusion, windows, the peak count (5), the detection majority (6/10),
EWT settings and the margin (0.10). Implementation errors found after the freeze are
fixed and logged; no parameter changes.

## Limitations

- Not the paper's exact implementation (see "Why").
- The kurtogram is a Butterworth filter bank on the 1/3-binary grid, not Antoni's
  quasi-analytic fast-kurtogram filters.
- One rig, accelerated life, nominal speed (no tachometer), and 15 bearings, of which 2
  are compound.
- End of life is a vibration-amplitude criterion. Late snapshots may be dominated by
  severe broadband damage, which affects all arms equally.

## Harness checks before the freeze (synthetic only)

`test_w2.py`: 12 tests.
- The fault frequencies match the dataset paper.
- LMWT is orthogonal, exactly invertible and energy preserving, with 4 vanishing moments; its levels run from high to low frequency.
- The kurtogram grid is correct (a level k.6 off-by-one was found and fixed here).
- EWT is a tight frame.
- On a simulated outer-race fault (bursts at BPFO exciting a 3 kHz resonance), FK, FK-RER and LMWT-RER all detect BPFO.

In a weaker version of the same signal (noise 1.5 instead of 0.5), the kurtogram's maximum moved to a noise band near Nyquist (spectral kurtosis 0.21 vs 0.17 at the resonance). That is the baseline's normal behaviour at low SNR, not a defect, and nothing was changed for it.

## Changes after freeze

None yet.
