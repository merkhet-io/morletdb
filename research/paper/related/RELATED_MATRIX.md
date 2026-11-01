# Related work: comparison matrix and novelty assessment (paper draft 0.7)

Compiled 2026-11-01 for PR #61. The goal was to find the **strongest overlapping** prior work for
shifted inner-product probes and for MorletDB's integration, from primary sources. Raw notes with
quotations, search terms and access gaps: [`notes_algorithms.md`](notes_algorithms.md) and
[`notes_systems.md`](notes_systems.md). Read levels: **F** full text, **P** parts, **A** abstract,
metadata or documentation only.

"Shift" below means a displacement along the time axis. It is distinct from a **vertical offset**
(adding a constant, removed by normalization, e.g. KV-match's cNSM β) and from **shift-invariant
kernels** that average or maximize over all circular shifts (GRAIL's SINK).

## What we compare against

MorletDB scores `D(q,g) = 1 − max_{|ℓ|≤30} (1/n) Σ_t q[t+ℓ] g[t]`: a biased, zero-padded, linear
cross-correlation of z-scored envelopes (n = 410). Each lag term equals `⟨s_ℓ, g⟩` with `s_ℓ` the
zero-padded shifted query. Probe search sends 13 shifted probes (spacing 5, chosen empirically) to one
pgvector inner-product HNSW index over one float16 copy of each envelope, takes the union (50 per
probe) and re-ranks with `D`. A router sends galleries of ≤ 5,000 eligible windows to exact search.

## Matrix: algorithms

| Work | Read | Similarity / normalization | Shift model | Side | Index | Candidates | Copies / cost | Refinement |
|---|---|---|---|---|---|---|---|---|
| Rafiei, ICDE 1999 | F | Euclidean on normal form, truncated DFT | set of linear transformations incl. time shifts (circular via DFT phase), moving average, scaling | transformations applied to index entries (MBR of transformations) | R-tree on DFT coefficients, **one traversal for all transformations** | exact (no false dismissals) | one copy | false positives removed by applying every transformation |
| Keogh et al., VLDB 2006 / VLDB J 2009 | F | Euclidean, also DTW/LCSS | all circular rotations; **rotation-limited** by dropping rows | **query**: n×n matrix of query rotations, merged into wedges | lower bounds; VP-tree on FFT-magnitude bound | exact | database stored once | exact |
| Yankov et al., SDM 2007 | P | rotation-invariant Euclidean (pseudo-metric) | all circular | either | metric pivots | exact | one copy | exact |
| Vlachos et al., CIKM 2005 | P | Euclidean on Fourier magnitudes | all circular | **data**: invariant feature | VP-tree variant | exact for the feature | one signature | — |
| Foulds et al., DAM 2015 | A | distance over cyclic permutations of the query | all cyclic | query | none: parallel DFT-based search | exact | — | — |
| Paparrizos & Gravano, SIGMOD 2015 / TODS 2017 (k-Shape, SBD) | P | NCC_c; defines biased NCC_b (our per-lag statistic) | all linear shifts via FFT | pairwise | none | exact | — | — |
| Paparrizos & Franklin, PVLDB 2019 (GRAIL) | P | SINK kernel over all circular shifts | all circular | **data**: learned Nyström embedding | any Euclidean/IP index | approximate | one embedding per series | — |
| Qiu et al., arXiv 2018 (Learning Correlation Space) | P | Pearson correlation at **zero lag** only ("we only consider Pearson correlation") | none | data: DFT + learned embedding | Euclidean index | approximate | one embedding | — |
| MASS, UCR suite, Matrix Profile | A | z-normalized ED per subsequence | every offset | scan | none | exact | — | — |
| Hydra 2 benchmark, iSAX/MESSI/Hercules/Dumpy, SEAnet | P/A | z-normalized ED, DTW | none | — | data-series indexes | exact/approx | one copy | — |
| BRAID (SIGMOD 2005), StatStream (VLDB 2002) | A | lag correlation between stream pairs; BRAID samples lags geometrically | lags | pairwise monitoring | sketches | approximate | sketches | — |
| Multi-probe LSH (VLDB 2007) | P | L2 | none | perturbs **hash buckets** of one query | LSH | approximate | fewer tables | — |
| ALSH (NeurIPS 2014), cone trees (KDD 2012) | P/A | MIPS → NN reductions | none | asymmetric transforms | LSH / trees | approx / exact | one copy | — |
| ColBERT end-to-end, SIGIR 2020 | F | sum over query tokens of max inner product | — | **query**: one vector per token | one faiss index, **one search per query vector** | approximate | multi-vector docs | **union re-ranked exactly** |
| Neural audio fingerprint (Chang et al., ICASSP 2021) | F | inner product on segment embeddings | ±200 ms robustness learned; 50% overlapping segments | data overlap + learned | faiss MIPS | approximate | ~2× segments | per-segment union, sequence re-score |
| Zhao & Singer 2014 (cryo-EM) | A | bispectrum-invariant features | all in-plane rotations | data: invariant feature | randomized ANN | approximate | one feature vector | exact rotational alignment on neighbours |

## Matrix: seismic and systems

| Work | Read | Similarity | Shift handling | Index / path | Candidates | Filters / updates | Hit = interval of recording |
|---|---|---|---|---|---|---|---|
| FAST, Sci. Adv. 2015; Rong et al., PVLDB 2018 | F | Jaccard on binary spectrogram fingerprints | **data**: 10 s windows every 1 s | MinHash LSH | approximate | batch | times (diagonal clustering) |
| Tibi et al., BSSA 2017 | F (OSTI preprint) | max-over-lag cross-correlation of 19 s waveforms | data: kernel PCA maps CC to Euclidean | KD-tree forest (LAFS) | approximate (recall > 80%, 25× faster than linear) | template library | detections; exact CC computed on candidates |
| Gonzales et al., Sandia (OSTI 1458306) | F | xcorr distance over a sliding set of alignments (±0.5 s) | data: kernel projection; several internal queries compensate projection error | KD-tree | approximate | — | — |
| EQcorrscan, FMF, Match & Locate | A/docs | normalized CC | full sliding scan; bounded shift only for pick refinement | brute force | exact | — | times |
| KV-match, ICDE 2019 | F | z-normalized ED/DTW with α/β constraints (vertical offset, scaling) | every offset | KV-store index on window means | exact | static | offsets in stored series |
| ULISSE, PVLDB 2018 | F | ED/DTW | every offset, variable length; envelope per γ offsets | data-side compressed | exact and approximate | static | offsets |
| AnalyticDB-V, PVLDB 2020 | F | L2 | — | cost-based choice: B-tree filter + **exact brute force** vs approximate plans | both | lambda architecture | — |
| Milvus, SIGMOD 2021 (+ Knowhere code) | F + code | L2/IP | — | Strategy A exact full scan for selective filters; HNSW switches to brute force at ≥93% filtered | both | segments | — |
| ACORN, SIGMOD 2024 | F | various | — | brute-force pre-filter below selectivity 1/γ | both | static | — |
| Weaviate / Qdrant / Vespa / Lucene | docs/code | various | — | documented flat-search thresholds (40,000 objects; full_scan_threshold; approximate-threshold 0.02; filter cost ≤ k) | both | various | — |
| VBase, OSDI 2023; pgvector ≥ 0.8 | F / docs | vector distances | — | filtered iterative index scans inside PostgreSQL | approximate | PostgreSQL | — |
| SingleStore-V, PVLDB 2024 | F | IP/L2 | — | ANN per immutable segment + exact scan of fresh rows; visible at commit (MVCC) | approximate + exact | transactional | — |

## Assessment

**Known (we do not claim):**
- The identity "lag term = inner product with a shifted signal" (textbook; NCC_b in k-Shape/TODS).
- **Shifting the query instead of the data**, including a **bounded** set of shifts and grouping
  neighbouring shifts: Keogh et al. 2006/2009 (exact, circular, Euclidean); Rafiei 1999 includes time
  shifts among transformations evaluated against one index of untransformed data; Foulds et al. 2015.
- **Several vector queries against one ANN index, union, exact re-ranking:** ColBERT 2020.
- **ANN candidates for seismic cross-correlation, exact correlation on candidates:** Tibi et al. 2017
  (data-side kernel-PCA embedding), Gonzales et al. (several internal queries).
- **Routing between brute force and an index by filter selectivity:** AnalyticDB-V, Milvus, ACORN,
  Weaviate, Qdrant, Vespa, Lucene. **Filtered index scans in PostgreSQL:** VBase, pgvector 0.8.
- **Vector search visible at commit in a transactional database:** SingleStore-V (without synchronous
  index maintenance).

**What distinguishes MorletDB (supported by our evidence):**
- For a **bounded, zero-padded, linear** lag scorer, query-side shifted probes go to an
  **unmodified approximate inner-product index**: no trained embedding (unlike Tibi, GRAIL), no per-shift
  data replication (unlike FAST), no lower bounds (unlike Rafiei, Keogh). One envelope vector copy plus
  its index are still stored (1.00–1.39× raw); the price is approximate candidate selection.
- The **diagnosis** that zero-lag candidates fail for this scorer because of their metric, not ANN error
  (R2-D), and an **ablation** separating grid sparsity (none at spacing 5, 0.014 at spacing 10 on dev)
  from index error (0.023 on held-out), with the spacing explained by envelope smoothness.
- One **contract on both paths** in PostgreSQL: sample intervals of losslessly stored recordings,
  eligibility filters, row-level security on all companion tables, commit-time visibility; and measured
  costs of storage, filters and maintenance.

**Single-traversal alternative (the design of Rafiei/Keogh):** tested before probe search on our scorer
(`evaluation/pruning/`): lag-uniform Keogh-style and spectral bounds pruned at most 63% of candidates
even with an oracle threshold and a free bound, capping an exact pruned scan near 1.9× (2.3× in the
large-gallery limit) against 5.4× for probes. Finer per-lag-group wedges were not tested.

**Not found** (which is not proof of absence): a work that serves a bounded linear cross-correlation by
a sparse grid of query-side shifted probes into one stock inner-product ANN index with exact re-ranking.
Searches beyond the notes: image retrieval with rotated query copies; "cross-correlation as maximum inner
product search with shifted queries"; cryo-EM rotational nearest neighbours (Zhao & Singer use
data-side invariant features and align candidates afterwards).
