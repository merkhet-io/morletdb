# Paper 3: related work notes

Literature check for the paper 3 idea, done on 2026-11-05. The idea has three parts: an orthogonal DWT of waveform episodes; a spike-and-slab prior whose slab is heavy-tailed (Pareto or generalized Pareto type), with the noise level estimated per recording or context; and two uses of the posterior. The first use is a lossy "approximated" representation at about 0.05-0.3 bytes/sample. The second is a label-free surprise or relevance score that decides what to send or keep exactly on the device and that weights a forgetting policy.

All 45 entries in `related.bib` were checked against the DOI registry (Crossref metadata or the doi.org resolver), the publisher or proceedings page, or arXiv. Entries without a DOI (rivoirard2005, cevher2009, banbury2021, koizumi2020) carry the official URL. Abstract-level claims were read from the publisher, arXiv or author page. Where a note goes beyond what was read in the abstract or full text, it says so.

## A. Bayesian wavelet shrinkage (foundations and baselines)

- **abramovich1998**: Introduced the spike-and-slab prior on wavelet coefficients (point mass plus Gaussian, with level-dependent mixing weight) and the posterior-median "BayesThresh" rule. This is the direct ancestor of our model; we replace the Gaussian slab with a heavy-tailed one. Prior work and baseline.
- **chipman1997**: Adaptive Bayesian wavelet shrinkage with a two-Gaussian mixture (narrow and wide) and posterior inclusion probabilities. It is an early use of per-coefficient posterior "signal" probabilities, the quantity our surprise score sums. Prior work and baseline.
- **vidakovic1998**: Bayes rules and Bayes factors for wavelet shrinkage, with heavy-ish priors (t / double-exponential families) on coefficients. Prior work.
- **vidakovic1999**: Book (Statistical Modeling by Wavelets, Wiley), the standard reference for Bayesian wavelet modelling, including BAMS-style shrinkage. Background.
- **johnstone2004**: Empirical Bayes for possibly sparse sequences. Mixing weight estimated by marginal maximum likelihood; slab is Laplace or quasi-Cauchy (heavy-tailed). Posterior median is adaptive-minimax over many sparsity classes. This is the closest established "heavy-tailed slab" method and our main statistical baseline.
- **johnstone2005**: The same empirical Bayes approach applied level by level to wavelet thresholds. Baseline for the per-level weights.
- **johnstone2005ebayes**: EbayesThresh R software (JSS). Reference implementation for the baseline.
- **clyde2000**: Flexible empirical Bayes for wavelets, including heavy-tailed (scale-mixture) slabs and noise variance estimated within the model. Prior work on combining noise estimation with spike-and-slab.
- **donoho1994**: VisuShrink, the universal threshold sigma*sqrt(2 log n) with MAD noise estimate. Non-Bayesian baseline; also the standard per-recording noise estimator.
- **donoho1995**: SureShrink, a level-wise SURE threshold. Non-Bayesian baseline.
- **chang2000**: BayesShrink: generalized-Gaussian prior per subband giving a closed-form soft threshold, explicitly tied to compression (MDL-style). This is the closest classical work linking Bayesian shrinkage to compression. Baseline for the "approximated" representation.
- **mallat2009**: A Wavelet Tour of Signal Processing (3rd ed.). Reference for orthogonal DWT, nonlinear approximation, and transform coding.
- **daubechies1992**: Ten Lectures on Wavelets. Reference for compactly supported orthogonal wavelets.
- **johnstone1997**: Wavelet thresholds under correlated noise, with level-dependent noise estimation. Supports estimating noise per level or context rather than once globally.
- **mitchell1988**: Origin of the "spike-and-slab" prior for Bayesian variable selection. Background.

## B. Heavy-tailed / Pareto-type priors in shrinkage

