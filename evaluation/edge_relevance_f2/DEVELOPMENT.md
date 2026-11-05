# F2 development (not frozen; development data only)

Harness [`f2_dev.py`](f2_dev.py), method [`shrink.py`](shrink.py), results in [`results/dev/`](results/dev/).
Data: CWRU 12 kHz drive end (normal and 0.007–0.021 in inner, ball and outer faults, loads 0–3), PyScrew s01
(5,000 cycles, 911 NOK), CX gallery 2025-03 with the EMSC catalogue (16 station-days for relevance, 94 of 967
ten-minute episodes contain a predicted arrival; 4 station-days for approximation windows).

## Round 1 (`f2_dev_20261105T212710.json`): what it changed

1. **The prior cannot be fitted on quiet background.** Fitted on noise alone, every slab collapsed to its smallest
   weight and the approximation removed everything. sigma now comes from the robust finest-level MAD, and p_j, s_j
   are fitted on the recording itself (on a device: a rolling window).
2. **Fidelity ratios must be two-sided.** Taking the largest ratio of a diagnostic measure rewarded reconstructions
   that inflate the fault peak (ratios of 3–4.5 on CWRU). The measure is now |log(reconstruction / original)|.
3. **Shrinkage biases an archive.** The posterior mean shrinks the coefficients it keeps; as a code that is error,
   not denoising. Added `select`: the posterior selects, the observed value is stored.
4. **Baselines needed wider grids** (ZFP never reached 0.3 bytes/sample) and SZ3 was added (hdf5plugin 7.1.0).

## Round 2 (`f2_dev_20261105T213638.json`)

Best value within each size (bytes per sample; entropy estimates for the wavelet methods, actual sizes for SZ3/ZFP):

| CWRU, relative error | 0.05 | 0.1 | 0.2 | 0.3 |
|---|---:|---:|---:|---:|
| select, Pareto slab | 0.57 | 0.42 | 0.21 | 0.21 |
| select, Laplace slab | 0.58 | 0.42 | 0.22 | 0.22 |
| hard threshold | 0.59 | 0.34 | 0.21 | 0.20 |
| SZ3 | 1.00 | 0.41 | 0.41 | 0.25 |
| ZFP | 1.00 | 0.97 | 0.83 | 0.58 |
| decimation | 0.93 | 0.81 | 0.75 | 0.75 |

| Seismic windows, relative error | 0.1 | 0.2 | 0.3 |
|---|---:|---:|---:|
| select, Pareto slab | – | 0.006 | 0.005 |
| hard threshold | 0.010 | 0.007 | 0.006 |
| SZ3 | 0.032 | 0.015 | 0.006 |
| decimation | 0.027 | 0.018 | 0.012 |

| AUROC, label-free | CWRU fault | PyScrew NOK | Seismic event |
|---|---:|---:|---:|
| relevance R (any slab) | 0.988 | 0.879 | 0.855 |
| RMS | **1.000** | 0.052 (inverted 0.948) | 0.806 |
| kurtosis | 0.914 | **0.937** | **0.880** |
| crest factor / STA/LTA | 0.925 | – | 0.742 |

Reading:
- **H1:** wavelet thresholding beats SZ3, ZFP and decimation on relative error at 0.1–0.3 bytes/sample on both
  datasets; but posterior selection is no better than a hard threshold. The envelope fault peak survives every method
  (deviation ≤ 0.02 at 0.2 bytes/sample), so on CWRU it does not discriminate; a harder bearing measure is needed
  (Paderborn, real damage). On seismic event windows the wavelet methods never got below 0.3 bytes/sample with the
  current quantisation grid, so the STA/LTA comparison is still missing.
- **H2:** the slab makes no difference to relevance (AUROC identical to three decimals) and at most a few hundredths
  in error. H2 is likely to be refuted.
- **H3:** relevance is well above 0.70 everywhere, but kurtosis is better on PyScrew and seismic data, and RMS is
  perfect on CWRU (faulty recordings are simply louder there). As specified, "not worse than simple label-free
  scores" fails on development data.
- **H4:** on the Mac, approximation and scoring run at about 6.7 M samples/s on one core (100× real time at 64 kHz),
  the prior fit 1.9 s per hour of 100 Hz data. The Pi remains to be measured.

Open: a coarser quantisation grid for seismic windows; Paderborn; an implemented coder; whether R adds anything to
kurtosis in combination (a question the protocol would have to state before confirmation, not a rescue after it).

## Scope change (owner, 2026-11-05): edge approximation only

The relevance score is dropped (it was not better than kurtosis). The harness is now [`f2_edge.py`](f2_edge.py):
every method writes actual bytes, and rate control picks, per window, the setting with the smallest relative error
within the budget, as a device can. Windows: 1 s (CWRU), one 1 s file (IMS), 2^15 samples (seismic), one cycle
(PyScrew, 1,000 cycles).

