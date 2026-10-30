# G1: does a gammatone filter bank expose bearing fault frequencies better than kurtogram bands?

Status: **FROZEN** in the commit that adds this line. The harness and its synthetic tests
are in the same commit. No CWRU envelope spectrum has been computed; only file names,
variable names and lengths were read. Changes after the freeze are logged under "Changes
after freeze".

## Why

The gammatone filter, a tone times a gamma envelope t^(n−1) e^(−2πbt), is the standard
model of the cochlea's filters. It is usually 4th order, with centres on the ERB scale
(Glasberg & Moore, 1990). Compared with Morlet, it has an **asymmetric, causal envelope**:
fast attack, slow decay.

That gives a physical argument for bearings. Each rolling-element impact rings a
structural resonance that decays roughly exponentially, so the gammatone's shape is
closer to a matched filter for those bursts than a symmetric band-pass.

Published work:
- In audio, gammatone filters are mainstream: speech cepstra and DCASE machine-sound
  anomaly detection.
- For bearings there are a few auditory-inspired pipelines:
  - RESAS (Scientific Reports, 2025);
  - a spiking-network anomaly detector;
  - an acoustic study (Potočnik et al., 2006).
- We found **no head-to-head comparison against kurtogram band selection**.

Project context:
- [W2](../multiwavelet/RESULTS.md) showed that a frequency-aware selection rule (RER), not
  the transform, gave the gain over the kurtogram.
- So the only comparison that can credit gammatone filters is one against the
  kurtogram's own bands **under the same rule**.

**Stated prior:** a tie. Across seven wavelet types the transform has not mattered. Also,
an auditory ERB band is narrow at low carriers (133 Hz at 1 kHz), which caps the envelope
bandwidth below 2×BPFI.

## Data

**CWRU Bearing Data Center**, 12 kHz drive-end set. 64 files downloaded on 2026-10-30 to
`data/cwru/raw`, with checksums in `data/cwru/SHA256SUMS`; not committed. The site states
no licence.

**Fault files: 60.** Seeded faults, each on one element:

| Element | Fault sizes (inch) | Positions |
|---|---|---|
| Inner race | 0.007, 0.014, 0.021, 0.028 | – |
| Ball | 0.007, 0.014, 0.021, 0.028 | – |
| Outer race | 0.007, 0.021 | @6, @3, @12 |
| Outer race | 0.014 | @6 only |

- Each fault condition is recorded at 4 motor loads: 0–3 HP, nominally 1797, 1772, 1750
  and 1730 rpm.
- That gives 15 fault conditions × 4 loads. The 4 loads of one condition use the same
  damaged bearing, so the **fault condition is the independent unit** (15 clusters).

**Normal files: 4** (97–100). Used only as a descriptive false-detection control.

**Signal used:**
- **Channel:** drive end (DE) is primary; fan end (FE) is descriptive. The 0.028" files
  (3001–3008) have no FE channel.
- **Length:** the first 120,000 samples (10 s) of every file.
  - 99.mat also contains file 98's variables; the reader takes the variables named after
    the file.
- **Speed:** the nominal rpm of the load, from the site's table. In-file RPM values are
  not used.

**Fault frequencies (SKF 6205-2RS)**, as multiples of shaft speed:

| Element | Multiple |
|---|---|
| BPFI (inner race) | 5.4152 |
| BPFO (outer race) | 3.5848 |
| BSF (ball) | 4.7135 |
| FTF (cage) | 0.39828 |

**Prior use:**
- Files 105, 106, 118, 119, 130 and 131 were used in the early run-separated retrieval
  baseline ([evaluation/README.md](../README.md)). That was a different task with
  different features; no envelope analysis was run.
- The rest of CWRU is unseen by this project.

**Known caveat:** Smith & Randall (MSSP 2015) found that many CWRU recordings, ball
faults especially, show no clear bearing signature with any standard method. This affects
all arms equally. Their classes are not used to select files.

**XJTU-SY (secondary, descriptive only):**
- The same new arms are run on W2's 150 snapshots, using W2's settings.
- **This is not blind:** W2's kurtogram results on these snapshots are already known.
  The XJTU-SY numbers cannot decide anything.

## Pipeline

The pipeline is identical to W2 (`w2.envelope_spectrum`, `w2.peak_ratio`, `w2.detected`,
`w2.rer`, `w2.spectral_kurtosis`):
- envelope spectrum over 5–500 Hz;
- window around a target frequency: ±max(2%, 1 Hz);
- peak ratio of the fault frequency (f_c or 2 f_c) to the spectrum's median;
- detected if one of the top 5 peaks is in the window.

On CWRU, each file is one recording, so "detected" is per file; there is no majority vote.

## Arms