- **rivoirard2004**: "Thresholding procedure with priors based on Pareto distributions" (TEST 13(1)). Puts a Pareto-based prior on wavelet coefficients, links the prior parameters to weak Besov spaces, and derives level-dependent thresholds. Comparisons include VisuShrink, SureShrink and Bayesian rules (per the indexed abstract; full text was not reachable). **This is the direct prior work for a Pareto-type prior in wavelet thresholding.** Its motivation is minimax and maxiset theory; it does not address compression, edge use or scoring.
- **rivoirard2005**: Bayesian modelling of sparse sequences and maxisets for Bayes rules (Math. Methods Stat. 14(3)). The full text, read on the author page, defines the model as a mixture of a point mass at zero and a heavy-tailed density gamma. It reports that heavy tails are not needed to reach maxisets equivalent to thresholding. This is spike plus heavy-tailed slab, studied theoretically. Prior work and a caution: heavy tails may not buy much in estimation risk.
- **armagan2013**: Generalized double Pareto (GDP) shrinkage prior. Continuous (not spike-and-slab), with a Laplace-like peak and Pareto-like tails, and a closed-form thresholding interpretation. Applied to regression, not wavelets. It is the nearest named "Pareto" shrinkage prior and a candidate continuous alternative to our slab.
- **carvalho2010**: Horseshoe prior, a continuous global-local shrinkage prior with Cauchy-like tails. Its shrinkage weights act like inclusion probabilities. Contrast: a continuous alternative to spike-and-slab.
- **rockova2018**: Spike-and-slab LASSO (two Laplace components). Modern spike-and-slab with fast MAP computation. Contrast and computation reference.
- **achim2003**: Bayesian wavelet shrinkage for SAR despeckling with alpha-stable (heavy-tailed) coefficient priors. Note: the 2001 Achim et al. paper in IEEE TMI is about ultrasound speckle; the SAR paper is this 2003 TGRS one. Prior work on heavy-tailed wavelet priors, for images.
- **boubchir2006**: Closed-form Bayesian wavelet estimator using an approximate alpha-stable prior (images). Prior work on heavy-tailed priors with tractable posteriors.
- **figueiredo2001**: Empirical Bayes wavelet estimation with Jeffreys' noninformative prior, giving a parameter-free, heavy-tailed (improper) shrinkage rule. Contrast: a heavy-tailed rule with no hyperparameters.
- **cevher2009**: "Learning with compressible priors" (NIPS 2009). Shows that iid draws from generalized Pareto, Student-t, Frechet, log-logistic and similar distributions are compressible, while generalized Gaussians are not in general. Reports wavelet coefficients of natural images as about 1.67-compressible. **Theoretical support for using a GPD-type slab when the goal is compression.** Used in compressive sensing, not in wavelet shrinkage or spike-and-slab.

## C. Wavelets and diagnostics in vibration condition monitoring

- **randall2011**: Rolling element bearing diagnostics tutorial (envelope analysis, cepstral prewhitening, spectral kurtosis). Domain reference: which signal content in bearing faults must survive the lossy representation.
- **antoni2006**: Spectral kurtosis for non-stationary, impulsive signals. A label-free impulsiveness measure and a contrast or baseline for our surprise score.
- **peng2004**: Review of wavelet transforms in machine condition monitoring and fault diagnostics. Background for DWT on vibration.
- **staszewski1998**: Wavelet-based compression and feature selection for vibration analysis. **Closest early prior work on compressing vibration by keeping a few wavelet coefficients**, using thresholding rather than a Bayesian model.

## D. Error-bounded lossy compression (baselines)

- **di2016**: SZ, a prediction-based error-bounded lossy compressor. Baseline family.
- **liang2023**: SZ3, a modular framework for prediction-based error-bounded compressors. Baseline to run at matched bytes/sample.
- **lindstrom2014**: ZFP, a fixed-rate compressed floating-point array format. Baseline with fixed rate (directly comparable at a set bytes/sample).
- **chandak2020**: LFZip, error-bounded lossy compression of multivariate floating-point time series with prediction and quantization. Time-series-specific baseline.
- **munizcuza2024**: EDBT 2024 evaluation of how error-bounded lossy compression (PMC, Swing, Squeeze) affects downstream forecasting. Methodological precedent for judging a lossy representation by its downstream task, not by error alone.

