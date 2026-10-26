# Protocol: multiscale external memory for retrieval-augmented signal models

Status: **pilot protocol, frozen before held-out evaluation** (frozen in the commit that
adds this file; any later change is listed in the changelog at the bottom with its reason).
Scope: a bounded research module in `research/multiscale_memory/`. Nothing here changes the
MorletDB extension, the Rust core or the frozen Morlet extractor and its fixtures.

## Decision taken for this pilot

The track mixes three hypotheses with different consumers. We test the **model-side
question first (H2, with H1 as a by-product)** because it does not depend on MorletDB and
a negative answer makes the systems question (H3) moot. H3 is deferred to a separate
study that runs only if H2 shows that representation matters.

## Research questions and hypotheses

- **H1 (retrieval helps).** A model that receives retrieved historical examples
  outperforms an otherwise matched model without retrieval.
  *Falsified in this pilot if* the retrieved-context model does not beat the
  no-retrieval model with a patient-level 95% interval excluding zero.
- **H2 (multiscale context helps at a fixed budget).** At an equal number of scalars
  delivered per retrieved example, coarse wavelet context plus selective refinement gives
  better task quality than (a) budget-matched raw-window context and (b) fixed-resolution
  wavelet context.
  *Falsified in this pilot if* coarse+refine does not beat both comparators at the
  pre-registered budget (64 scalars), or if retrieved signal content adds nothing over
  retrieved labels alone (then representation cannot matter here).
- **H3 (storage and selective reads reduce end-to-end cost).** **Not tested here.**
  Requires a matched file/array-cache baseline and a level-major storage layout; deferred.

## Task, data and independent units

- **Dataset:** PTB-XL 1.0.3 (PhysioNet; CC BY 4.0; Wagner et al., 2020): 21,799 ten-second
  12-lead ECGs from 18,869 patients. We use the 100 Hz records (`records100`). Files are
  verified against PhysioNet's `SHA256SUMS.txt`; data stays in `data/` and is never
  redistributed. This is a **clinical ECG, not an industrial signal**: results speak to the
  method, not to CNC or vibration use.
- **Signal:** a single lead (II), 1000 samples at 100 Hz, matching MorletDB's single-channel
  model. Lead identity is read from each record header, not assumed.
- **Task:** multi-label classification of the five diagnostic superclasses (NORM, MI, STTC,
  CD, HYP), derived from `scp_codes` via `scp_statements.csv` (`diagnostic_class`), as in the
  PTB-XL benchmark convention. Records with no superclass are excluded and counted.
- **Independent unit:** the patient. PTB-XL's `strat_fold` keeps all records of a patient in
  one fold. **Split:** folds 1-8 train (and retrieval gallery), fold 9 validation (early
  stopping only), fold 10 test (evaluated once per arm after the protocol freeze).

## Retrieval contract

- **Gallery:** training folds 1-8 only, with their labels (historical outcomes that would be
  known at evaluation time).
- **Exclusions:** a query never retrieves itself or any record of the same patient.
  Validation and test patients are absent from the gallery by construction.
- **Retriever (fixed for all representation arms):** cosine similarity of log-magnitude
  FFT spectra (0.5-40 Hz) of the per-window z-scored lead. It is shift-invariant, has no
  fitted parameters and is exhaustive (exact top-k over the whole gallery; no index).
  k = 8 neighbours.
- **Selection vs representation:** the representation arms all use the *same* retrieved
  neighbours, so they differ only in how those neighbours are represented. A random-neighbour
  arm isolates the value of selection.

## Representation contract (`msmem`)

- Orthonormal periodized DWT, Daubechies-4, 6 levels (blocks a6, d6, ..., d1 of lengths
  16, 16, 32, 64, 128, 256, 512), after per-window z-scoring. The
  1000-sample window is extended to 1024 by symmetric half-sample reflection, and
  reconstruction crops it back. Full coefficients are always stored; sparsification only
  applies to the budgeted context, never to storage.
- Specs are immutable with content-derived ids (family, levels, rate, units, window,
  normalization, channel, boundary, dtype, sparsification, implementation version).
  Incompatible specs are refused. Level indices map to physical bands through the
  sampling rate.
- Verified against PyWavelets and analytical fixtures (`tests/`).

## Arms

Every arm with retrieved context also receives the neighbours' label vectors, except
the **no-label variants** marked `n` (amendment 1), which receive neighbour signals only.

| Arm | Neighbour signal context | Scalars per neighbour |
|---|---|---|
| A0 no-retrieval | none (query only; no neighbour labels) | 0 |
| A1 labels-only | none (neighbour labels only) | 0 |
| A2 random | full raw signal of 8 random gallery records (other patients) | 1000 |
| A3 retrieved-raw | full raw signal of the retrieved neighbours | 1000 |
| R-B | raw signal decimated to B samples (FFT resampling), interpolated back | B |
| W-B | Fixed resolution: all coarse blocks totalling B coefficients (B=32: a6,d6; 64: +d5; 128: +d4), i.e. the projection onto the approximation space of that length | B |
| C-B | The W-(B/2) coarse blocks plus the B/2 largest-magnitude coefficients among all remaining detail blocks (deterministic rule) | B (+ B/2 indices) |

No-label variants (amendment 1): A3n, R-Bn, W-Bn and C-Bn are identical to A3, R-B, W-B and
C-B but without neighbour labels; the model omits the label input.

Budgets: B ∈ {32, 64, 128}. Every arm delivers each neighbour to the model as a
1000-sample time-domain signal reconstructed from what was transmitted. The neighbour
encoder therefore has identical capacity across arms, and the arms differ only in
information content. C-B also needs coefficient indices; we report its scalars and its
bytes with indices included.