**Filter-bank grids**
- **Kurtogram grid at 12 kHz:** levels 1, 1.6, 2, 2.6, 3 and 3.6, giving 29 bands of
  bandwidth ≥ 500 Hz with a lower edge ≥ 500 Hz.
  - Level 4 would give bands of 375 Hz, narrower than the 500 Hz analysis range.
  - At 25.6 kHz the grid is W2's.
- **ERB bank:** centres one ERB apart on the ERB-number scale, each filter within
  [500 Hz, fs/2] at ±½ ERB. That gives 18 filters at 12 kHz (627–5099 Hz) and 25 at
  25.6 kHz.

**Gammatone filters**
- Causal, 4th order, unit gain at the centre.
- The bandwidth parameter is b = ERB/0.98175: the equivalent rectangular bandwidth of a
  4th-order gammatone is 0.98175 b.
- Applied by convolution, with an impulse response 20/(2πb) long.

| Id | Band signals | Selection |
|---|---|---|
| RAW | whole recording | – |
| FK | Butterworth order 4, zero phase, on the kurtogram grid | max spectral kurtosis |
| **FK-RER** (comparator) | same as FK | max RER |
| GTFK-K | gammatone with the **same centre and equivalent bandwidth as each kurtogram band** | max spectral kurtosis |
| **GTFK-RER** (candidate) | same as GTFK-K | max RER |
| GTERB-K | gammatone on the auditory ERB bank | max spectral kurtosis |
| GTERB-RER (secondary candidate) | same as GTERB-K | max RER |

What the arms isolate:
- **GTFK vs FK** changes only the **filter shape** (asymmetric gammatone vs flat
  Butterworth), with the same centres, bandwidths and selection rule. This is the
  matched-filter hypothesis.
- **GTERB** is gammatone as used in hearing: auditory spacing and bandwidths.

RER selection uses BPFO, BPFI, BSF and FTF, as W2 defines it. It does not use the
documented fault element.

## Measures

- **Per file:** score = log10 peak ratio at the documented element's frequency; detected
  true or false.
- **Per arm:**
  - the mean score over the 60 fault files;
  - detections overall and by element.
- **Normal files:** the count of false detections of BPFO, BPFI or BSF (12 per arm).

## Primary comparison and decision (fixed now)

Δ = mean over the 60 DE fault files of score(GTFK-RER) − score(FK-RER). The 95% CI is a
bootstrap over the **15 fault conditions** (all 4 loads resampled together; 2000
resamples, seed 7).

- **Niche** (the gammatone shape adds something) only if all hold:
  1. Δ ≥ **0.10** with CI lower bound > 0;
  2. GTFK-RER detects at least as many files as FK-RER.
- **Close** if the CI upper bound < 0.10.
- **Inconclusive** otherwise. Report it and do not tune.

**Secondary:** GTERB-RER vs FK-RER under the same rule, reported as secondary.
- If only the secondary meets "niche", the report says so and claims nothing. It would
  need new data.

**Descriptive only:**
- the kurtosis-rule arms;
- RAW;
- the FE channel;
- the normal files;
- every XJTU-SY number.

## Not tunable

File set, first 10 s, nominal speeds, channel, grid levels, ERB spacing, filter order,
impulse-response length, windows, the peak count, the margin (0.10), and the cluster
definition.

Implementation errors found after the freeze are fixed and logged; no parameter changes.

## Limitations

- **Seeded faults on one test rig.**
- **The loads are not independent bearings:** 15 clusters, not 60 units.
- **Nominal speed only.**
- **The ERB bank is defined for hearing, not structural resonances.** The secondary arm
  tests gammatone as it is used, not an optimised variant.
- **The causal gammatone delays the signal by a few ms.** The envelope magnitude spectrum
  is insensitive to delay apart from the edge samples.

## Harness checks before the freeze (synthetic only)

`test_g1.py`: 11 tests.
- **Ground truth:** 60 files, 15 clusters; the 6 previously used files are in the set;
  the fault frequencies are correct.
- **Gammatone:** Glasberg–Moore ERB(1 kHz) = 132.64 Hz; the order-4 ERB factor is
  0.98175. The measured equivalent bandwidth and peak frequency match the request within
  3% and 2%. The envelope peaks at 3/(2πb).
- **Banks:** ERB spacing is exactly 1. The 12 kHz grid has every band ≥ 500 Hz, and the
  25.6 kHz grid equals W2's.
- **Pipeline:**
  - on a simulated outer-race fault at 12 kHz (3 kHz resonance), FK-RER, GTFK-RER and
    GTERB-RER all detect BPFO with a peak ratio > 5;
  - white noise gives no large BPFO peak.

## Changes after freeze

None yet.