### Edge rounds 1–2 (`f2_edge_20261105T221820.json`, `…T222852.json`): what they changed

1. **A denoiser's threshold is wrong for a coder.** Thresholds from a noise scale per level (λσ_j) treated the
   seismic microseism as noise and removed most of the signal (error 0.66 at 0.05 bytes/sample, SZ3 0.11). The coder
   is now a transform coder: one absolute threshold T = c·std(x) for all levels, step q·T (`shrink.encode_abs`).
2. **STA/LTA needs a band-pass.** On the raw trace the original itself gave over 6,000 false triggers in 8
   station-days. Now 2–20 Hz, 1 s / 30 s, on 8 (chosen on development data: the original then triggers on 28 of 30
   catalogued arrivals, with 400 triggers elsewhere, many of them presumably uncatalogued small events).
3. **The grid must reach the budget for broadband vibration** (c up to 5, q up to 2).

### Edge round 3 (`f2_edge_20261105T224107.json`)

Median relative error at each budget (bytes per sample, actual sizes):

| Data | Method | 0.05 | 0.1 | 0.2 | 0.3 |
|---|---|---:|---:|---:|---:|
| CWRU | **wavelet** | **0.67** | **0.56** | **0.40** | **0.23** |
| | SZ3 | 1.00 | 0.93 | 0.59 | 0.33 |
| | decimation | 0.92 | 0.86 | 0.83 | 0.82 |
| | wavelet, Bayesian selection | 0.81 | 0.57 | 0.45 | 0.36 |
| IMS set 2 | **wavelet** | 0.88 | **0.70** | **0.53** | **0.43** |
| | SZ3 | 1.00 | 0.95 | 0.55 | 0.54 |
| | decimation | **0.87** | 0.74 | 0.56 | 0.50 |
| Seismic | **wavelet** | **0.071** | **0.038** | **0.023** | **0.017** |
| | SZ3 | 0.113 | 0.063 | 0.035 | 0.018 |
| | decimation | 0.136 | 0.079 | 0.073 | 0.073 |
| PyScrew | wavelet | 0.32* | 0.090 | **0.047** | **0.034** |
| | decimation | 0.23* | **0.086** | **0.047** | 0.040 |
| | SZ3 | – | – | 1.35* | 0.55 |

\* budget not reachable for some windows. ZFP's 1-D error-bound mode never came close (relative error ≈ 1).

Diagnosis on the approximation:

| | Original | Wavelet 0.05 / 0.1 / 0.3 | SZ3 0.05 / 0.1 / 0.3 | Decimation 0.05 / 0.1 / 0.3 |
|---|---|---|---|---|
| IMS: file at which the outer-race fault is detected | 532 | **532 / 532 / 532** | 704 / 549 / 532 | 700 / 539 / 532 |
| Seismic: arrivals triggered (of 30) | 28 | 30 / 29 / 29 | 24 / 26 / 28 | 24 / 26 / 28 |
| Seismic: triggers elsewhere | 400 | 2,408 / 955 / 447 | 911 / 393 / 365 | 615 / 556 / 606 |
| PyScrew: AUROC of kurtosis for NOK | 0.961 | 0.859* / 0.957 / 0.961 | – / – / 0.916* | 0.483* / 0.954 / 0.961 |

Reading against the draft hypotheses:
- **H1 (error per byte):** the wavelet coder has the lowest error at every budget on CWRU and seismic data, at
  0.1–0.3 on IMS, and ties decimation on short screwdriving cycles (where a 400–700-sample episode leaves little to
  code). SZ3 is never better below 0.3; ZFP is not competitive in 1-D.
- **H2 (diagnosis at 0.1):** bearing detection is unchanged (SZ3 is 17 files late, decimation 7); kurtosis on
  cycles stays within 0.004; seismic arrivals are all still found, but **false triggers rise 2.4×** (955 vs 400):
  thresholding leaves quantisation artefacts that STA/LTA reads as onsets. As specified, H2 fails on the seismic
  false-trigger criterion at 0.1 and passes at 0.3 (+12%).
- **H4 (no Bayesian machinery):** posterior selection is worse than the plain threshold (0.57 vs 0.56 at 0.1, 0.45 vs
  0.40 at 0.2 on CWRU) and slower. It is not needed.
- **H3 (Pi):** the encoder runs at about 22 M samples/s on one laptop core (350× real time at 64 kHz); the Pi is
  still to be measured.

Open before freezing: the false-trigger artefact (a candidate fix to test on development data: dithered or
noise-shaped quantisation, or keeping the coarsest levels exactly); whether H2's seismic false-trigger threshold is
the right criterion; Pi access; the confirmation datasets' definitions.
