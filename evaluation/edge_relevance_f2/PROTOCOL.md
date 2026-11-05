# F2: keeping the spikes — approximation for the edge and label-free relevance (DRAFT, not frozen)

Status: draft. Development runs on development data only ([`DEVELOPMENT.md`](DEVELOPMENT.md)); this file is frozen,
with its title changed to FROZEN, in a commit before any confirmation data is loaded. Report: [paper 3](../../research/paper3).

## Question

Machine waveforms are mostly stationary background with rare, short, large transients. With an orthogonal wavelet
transform and an empirical-Bayes spike-and-slab prior whose slab is heavy-tailed, can a device (1) keep an
approximation at a tenth of the lossless size that still carries what diagnosis needs, and (2) score each episode by
how much real signal it holds, without labels, well enough to decide what is sent or kept exactly?

## Method under test

[`shrink.py`](shrink.py): DWT (sym8; 6 levels for vibration and seismic windows, 4 for screwdriving cycles); noise
sigma from the finest level (median absolute deviation / 0.6745) of a quiet reference or of the recording; per level
a spike at zero plus a slab, Gaussian, Laplace or symmetric Pareto (Lomax, alpha 1.5), fitted by marginal maximum
likelihood on the recording or a window of it. Approximation: coefficients with posterior probability of signal
above tau, posterior mean quantised with step q·sigma, coarsest band always kept; size is an entropy estimate of
significance map and values (to be replaced by an implemented coder before freezing). Surprise of an episode: sum of
pi·(z² − 1)+ over its detail coefficients per sample.

Prior work (paper 3, `RELATED_NOTES.md`): heavy-tailed and Pareto-based priors for wavelet thresholding are not new
(Rivoirard 2004, 2005; Johnstone and Silverman's quasi-Cauchy slab); the Pareto slab is a design choice tested by H2,
not a contribution. What we found no precedent for is the posterior used as a label-free relevance score for what a
device sends or keeps, and as importance in forgetting (H3, H4). The practical baseline for the prior is the
empirical-Bayes quasi-Cauchy slab (EbayesThresh), to be added before freezing.

## Hypotheses

- **H1 (fidelity at small size).** At 0.05–0.3 bytes per sample, the Bayesian approximation keeps more of what
  diagnosis needs than hard thresholding, SZ3 and ZFP at an absolute error bound, and decimation, at the same size.
  Measures: bearings, the fault-order peak in the envelope spectrum over its value on the original; seismic, the
  STA/LTA maximum around catalogued arrivals over its value on the original; all, relative error. Refuted if SZ3 or
  ZFP is as good or better on two of three measures.
- **H2 (the heavy tail matters).** At the same size, the Pareto slab beats the Gaussian and Laplace slabs on the H1
  measures. Refuted if no difference within the interval.
- **H3 (relevance without labels).** The surprise score ranks known-event episodes above others with AUROC ≥ 0.70 on
  every dataset, and is not worse than simple label-free scores (RMS, kurtosis, crest factor, STA/LTA). As the
  importance term in F1's setup (no labels), it keeps more known events searchable than recency at 50% and 25% and is
  not worse on rare queries. Refuted if either part fails on any dataset.
- **H4 (edge).** On a Raspberry Pi 3B+ (as EX2), approximation and scoring keep up with 64 kHz in real time on one
  core; sending the approximation continuously plus exact samples for the top-surprise episodes delivers at least
  80% of known events exactly at 10% of lossless bandwidth. Refuted if either fails.

## Data

Development: CWRU 12 kHz drive end, PyScrew s01, the CX gallery of 2025-03 with the EMSC catalogue.
Confirmation (proposed): Paderborn bearings (artificial and real damage), PyScrew s02, the CX held-out gallery of
2021-03 with its catalogue. s02 and the 2021 gallery were F1's confirmation data; the surprise score was never
developed on them, and the reuse is disclosed.

## Open before freezing

Wavelet and levels per data type; the size grid and the matched-size rule; whether sigma comes from a reference or
the recording; the importance scaling in the F1 arm; the Paderborn episode and label definition; an implemented
coder instead of entropy estimates; the bootstrap groups.

## Deviations

(none; not frozen)
