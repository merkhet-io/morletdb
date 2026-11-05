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