## E. Edge, selective transmission, compressive sensing, TinyML

- **xu2004**: Wisden wireless structural monitoring network on Mica-2 motes; studies wavelet-based compression to fit low-power radio bandwidth. **Closest systems prior work (wavelet compression on the sensor node for vibration-type data)**, with no Bayesian model and no relevance score.
- **miskowicz2006**: Send-on-delta event-based reporting. Classic data-reduction rule at the edge. Simple baseline for "send only when something changed".
- **bao2011**: Compressive sampling of accelerometer signals for SHM. Contrast: reduces data at acquisition using random projections, not by keeping selected coefficients.
- **ji2008**: Bayesian compressive sensing (sparse Bayesian learning with posterior error bars). Prior work on posterior uncertainty steering what is measured or sent; a different problem (reconstruction from projections).
- **michala2022**: Vibration edge computing in maritime IoT. Edge features (trend, FFT, time-domain) cut data volume by about three orders of magnitude while keeping decision support. Contrast: keeps features, not a reconstructable waveform.
- **pandey2025**: Case study on compressing vibration data for distributed wireless condition monitoring (DCT-based, splitting regular and outlier coefficients). Names on-node novelty detection as a way to cut data further. **Closest recent work**: an open direction it names is what our score does.
- **banbury2021**: MLPerf Tiny benchmark, which includes an anomaly-detection task on microcontrollers. Reference for edge compute budgets and TinyML anomaly detection as a learned alternative.

## F. Label-free relevance and anomaly scoring

- **pimentel2014**: Review of novelty detection. Places our score among probabilistic and distance-based novelty methods.
- **koizumi2020**: DCASE 2020 Task 2, unsupervised anomalous sound detection for machine condition monitoring (training on normal data only). Label-free CM benchmark; contrast because it is learned and supervised by "normal-only" data rather than a per-recording prior.
- **allen1978**: STA/LTA automatic earthquake detection. Label-free baseline for the seismic case.
- **withers1998**: Comparison of seismic trigger algorithms (STA/LTA and others). Baseline settings and evaluation for label-free triggers.
- **itti2009**: Bayesian surprise (KL divergence between posterior and prior) attracts human attention. Conceptual precedent for "surprise" as a posterior-based relevance measure. Our score is a simpler energy-share statistic and should be named carefully, so readers do not take it for KL surprise.

## Unverified / left out of the .bib

- Vidakovic & Ruggeri, "BAMS method: theory and simulations" (Sankhya B, 2001). Known from memory, but no DOI or official page was opened. **UNVERIFIED**, not in the bib. vidakovic1999 is cited instead.
- Rivoirard 2004: only the bibliographic record and indexed abstract were read (HAL full text blocked automated access). Whether the TEST paper's prior is a Dirac plus Pareto mixture or a pure Pareto-based prior is inferred from rivoirard2005's model (M1), which was read in full, and from the abstract. Read the TEST full text before stating the exact form in the paper.
- Several verified but lower-priority items were dropped to keep 45 entries. They can be added back:
  - Donoho 1995 (doi:10.1109/18.382009)
  - Rivoirard 2006 (Bernoulli 12(4), doi:10.3150/bj/1155735929)
  - Pensky 2006 (Ann. Stat., doi:10.1214/009053606000000128)
  - Fadili & Boubchir 2005 (doi:10.1109/TIP.2004.840704)
  - Achim et al. 2001 (doi:10.1109/42.938245)
  - Sousa et al. 2021 beta priors (doi:10.1007/s00180-020-01048-1)
  - Antoniadis, Bigot & Sapatinas 2001 (doi:10.18637/jss.v006.i06)
  - Antoni 2007 kurtogram (doi:10.1016/j.ymssp.2005.12.002)
  - Yan et al. 2014 (doi:10.1016/j.sigpro.2013.04.015)
  - Smith & Randall 2015 CWRU (doi:10.1016/j.ymssp.2015.04.021)
  - Candes & Wakin 2008 (doi:10.1109/MSP.2007.914731)
  - Shi et al. 2016 (doi:10.1109/JIOT.2016.2579198)
  - Huang et al. 2014 robust Bayesian CS for SHM (doi:10.1111/mice.12051)
  - Chandola et al. 2009 (doi:10.1145/1541880.1541882)
  - Lei et al. 2020 (doi:10.1016/j.ymssp.2019.106587)
  - Lynch 2006 wireless SHM review (doi:10.1177/0583102406061499)
  - Razafimandimby et al. 2018 Bayesian IoT data reduction (doi:10.1007/978-3-319-93797-7_1)
  - Chiarot & Silvestri 2023 time series compression survey (doi:10.1145/3560814)

