<!-- Moved from the README on 2026-11-01; content unchanged except link paths. -->

# What has been measured

Every result names its data, baselines and limits. Each frozen evaluation fixes
its protocol before the data is read, runs once, and reports the result, including
negative ones.

- **Morlet features have not beaten simple baselines** in frozen, held-out tests:
  FFT band powers did as well on bearings, ECG and sleep EEG.
- **No wavelet or filter type has beaten a plain band-pass filter for bearing
  faults** (W1, W2, G1, F1). Eight families were tested, including Morlet, wavelet
  packets, Legendre multiwavelets, framelets and gammatone filters. What helped was
  choosing the band from the bearing's known fault frequencies, with any filter bank.
- **Comparing a machine with its own history has not warned earlier than a plain
  RMS alarm** on run-to-failure bearings.
- **What has held up** is the storage and retrieval machinery around raw recordings:
  - exact search;
  - comparison under the same operating context;
  - retrieval of similar labelled examples as an explanation;
  - access control;
  - compact lossless storage, 1.2–6× smaller than TimescaleDB compression on the
    same samples ([below](#why-an-extension-instead-of-postgresql--timescaledb--pgvector));
  - envelope search that tolerates time shifts, which pgvector's distances do not.

| Evaluation | Result |
|---|---|
| [Paderborn bearings](../evaluation/decision/results/e6bf274/RESULTS.md), frozen plan, held-out bearings | Morlet vs FFT bands + statistics: Δ P@10 −0.032 [−0.098, +0.036]. **Not supported.** |
| [Seismic timing test](../evaluation/decision/results/timing-012bb3d/RESULTS.md), frozen plan, 2024 held out | Time-resolved features help (+21 km). Temporal Morlet beats a time-binned spectrogram by only +1.6 km [+0.9, +2.3], below the 5 km margin. **Envelope cross-correlation beats temporal Morlet by 6.0 km.** |
| [PTB-XL ECG pilots](../research/multiscale_memory/) | Retrieved context does not improve a classifier, with fixed or learned retrievers. Closed. |
| [Raw storage](../evaluation/compression/README.md) | Lossless encoded chunks are 1.7–2.9× smaller than the previous `float8[]` chunks (4096-sample chunks), comparable to optimized Parquet (codec alone: 2% larger to 13% smaller). |
| [Storage vs TimescaleDB (P2)](../evaluation/plain_postgres/README.md), engineering benchmark, 4.2 M held-out samples per dataset | pg_morlet: 6.1–11.5 bits/sample, bit-exact. TimescaleDB 2.30 compression (default settings) is 24–35% larger on integer columns and 2.3–6.3× larger on float columns: noisy sensor floats grow to 70–72 bits/sample. |
| [Context-conditioned comparison (C1)](../evaluation/context/results/RESULTS.md), frozen plan, NASA milling, held-out insert sets | Deviation from same-setting history tracks flank wear far better than deviation from all history: ρ 0.91 vs 0.60, Δ +0.31 [+0.08, +0.54]. Time in use alone reaches 0.79. Available as the [context workflow](context-workflow.md). |
| [Own-history early warning (C5)](../evaluation/context/c5/RESULTS.md), frozen plan, Paderborn run-to-failure, 14 bearings | Same-context history vs a plain RMS alarm: Δ stable lead −0.047 [−0.195, +0.050], **not supported**. Same-context vs all history: +0.074 [+0.001, +0.181], **supported** (narrowly). |
| [Cross-condition resemblance (C6)](../evaluation/context/c6/RESULTS.md), frozen plan, same bearings | Late-life recordings resemble other operating conditions more often: +0.120 [+0.039, +0.193], **supported**, but only at the minimum of 5 bearings that passed the gate, and inconclusive on the second sensor. |
| [Pinned healthy baseline (C7)](../evaluation/context/c7/RESULTS.md), frozen plan, XJTU-SY, 15 bearings | Q1, pinned vs all history: +0.049 [−0.059, +0.196], inconclusive. Q2, vs RMS: +0.100 [+0.005, +0.242], **supported by the rule, but driven by alarms on vibration settling after run-in**. Counting only rising alarms (post hoc): +0.001. Fixes history absorbing a lasting rise on Paderborn B09 (exploratory). |
| [Level track (L1)](../evaluation/level/RESULTS.md), frozen plan, synthetic | On evenly spaced data it equals an exponential moving average. It adds uncertainty and 39% lower error after 48 h gaps. Intervals are overconfident with correlated noise (coverage 0.85). **Unclear**; kept as opt-in. |
| [pgvector integration (VR1)](../evaluation/vector/RESULTS.md), frozen plan, PTB-XL, 17,418 ECGs | Morlet features in pgvector rank like pg_morlet's own scorer (overlap 1.000). HNSW recall@10 is 0.9995–1.0 at about 9× the speed of a scan, and row-level security holds. A stand-in text vector missed the recall bar (0.90–0.92). No relevance claim. |
| [Sleep staging by retrieval (S1)](../evaluation/sleep/RESULTS.md), frozen plan, Sleep-EDF, 39 held-out subjects | Morlet vs FFT bands with the same layout: Δ kappa −0.006 [−0.015, +0.002], **not supported**. Staging by the 40 most similar expert-scored epochs vs logistic regression: +0.042 [+0.016, +0.068], **supported**: explanation by example costs no accuracy. Kappa is about 0.58 from one EEG channel. |
| [Materialized envelopes (R1)](../evaluation/materialized/results/eval-2025/RESULTS.md), frozen plan, 2025 held out | Stored envelopes return the same top-10 as decoding raw at 0.10× the p95 latency, but cost 27.9% of raw storage (bound 25%): **narrow**. The follow-up R1b (half precision, 2022 held out) gave the same results at 12.9% of raw storage: **continue**. Available as [materialized envelopes](durable-ingestion.md#materialized-envelopes). |
| [Envelopes in pgvector (P1)](../evaluation/plain_postgres/README.md), engineering benchmark, CX 2022 | `halfvec` stores the same envelopes in identical space. Its cosine search, with no time shift, shares only 65% of pg_morlet's top 10. Scoring with shifts in the client reproduces pg_morlet exactly, at 2× its latency. A zero-lag pgvector scan takes 0.48 ms. |
| [Read and search speed](../evaluation/materialized/compact/results/search-speed-2022.json), same 1,826 queries | Faster with identical results (same top 10, bitwise-equal distances): reading 4096 samples 2.9 → 1.1 ms, same-station envelope search 9.4 → 6.5 ms, unfiltered 38 → 27 ms. |
| [Probe search (BL1)](../evaluation/bounded_lag/RESULTS.md), frozen plans, CX 2025 development and CX 2021 held out (unread stations and year), about 235k windows | 13 shifted inner-product probes through pgvector HNSW, then exact re-ranking: recall@10 0.976 held out, p95 131 ms against 2,854 ms for the exact search. Maintained in the append transaction during replayed ingestion. Available as [probe search](durable-ingestion.md#probe-search-opt-in-needs-pgvector) (opt-in, approximate). |
| [CWRU bearings](../evaluation/README.md), run-separated, early slice | Pooled Morlet **ties** the statistical baseline. A reproducible baseline, not a win. |
| [Wavelet early warning (W1)](../evaluation/wavelets/results/eval/RESULTS.md), frozen plan, FEMTO run-to-failure | Morlet spectral kurtosis vs the STFT kurtogram: Δ stable lead −0.056 [−0.168, 0.000]. **Close.** |
| [Legendre multiwavelet (W2)](../evaluation/multiwavelet/RESULTS.md), frozen plan, XJTU-SY, 15 bearings | Beats the kurtogram (+0.363 [+0.234, +0.522] log10 fault-peak ratio), but the kurtogram's own bands with the same frequency-aware band choice do better (−0.185 [−0.364, −0.018]). **Close:** the gain is the selection rule, not the transform. |
| [Gammatone filters (G1)](../evaluation/gammatone/RESULTS.md), frozen plan, CWRU, 15 fault conditions | Gammatone vs Butterworth with the same bands and selection: −0.021 [−0.071, +0.025], **close**. The auditory ERB bank's higher score is an artefact of the measure, and it detects fewer faults (40 vs 46 of 60). Frequency-aware selection raises false alarms on 3–4 of 12 healthy checks. |
| [Framelets and multiwavelets on sleep (F1)](../evaluation/framelets/RESULTS.md), screen, development subjects | Kappa: FFT bands 0.564, Morlet 0.568, B-spline framelet 0.523, GHM multiwavelet 0.553. **Nothing advances.** |
| [Seismic replay](../evaluation/reports/seismic-cx-north-chile-2024-v2.md), 3 stations, early slice | Real ingestion of about 324 M samples with gaps, duplicates and resume. No simple-feature baseline was run, so it says nothing about Morlet vs simple features. |
| [Wave-index PoC](wave-index-poc.md), synthetic | Temporal cells separate a burst from its time reversal, which pooled features cannot (distance 1.18 vs ≈0). Posting lists traverse most of the index. |
| [Benchmarks](../benchmarks/README.md) | Ingestion, extraction and retrieval are measured separately. A 10,000-candidate CI smoke checks ranking against an independent oracle; it is not a capacity claim. |

Reports record parameters and source hashes so they can be rerun; the seismic
report also records the code revision and database it ran on. The wave-index
experiment is in memory, not a PostgreSQL index, and needs a source checkout
rather than a release image; its next steps are in the
[advanced retrieval design](advanced-retrieval.md).

## Why an extension instead of PostgreSQL + TimescaleDB + pgvector?

Could the same be had with existing PostgreSQL extensions? Bits per sample on the same 4.2 million
held-out samples per dataset, every layout read back bit-exact
([P2](../evaluation/plain_postgres/README.md); lower is better):

| Data | pg_morlet | TimescaleDB, integer column | TimescaleDB, float column |
|---|---|---|---|
| Seismic counts (IPOC CX) | **6.1** | 8.3 | 14.3 |
| Bearing vibration, 3 decimals (IMS) | **11.4** | 14.1 | 72.0 |
| Bearing ADC counts (Paderborn) | **10.0** | 12.9 | 24.6 |
| Bearing physical floats (Paderborn) | **11.5** | – | 70.1 |

- **Floats:** TimescaleDB's float compression suits slowly changing values. Noisy
  vibration samples come out larger than raw 64-bit floats. pg_morlet stores them
  losslessly by fitting the sensor's value grid.
- **Integers:** TimescaleDB comes within 24–35%, after the samples have been
  converted to integer counts.
- **Envelopes:** pgvector's `halfvec` stores them in the same space
  ([P1](../evaluation/plain_postgres/README.md)). What it lacks is matching that
  tolerates time shifts.
- **Speed:** this is not a speed claim. When time shifts are not needed, pgvector's
  zero-lag scan is about 14× faster: 0.49 vs 6.8 ms median, both in one run on
  `main` after the search speed-up
  ([P1 re-run](../evaluation/plain_postgres/README.md#p1-re-run-after-the-search-speed-up)).
  P1's original 20× was measured before that speed-up. The two searches have
  different semantics (zero-lag cosine vs lag-tolerant cross-correlation), so this is
  neither a like-for-like comparison nor evidence about relevance.

One machine (Apple M5 Max), TimescaleDB 2.30.2 with default compression settings
(`segmentby` recording, `orderby` sample index). Other layouts were not tried.

For the lag-tolerant retrieval results, see the [technical report](../research/paper/morletdb.pdf) and [E1](../evaluation/consolidated/RESULTS.md).
