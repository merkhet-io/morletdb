# VR1: pg_morlet features and text vectors side by side in pgvector

Status: **FROZEN** in the commit that adds this line, before `vr1.py check` is run. Changes
after the freeze are logged under "Changes after freeze".

## Question

Can pg_morlet's waveform features and a text embedding live in the same PostgreSQL table,
be searched with pgvector and fused into one ranking, **without** losing the exact ranking,
the access rules or the provenance that pg_morlet guarantees today?

This is option A from the wave-RAG discussion: an **integration and fidelity check**. It
makes **no claim that retrieval is useful**. No relevance labels are read, and nothing here
says whether waveform, text or fused retrieval finds better neighbours. That is option B,
which needs its own frozen protocol and data.

## Setup (fixed now)

- **Software:** PostgreSQL 17.11 (pgrx), pg_morlet 0.3.1 from `main`, pgvector 0.8.0 built
  from the tag `v0.8.0` (commit 2627c5f) of github.com/pgvector/pgvector.
- **Data:** PTB-XL 1.0.3 (CC BY 4.0), 100 Hz records, **folds 1–8 only** (17,418 records).
  Folds 9 and 10 are not read; they stay available for option B. Each file read is checked
  against the release's `SHA256SUMS.txt`.
  - **Waveform:** lead II, 1,000 samples (10 s) in mV, decoded from the 16-bit format by a
    numpy reader. The first sample must equal the header's initial value.
  - **Text:** the free-text `report` (mostly German). Empty reports get an empty-text vector
    and are counted.
- **Table** `vr1.ecg`, one row per record:
  - `samples float8[]`;
  - `features float8[]`, **generated** as
    `morlet_features(morlet_normalize(samples, 'zscore'), 100, 'morlet-energy-lowrate-v1')`,
    the same normalization and scorer inputs as `morlet_search_features`;
  - `wave_vec vector(15)`, generated as `features::vector`;
  - `extractor text`, generated as the constant extractor name;
  - `text_vec vector(256)` and `text_model text`, supplied by the loader;
  - `patient_id`, `strat_fold`, `site`, `device` as plain columns.
- **Text embedding: a deterministic stand-in, not a language model.** Character 3–5-grams of
  the lower-cased report, signed feature hashing into 256 dimensions, TF-IDF weighted with
  IDF fitted on the corpus, L2-normalized (`text_model = 'hash-char35-tfidf-256-v1'`). It
  exists to test the plumbing; any real embedding model drops into the same column.
- **Indexes:** HNSW, `m = 16`, `ef_construction = 64` (pgvector defaults). `wave_vec` with L2
  (`vector_l2_ops`), `text_vec` with cosine (`vector_cosine_ops`).
- **Queries:** 200 records drawn with seed 7 from the corpus. A query never returns itself
  or another record of the same patient. k = 10.
- **Fusion:** reciprocal rank fusion (RRF) of the top 50 of each arm, score
  Σ 1/(60 + rank), ties broken by `ecg_id`. Each fused row carries both constituent ranks,
  NULL when absent from that arm. **Distances are never added across arms**
  (wave-RAG spec).

## Checks and pass criteria (fixed now)

| Id | Check | Pass if |
|---|---|---|
| C1 | **Float4 fidelity.** pgvector exact top-10 (sequential scan, `<->`) vs pg_morlet's own scorer `morlet_feature_vector_distance` on `features` (float8), per query | mean overlap ≥ 0.99; distances agree to ≤ 1e-5 relative after the √15 scale |
| C2 | **HNSW recall@10** vs the exact pgvector top-10, at `ef_search` 40, 100, 200, for each arm | recall ≥ 0.95 at some `ef_search` ≤ 200, for both arms |
| C3 | **Filtered search under row-level security.** A reader role sees folds 1–4 only (policy). With patient exclusion, recall@10 vs exact filtered top-10, with `hnsw.iterative_scan` = `off` and = `relaxed_order`, `ef_search` 100 | with `relaxed_order`: recall ≥ 0.95 and 10 rows for every query. `off` is reported |
| C4 | **Access.** A role without `SELECT` is refused in every arm. The reader role returns **0** rows outside folds 1–4 in the wave, text and fused queries | both hold |
| C5 | **Generated columns.** A newly inserted record gets `wave_vec` equal to `morlet_features(...)` within float4 rounding, and `extractor` set; an update of `samples` recomputes both | holds |
| C6 | **Fusion determinism.** The fused top-10 of 20 queries is identical across two runs, and every row's constituent ranks match the arm rankings | holds |

Reported without a criterion:

- latency per arm (median and 95th percentile over the 200 queries, after one warm-up pass),
  exact vs HNSW, with the machine recorded;
- index build time and size;
- top-10 overlap between the wave and text arms. This says how different the two rankings
  are, **not** which is better.

## What a pass and a fail mean

- **All pass:** the pattern is sound as plumbing. pgvector can replace the 4,096-candidate
  cap of `morlet_search_features` for collections where an approximate first stage is
  acceptable, and exact rescoring remains available.
- **C1 fails:** float4 storage changes the ranking. pgvector would then serve only as a
  candidate generator, with exact float8 rescoring required.
- **C2 or C3 fails:** the approximate index or filtering loses neighbours at these settings.
  Report it; do not tune further on this run.
- **C4 or C5 fails:** a correctness bug. Stop and fix before anything else.

## Limitations

- The stand-in text vector is lexical, not semantic.
- ECG, not condition monitoring: the same plumbing, a different domain.
- One machine, one dataset size (17,418 rows); no concurrency or update load.
- PTB-XL folds 1–8 were used to train models in the multiscale-memory pilots; that does not
  matter for a check with no relevance metric.

## Changes after freeze

None yet.
