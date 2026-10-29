# VR1 results: pg_morlet features and text vectors side by side in pgvector

Run once on 2026-10-29, as frozen in `PROTOCOL.md` (a754f6f), with the two logged changes after
freeze. Integration and fidelity only: **no claim that any retrieval is useful.**

**Short version.**

- **The waveform side passes everything.**
  - pg_morlet's 15 Morlet features, stored as a generated pgvector column, rank exactly like
    pg_morlet's own float8 scorer.
  - An HNSW index finds the same top 10 (recall 0.9995–1.0) about 9× faster than a sequential
    scan, and 14× faster than the float8 scorer.
  - Row-level security holds in every arm, including the fused one.
- **The text side fails C2 and C3.** With the stand-in text vectors, HNSW recall@10 is 0.90–0.92
  at default build settings, under the 0.95 bar. Exact duplicate reports explain part of the loss.
- Fusion is deterministic and records both constituent ranks.

## Setup as run

| | |
|---|---|
| Software | PostgreSQL 17.11, pg_morlet 0.3.1 (`main`, 0fbe781), pgvector 0.8.0 (tag `v0.8.0`, 2627c5f) |
| Machine | macOS 27.0.1, Apple M5 Max, local server |
| Data | PTB-XL 1.0.3, folds 1–8: 17,418 records, every file SHA-256-verified; folds 9–10 not read |
| Columns | lead II (1,000 samples at 100 Hz), generated `features` / `wave_vec vector(15)` (`morlet-energy-lowrate-v1`, z-score), `text_vec vector(256)` (`hash-char35-tfidf-256-v1`) |
| Load | 43 s for 17,418 rows including feature extraction; table 103 MB |
| HNSW build | wave 2.4 s, 6.4 MB; text 2.1 s, 15.3 MB (`m` 16, `ef_construction` 64) |
| Queries | 200 records (seed 7); none has an empty report; self and same patient excluded; k = 10 |

## Checks

| Id | Check | Result | Pass |
|---|---|---|---|
| C1 | pgvector exact vs pg_morlet's float8 scorer | top-10 overlap 1.000 (min 1.0); distance error max 4.6e-7, median 6.5e-8 relative | **yes** |
| C2 | HNSW recall@10, `ef_search` 40 / 100 / 200 | wave 0.9995 / 0.9995 / 1.000; **text 0.901 / 0.912 / 0.922** | **no** (text) |
| C3 | Under RLS (reader sees folds 1–4), `ef_search` 100, iterative scan off / `relaxed_order` | wave 1.000 / 1.000; **text 0.924 / 0.924**; always 10 rows | **no** (text) |
| C4 | Access | no grant: refused in the wave, text and fused queries; reader: 0 of 6,000 rows outside its folds | **yes** |
| C5 | Generated columns on insert and update | max relative error 6.0e-8 (float4 rounding); extractor recorded | **yes** |
| C6 | Fusion determinism, 20 queries | identical across runs; 0 rank mismatches | **yes** |

- **Plans:** every approximate query's plan used the HNSW index, both as owner and under RLS.
- **Iterative scan:** `hnsw.iterative_scan` made no difference here. Removing folds 5–8 and the
  query's own patient never left a query short of 10 rows at `ef_search` 100.

## Reported measurements

**Latency** (200 queries after a 20-query warm-up, client round trip included):

| Query | Median (ms) | 95th percentile (ms) |
|---|---|---|
| wave, pg_morlet float8 scorer (`morlet_feature_vector_distance`, full scan) | 15.5 | 18.7 |
| wave, pgvector exact (sequential scan) | 9.6 | 11.4 |
| wave, pgvector HNSW (`ef_search` 100) | 1.1 | 1.4 |
| text, pgvector exact | 6.3 | 7.6 |
| text, pgvector HNSW | 1.3 | 1.8 |
| fused (two HNSW arms of 50 each, RRF) | 2.6 | 3.5 |

**How different the two arms' rankings are:** the exact wave and text top 10 share 0.3% of their
records on average (max 1 of 10). The rankings are nearly disjoint, so fusion mostly interleaves
two different lists. Whether either list, or the fused one, holds better neighbours is not
measured here.

## Post hoc: where text recall is lost (decides nothing)

`posthoc_ties.py` takes the same 200 queries, at `ef_search` 200, and groups them by how many
other records have exactly the same text vector:

| Exact duplicates of the query's report | Queries | Mean recall@10 | Queries below 1.0 |
|---|---|---|---|
| 0 | 91 | 0.974 | 12 |
| 1–9 | 36 | 0.886 | 7 |
| 10–99 | 23 | 0.735 | 7 |
| ≥ 100 | 50 | 0.940 | 3 |

- **Duplicates explain part of the loss.** Mid-sized groups of identical vectors are where HNSW
  misses most: identical points crowd each other out of the graph's neighbour lists.
- **They don't explain all of it.** Unique reports reach 0.974, not 1.0, so 256 dimensions are
  also harder for the index than 15.
- No build or search setting was changed to fix it.

## What this means

- **pg_morlet features in pgvector work as plumbing.**
  - A generated `vector(15)` column keeps the exact ranking, records the extractor, and
    recomputes on update.
  - HNSW makes waveform search about 9× faster at full recall on 17k rows, without
    `morlet_search_features`' 4,096-candidate cap.
  - The caller's privileges and row-level security apply in every arm.
- **Text vectors need their own recall check per embedding model and corpus.** With these
  stand-in vectors and default settings, approximate search misses about 8% of the exact top 10.
  Possible remedies are larger `m` or `ef_construction`, collapsing identical texts before
  indexing, or exact rescoring of a larger candidate set. **None was tested.**
- **Fusion as specified is safe:** rank-based, deterministic, constituent ranks kept, no distances
  added across arms.
- **Usefulness is untested.** That is option B, which needs real text and relevance judgments
  independent of the text.

## Limitations

- The text vectors are lexical stand-ins, not a language-model embedding.
- ECG at 100 Hz, not machine vibration; one machine; 17,418 rows; no concurrent writes.
- The latency numbers include Python client overhead and come from one local run.
- The duplicate analysis was designed after seeing C2 and C3.

Outputs: `results/vr1.json`, `results/load.json`, `results/posthoc_ties.json`. Cite PTB-XL (Wagner
et al., 2020, *Scientific Data*) and PhysioNet.
