# Decision milestone, phase 1: current-state assessment

Audited 2026-10-26 against `main` at `5292c5e` (0.1.0-alpha.3) and open PR #5. This audit
covers repository evidence only. It contains no new measurements.

The four hypotheses under decision:

| | Hypothesis |
|---|---|
| H1 | The investigation workflow saves engineers meaningful time. |
| H2 | Morlet features improve relevant retrieval over simpler alternatives. |
| H3 | PostgreSQL integration provides enough practical value to justify an extension. |
| H4 | The current implementation supports a useful operating envelope. |

## What existing reports actually establish

| Evidence | Establishes | Does not establish |
|---|---|---|
| CWRU cross-load slice (`evaluation/reports/cwru-007-cross-load-v1.json`) | The evaluator runs end to end on real recordings through the Rust core. Macro recording precision@10: statistics 1.000, FFT bands 0.950, pooled Morlet 1.000, temporal 1.000, aligned samples 0.378 | Any H2 advantage: Morlet **ties** RMS/crest/kurtosis. The slice has 6 recordings from one rig, 3 fault classes, no healthy class, no negative queries and no held-out bearings. Recall@10 is capped at 10/24 by construction |
| Seismic replay v2 (`evaluation/reports/seismic-cx-north-chile-2024-v2.md`) | Real ingestion of about 324 M samples over 3 stations with gaps, duplicates and resume. Server-side extraction p50 6.3 ms per 2048-sample window. Pooled features tie or beat the temporal experiment | **H2 is not tested here.** No statistics or FFT baseline was run, so "pooled beats temporal" says nothing about Morlet versus simple features. The catalog labels are a proxy, the associated queries per station number 9–14, and the stations are not independent |
| Wave-index posting PoC (`evaluation/reports/wave-index-synthetic-v1.json`, seismic v2 sweeps) | An exact reference oracle and the recall/traversal trade-off. Seismic candidate recall is 0.29–0.43 at budget 16 and 0.67–0.81 at budget 1024, with 0.2–0.7 M posting visits per query | That approximate search is viable. The traversal cost is roughly constant in the budget; synthetic data only for the PoC |
| SQL exact-search scale harness (`evaluation/scale.py`, CI 10k smoke) | Correct exact ranking against a NumPy oracle at 10k synthetic feature rows; explicit budget failures | **H4.** It bypasses raw ingestion and extraction by design. The manual 100k/1M sweeps are not committed as results |
| `benchmarks/` (synthetic, CI) | Extraction-on-write, JSONB/typed/DuckDB exact top-10 parity, and search under one concurrent writer | Representative throughput: synthetic sinusoids, CI hardware, labelled "not architecture-selection" |
| Notebook (`examples/notebooks/waveform-investigation.ipynb`) | The import → select → search → inspect workflow on synthetic data, including a documented sign-flip blind spot | H1: no user has run it; the data is synthetic |
| PTB-XL multiscale pilots (PR #5, `research/multiscale_memory/`) | On ECG, retrieved context does not improve a classifier: not with a fixed spectral retriever and mean pooling (pilot 1), nor with a learned retriever and attention (pilot 2); no retrieval arm beats no retrieval with an interval above 0, and several compressed-context arms are significantly worse. Budgeted coarse+refine wavelet context degrades least, in the order of reconstruction fidelity | Anything about vibration or Morlet features; a separate research track |

## Per hypothesis

- **H1, workflow value:** **untested.** There has been no external participant, no baseline
  workflow, and no timing.
- **H2, Morlet over simple features:** **no supporting evidence.** The only head-to-head
  comparison (CWRU) is a tie on an easy slice. Seismic never ran simple baselines. On
  every dataset where we have a comparison, simple features matched or beat the richer
  representations.
- **H3, PostgreSQL integration:** **untested** as a user benefit. The engineering exists:
  metadata filtering, eligibility in SQL, provenance, durable ingestion and RLS. Nobody
  has compared it with "Parquet files + notebook + NumPy kNN".
- **H4, operating envelope:** **partially measured, not established.** Real ingestion
  (seismic replay: one stream at a time, about 112k samples/s including extraction, emit-to-commit p90 0.42 s) and synthetic
  search exist separately. No single sustained run measures ingestion, extraction,
  visibility and search latency together on recorded hardware.

## Experiments already in progress (do not conflict)

- **PR #5** (draft, `research/multiscale_memory/`): PTB-XL pilot 2 (learned retriever)
  is running locally. It touches no extension, `evaluation/` or `docs/` files. This
  milestone works on a separate branch and worktree.
- **Wave-RAG** (`docs/wave-rag-spec.md`, merged spec, proposed track): not implemented.
  This milestone adds no RAG or LLM layer.
- **CNC spindle spec** (`docs/cnc-spindle-experiment.md`): proposed, blocked on data
  access and reviewers. It asks the same retrieval question; this milestone's plan is
  compatible with it and should replace it if a partner dataset arrives.

## Smallest experiment that could change the decision

1. **H2, decisive and cheap.** On one public vibration dataset with *independent physical
   bearings* and *varied operating conditions*, compare statistics, FFT bands and frozen
   pooled Morlet retrieval under identical windows, normalization and eligibility.
   - If Morlet does not beat FFT bands by a pre-agreed margin, H2 fails, and Morlet is
     not the product's differentiator. This is the likely outcome given every
     comparison so far.
   - If it does, H2 gains its first support.
   The existing evaluator (`evaluation/retrieval.py`) already implements the three
   methods. It mainly needs group-aware splits, operating-condition metadata, harder
   negatives and a group bootstrap.
2. **H1/H3, decisive but needs people.** 2–3 external engineers each run the same
   investigation twice: once in MorletDB and once in a notebook baseline (same features,
   NumPy kNN over Parquet). No amount of internal work substitutes for this.
   Until it happens, H1 and H3 stay "decision pending".
3. **H4** matters only if 1 or 2 is positive. One sustained ingestion-plus-search run
   bounds it; it does not need a new harness.