## Is the combination new?

**(a) Pareto or heavy-tailed priors in wavelet shrinkage: done before.** Heavy-tailed slabs are standard:
- quasi-Cauchy and Laplace slabs (johnstone2004, johnstone2005)
- scale mixtures (clyde2000)
- alpha-stable priors (achim2003, boubchir2006)
- Jeffreys priors (figueiredo2001)

A **Pareto-based prior on wavelet coefficients has been published by Rivoirard (rivoirard2004, TEST 2004)**, with the spike-plus-heavy-tailed-slab model analysed in rivoirard2005. Generalized-double-Pareto shrinkage (armagan2013) exists outside wavelets. Cevher (cevher2009) argues that GPD-type priors are the "compressible" ones. So we cannot claim the Pareto slab as new. What we found no evidence of is:
- a **generalized Pareto** slab inside a point-mass mixture applied to DWT coefficients of sensor waveforms
- that model used as a **rate-targeted lossy code** (bytes/sample) instead of as a denoiser.

Claim it as an application or design choice, motivated by rivoirard2004 and cevher2009, not as a new prior.

**(b) A posterior-based surprise score as label-free importance for retention or transmission in condition monitoring: no direct precedent found.** The related pieces exist separately:
- posterior inclusion probabilities (chipman1997, abramovich1998)
- label-free impulsiveness and novelty measures (antoni2006, pimentel2014)
- STA/LTA triggers (allen1978, withers1998)
- send-on-delta and event triggers (miskowicz2006)
- edge data reduction (michala2022, pandey2025)
- Bayesian surprise as a concept (itti2009)

No paper we found uses the posterior mass or energy of spike-and-slab wavelet coefficients, relative to a per-recording background, as the score that decides which episodes are transmitted or kept exactly, or as the importance term in a forgetting or retention policy. pandey2025 names on-node novelty detection as future work, without wavelets or a Bayesian model. This part is new as far as this search reaches. The search covered Crossref, publisher pages and web search, but not a full systematic review, so phrase the claim as "to our knowledge".

**(c) Closest prior work to the combined idea:**
1. rivoirard2004 / rivoirard2005: Pareto-based / heavy-tailed spike-and-slab on wavelet coefficients (the statistics, without compression or scoring).
2. johnstone2004 / johnstone2005: empirical Bayes spike plus heavy-tailed (quasi-Cauchy) slab with level-wise weights. The practical method to beat.
3. chang2000: Bayesian (generalized-Gaussian) wavelet shrinkage tied directly to compression.
4. staszewski1998 and xu2004: wavelet-coefficient compression of vibration data, offline (staszewski1998) and on the wireless sensor node (xu2004), with no Bayesian model and no relevance score.
5. pandey2025: on-node compression for wireless vibration condition monitoring, with on-node novelty detection named as the next step to cut transmitted data.

Honourable mention: chipman1997, the posterior "signal" probabilities per wavelet coefficient that our score aggregates.