## Model and training (fixed a priori; no tuning on the test fold)

- **Encoder:** a shared small 1D CNN (3 conv blocks plus global pooling) for query and
  neighbours.
- **Neighbour aggregation:** the mean of neighbour embeddings, concatenated with the mean
  neighbour label vector and the query embedding, feeds an MLP head with 5 sigmoid outputs.
- **Arm-specific parameters:** A0 and A1 use the same encoder and head with the missing
  inputs absent; parameter counts are reported per arm.
- **Training:** Adam (lr 1e-3), batch 128, at most 20 epochs, early stopping on validation
  macro AUROC (patience 4), 3 seeds per arm. The same budget applies to every arm.

## Metrics, uncertainty and decision rules

- **Primary metric:** test macro AUROC over the 5 superclasses.
- **Secondary metrics:** per-class AUROC and macro AUPRC.
- **Cost recorded (descriptive, not H3):** delivered scalars and bytes per query, retrieval
  time, representation build time, and training and inference time.
- **Uncertainty:** paired patient-level bootstrap on the test fold (1000 resamples of
  patients), using seed-averaged predictions. The seed spread is reported separately.
- **Pre-registered primary comparison (amendment 1):** C-64n vs R-64n. The with-label
  comparison C-64 vs R-64 becomes secondary.
- **Reported comparisons:** C-B vs W-B, A3 vs A0 (H1), A3 vs A2 (selection), A3 vs A1
  (signal content), and every budget arm vs A1. All comparisons are reported, not only
  significant ones.
- **Execution in two phases (amendment 1):**
  - Phase 1 runs A0, A1, A3, A3n, R-64n, W-64n and C-64n with 3 seeds each.
  - The phase-2 gate uses **validation fold 9 only**. If A3n does not exceed A0 in mean
    validation macro AUROC, the remaining arms are not run and the test fold is evaluated
    once for the phase-1 arms. Otherwise phase 2 runs the remaining arms.
  - Test-fold predictions are written during training but are not read before the single
    final evaluation of all completed runs.
- **Decision for this pilot:**
  - H1 is supported if A3 − A0 has a 95% interval above 0.
  - H2 is supported if C-64n beats R-64n and W-64n with intervals above 0, *and* A3n beats
    A0. If A3n does not beat A0, retrieved signals carry no usable information here and the
    result is reported as inconclusive about representation. The with-label comparisons
    (C-64 vs R-64 and W-64, A3 vs A1) are reported as secondary.

## Leakage controls

- Patient-disjoint folds.
- No self or same-patient retrieval.
- A training-only gallery.
- Per-window normalization (nothing fitted on held-out data).
- An unfitted retriever and a deterministic refinement rule.
- Early stopping on validation only.
- The test fold is evaluated once, after this protocol is committed.

## Compute limits

Local Apple M5 Max (MPS), no paid resources. The full matrix (13 arms × 3 seeds) runs only
after a smoke run measures the per-run time; the estimate is recorded in the results.

## Literature: published facts vs this work

| Work | What it establishes (as published) | Not established by it |
|---|---|---|
| TS-RAG (arXiv 2503.07649; NeurIPS 2025) | Retrieving similar segments with pretrained encoders and fusing them through a learned mixing module improves zero-shot forecasting of time-series foundation models | Whether the *representation* of retrieved context, at a fixed budget, matters |
| TimeRAG (arXiv 2412.16643) | DTW-retrieved reference sequences given to an LLM as text improve forecasting | Numerical (non-text) consumers; budgeted representations |
| RAG4CTS (arXiv 2603.04951) | A training-free, regime-aware RAG with a hierarchical knowledge base of raw historical regimes helps industrial covariate time-series forecasting | Multiscale coefficient context |
| Wavelet tokenizer for ViT (arXiv 2405.18616) | Sparse wavelet-coefficient tokens can replace patch tokenizers on ImageNet with accuracy and throughput gains at 256 px (smaller gains at 512 px) | Coarse-to-fine token selection (left as future work); 1D signals; retrieval |
| Operator-based machine intelligence (arXiv 2507.21189) | A conceptual Hilbert-space and operator framework with reported spectral-model results | Any retrieval or external-memory component (architectural inspiration only) |
| DWT time-series indexing; wavelet synopses (classic database work; citations to verify) | Wavelets for similarity search and approximate queries | Delivery of multiscale context to a downstream model |

**Proposed contribution, if supported:** a controlled measurement of whether the
*representation* of retrieved signal context, holding the retrieved examples and the model
capacity fixed, changes task quality at a fixed context budget. Neither wavelets, coefficient
storage, progressive retrieval nor retrieval augmentation is claimed as new.

## Unresolved choices (to revisit after the pilot)

- Single lead vs all 12 leads.
- k, the budget grid and the choice of retriever.
- Whether neighbour labels should be withheld in a variant, to measure pure signal context.
- Forecasting or reconstruction tasks.
- An industrial dataset with enough independent units.
- H3 storage layout.

## Changelog

- 2026-10-25: initial pilot protocol.
- 2026-10-25, amendment 1 (before any training run or held-out evaluation): the primary H2
  test moves to a **no-label** variant. Reason: neighbour labels are likely the strongest
  input, and could mask any effect of how neighbour *signals* are represented, which is
  the question H2 asks. The with-label arms stay as the realistic secondary setting.
  Execution is split into a decisive phase 1 and a phase 2 that runs only if a
  validation-only gate passes.
