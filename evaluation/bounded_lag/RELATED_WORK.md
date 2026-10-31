# Related work: incrementally maintained, bounded-lag waveform retrieval

Scope: positions MorletDB's planned bounded-lag retrieval experiment ((A) shifted query
probes over inner-product ANN indexes, union, then exact re-rank; baseline: faster exact
batched/parallel scan) against streaming-vector systems and shift-tolerant time-series search.

MorletDB semantics used in this note: stored g is a 410-value z-scored float16 envelope,
query q is full precision, and
`d(q,g) = 1 − max_{|l|≤30} (1/n)·Σ_t q[t+l]·g[t]`. This is a biased cross-correlation:
values outside the support are zero, there is no per-lag renormalisation and no circular
shift. Each lag term equals `⟨shift_l(q) zero-padded, g⟩/n`. Known so far: a zero-lag
cosine shortlist of 1000 out of 235k windows recovers 74% of the exact top ten, and HNSW
matches the exact zero-lag shortlist, so the loss comes from the candidate metric. Certified
lower bounds were too loose (≤1.9× ceiling).

Access log (sources read 2026-10-31):

| # | Source (as verified) | What was read |
|---|---|---|
| 1 | RisingWave, "Architecture Design" (design docs) | Full page |
| 2 | RisingWave, "An Overview of RisingWave State Store" (Hummock) | Full page |
| 3 | Yuhao Su, "The Live Index: Why Vector Search Should Be a Streaming Problem", RisingWave blog, 22 May 2026 | Full page |
| 4 | Lu, Feng, Zhou, Solleza, Schwarzkopf, Çetintemel, "VectraFlow: Integrating Vectors into Stream Processing", CIDR 2025 | Full PDF text |
| 5 | Gong, Sun, Fang, Liu, Chen, Gao, "VStream: A Distributed Streaming Vector Search System", PVLDB 18(6):1593–1606, 2025, doi:10.14778/3725688.3725692 (title and authors checked against the PDF's PVLDB reference block) | PDF text, partly (abstract, system, setup, recall sections skimmed) |
| 6 | Singh, Subramanya, Krishnaswamy, Simhadri, "FreshDiskANN: A Fast and Accurate Graph-Based ANN Index for Streaming Similarity Search", arXiv:2105.09613 | **Abstract only** (arXiv landing page). The MSR page was not opened separately. Venue: not stated on the arXiv page |
| 7 | Paparrizos, Gravano, "k-Shape: Efficient and Accurate Clustering of Time Series", SIGMOD 2015, doi:10.1145/2723372.2737793 | Full PDF text (SBD section read closely) |
| 8 | Qiu, Lam, Fusco, Sinn, "Learning Correlation Space for Time Series", arXiv:1802.03628 (v3) | Abstract plus ar5iv full text. Venue: none listed |
| 9 | Rakthanmanon, Campana, Mueen, Batista, Westover, Zhu, Zakaria, Keogh, "Searching and Mining Trillions of Time Series Subsequences under Dynamic Time Warping", KDD 2012, doi:10.1145/2339530.2339576 | Full text of author copy (cs.ucr.edu/~eamonn/SIGKDD_trillion.pdf). The ACM page was not opened |
| 10 | Mueen et al., "The Fastest Similarity Search Algorithm for Time Series Subsequences under Euclidean Distance" (MASS web page; citation as given on the page) | Full web page. The linked MASS paper was **not** read |
| + | Yoon, O'Reilly, Bergen, Beroza, "Earthquake detection through computationally efficient similarity search" (FAST), Sci. Adv. 1(11):e1501057, 2015 | Full text (PMC4672764), read for the multi-probe question |

## Matrix

| Source | Problem | Distance semantics | Algorithm | Update model | Evidence (read) | Implementation | Transfers / does not |
|---|---|---|---|---|---|---|---|
| RisingWave arch. (1) | Streaming SQL with incrementally maintained MVs | n/a | Stateless frontends, compute nodes, MV dataflow DAG, barrier-driven checkpoints to S3/MinIO | Streaming; MVs refresh as records arrive | None (design doc) | github.com/risingwavelabs/risingwave, Apache-2.0 (from GitHub; the page states no licence) | **Transfers:** epoch/barrier model for defining "bounded lag" as a visible-epoch watermark. **Does not:** a separate distributed engine, whereas MorletDB is an in-Postgres extension |
| RisingWave Hummock (2) | State store for streaming state and MVs | n/a | LSM SSTs on object storage, keys versioned by epoch, separate compactors, snapshot reads at a pinned epoch | Epoch-batched writes; `wait_epoch` for cross-node visibility | None | as above | **Transfers:** snapshot-at-epoch reads, which give a well-defined lag bound. **Does not:** no separate hot/cold tier is described; Postgres MVCC already provides snapshots |
| RisingWave live index (3) | Vector search that stays fresh in the stream | pgvector operators: L2, cosine, inner product (`<#>`, negated), L1 | HNSW maintained inside the streaming dataflow | **Append-only inputs only** for HNSW at present; deletes are claimed through exactly-once machinery but are not supported by the index | No benchmarks. Claims "well under a second" freshness and "single-digit ms" search, both unmeasured | as above | **Transfers:** inner-product HNSW as a probe index; the freshness framing. **Does not:** no lag or shift semantics; the zero-lag metric is exactly the one our diagnostic shows is lossy |
| VectraFlow (4) | Continuous vector operators (filter, top-k, join) over streams | Euclidean in experiments | Centroid-based "OPList" index, clustering iV-TopK, windowed V-Join, binary quantisation | Streaming, windowed (count-based sliding windows) | SIFT1M. iV-Filter: Centroid OPList 1.1–2.5× over brute force, 3–9× over HNSW, recall 0.946 (HNSW 0.804). V-Join ~2–10× over brute force as window grows from 1k to 4k | No code link found in the paper. Licence: paper is CC-BY 4.0 | **Transfers:** quantised prefilter followed by exact re-check, similar to float16 then exact re-rank. **Does not:** standing queries over a vector stream, versus our ad-hoc query over stored windows; no shift tolerance |
| VStream (5) | Streaming vector search embedded in a stream engine (Flink) | Generic ANN; metric not pinned in the sections read. Datasets SIFT/Deep | Dynamic (LSH-based) partitioner, four-level storage (state memory, local memory, local disk, remote), hot/cold separation by access frequency, indexes such as HNSW/IVF_PQ per segment | Inserts, deletes and expiry; streams at 0.5k–9.5k/s with query:insert:delete = 1:3:1 | SIFT1B, Deep1B, Tweets (475M). Claims 251–373× query efficiency, 2.2–2.5× CPU and 1.5–2.0× memory vs. Milvus et al. | github.com/ZJU-DAILY/VStream, MIT (from GitHub) | **Transfers:** segment/immutable-state design and the hot/cold idea for recent windows. **Does not:** a distributed system on Flink + RocksDB; zero-lag vector semantics only |
| FreshDiskANN (6) | Graph ANN index that absorbs updates in real time | Generic ANN (metric not stated in abstract) | Graph index (DiskANN family) with update rules; SSD-resident at billion scale | Concurrent inserts, deletes and searches | Abstract only: >95% 5-recall@5, thousands of updates/searches per second, 5–10× cheaper freshness than periodic rebuild | microsoft/DiskANN, MIT (from GitHub; not verified that it contains the FreshDiskANN code path) | **Transfers:** evidence that streaming graph ANN with deletes is solved for zero-lag metrics, so the per-lag probe indexes could be fresh. **Does not:** no shift semantics; recall is measured against its own metric, not ours |
| k-Shape / SBD (7) | Shape-based time-series clustering | Full cross-correlation over all 2m−1 shifts (zero-padded via FFT); SBD uses coefficient normalisation `CC_w/√(R0(x,x)·R0(y,y))` (one global normaliser, not per-shift); NCC_b and NCC_u also defined | FFT cross-correlation in O(m log m); shape-extraction centroids | Static batch clustering | 48 UCR datasets. Claims k-Shape beats scalable approaches on accuracy and is about 2 orders of magnitude faster than the best non-scalable competitor | Authors' page (columbia.edu/~jopa/kshape.html); github.com/johnpaparrizos/kshape, MIT (from GitHub) | **Transfers:** with z-scored inputs, SBD's normaliser is a constant, so the score is close to MorletDB's biased CC; FFT for a batched exact scan. **Does not:** all shifts vs. our \|l\|≤30; no indexing or retrieval |
| Learning Correlation Space (8) | Fast top-k correlation search | **Zero-lag** Pearson correlation; no lag search | DFT features followed by an MLP embedding, L2-normalised, so that Euclidean distance approximates correlation; NN index on the embedding | Static | Stock, Yoga, UWaveGesture, StarLightCurves. Abstract: halves approximation loss; top-k "precision from 5% to 20%" (wording ambiguous: possibly a gain of 5–20 points); exact values are only in figures | None; the code URL is omitted for blind review | **Transfers:** learned embeddings as a candidate generator. **Does not:** zero-lag only, so it has the same blind spot as our cosine shortlist. Learned components conflict with frozen protocols |
| UCR suite (9) | Exact subsequence search (ED/DTW) at trillion scale | Per-subsequence z-normalised ED or DTW; a sliding window over one long series | Early-abandoning z-normalisation, reordered early abandoning, query/data role reversal in LB_Keogh, cascading lower bounds (LB_KimFL, LB_Keogh) | Static scan; the paper notes applicability to streams | Exact DTW claimed faster than prior ED state of the art. Chimp genome query (len 72,500): 38.7 days naive vs 14.6 h; real-time DTW at 29,219× real time (EEG) | Not checked in the text read; the paper describes code as available | **Transfers:** baseline design for exact scans (early abandon against the best-so-far kth score, reordering, cascades). **Does not:** its bounds are DTW/ED-specific; our own bounds were already too loose (≤1.9×) |
| MASS (10) | Distance profile of one query against all subsequences | z-normalised ED, equivalent to per-subsequence Pearson correlation, so **normalised per shift**; MASS_absolute variant has no normalisation | FFT sliding dot product, O(n log n), data-independent; V2 >2× V1, V3 piecewise, V4 DCT (no aliasing) | Static; the page does not mention streaming or batched multi-query | GPU example: 100M points with a length-400 query, 1.6 s GPU vs 8.9 s CPU | MATLAB, Python (MASS.py; mass-ts on GitHub, Apache-2.0), C++. Page states no licence | **Transfers:** a sliding dot product equals our lag terms; FFT and GPU paths for an exact scan. **Does not:** one long series vs. our many independent windows (at 61 lags, direct dot products may beat FFT); per-shift normalisation differs from ours |
| FAST (+) | Detecting similar earthquakes in continuous seismic data | Jaccard over binary Haar-wavelet fingerprints of spectrogram images | MinHash LSH (b=100 tables, r=5); single-bucket lookup per table | Static batch (all pairs) | One week (Calaveras, CCOB.EHN): 89 detections (21/24 catalog, 68 new, 12 false); 1 h 36 min vs 9 d 13 h autocorrelation (≈140×) | No code link in the text read; a patent application is mentioned | **Transfers:** the closest seismology analogue; shift tolerance comes from **data-side** overlapping windows (1 s lag), not query probes. **Does not:** non-correlation metric; no shifted query probes and no multi-probe LSH |

## Not novel

- **Streaming / fresh vector indexes:** FreshDiskANN (inserts and deletes), VStream (inserts, deletes, expiry, hot/cold), RisingWave (HNSW in a streaming dataflow, append-only for now), VectraFlow (windowed continuous operators).
- **Normalised cross-correlation as a shift-tolerant distance:** k-Shape/SBD, and the sliding dot product in MASS. FFT evaluation of all shifts is standard.
- **Incrementally maintained materialized views and epoch-consistent snapshots:** RisingWave (1, 2).
- **Hot/cold or tiered storage for streaming vectors:** VStream. Hummock uses object storage plus local caching.
- **Learned embeddings that approximate correlation for NN search:** Qiu et al. (8), zero-lag only.
- **Exact-scan acceleration:** UCR suite (early abandoning, cascading lower bounds); MASS (FFT, GPU).
- **Shift tolerance by redundancy:** FAST gets it on the data side through densely overlapping windows. Among the sources read, **none** uses query-side shifted probes into an ANN index. k-Shape and MASS compute every shift exactly; FAST probes only the query's own bucket. Multi-probe LSH (probing neighbouring buckets) exists in the literature but was **not read** here, and it is about bucket boundaries rather than time shifts. We do not claim shifted-copy query augmentation is new in general; we only report that we did not find it in these sources.

## Open combination

We found no source that combines all of the following:
1. Retrieval under a **bounded-lag (|l|≤30), zero-padded, biased (non-renormalised)** cross-correlation score over **many independent fixed-length windows** (rather than one long series or all-shift SBD).
2. Candidate generation through **standard inner-product ANN indexes**, by issuing a **grid of zero-padded shifted query probes**, taking the union, and **exact re-ranking** under the true lagged score.
3. Indexes and windows **incrementally maintained inside a transactional database** (PostgreSQL), with a stated bound on how stale a result can be.

Streaming-vector systems (3–6) cover point 3 for zero-lag metrics only. Shift-aware methods (7, 9, 10) are exact and static. FAST gets shift tolerance on the data side under a different metric. Whether point 2 actually recovers the exact top ten better than the 74% zero-lag shortlist, at a cost below a well-engineered exact scan (UCR/MASS-style batched baseline), is an empirical question that none of these sources answers.

## Semantics differences to watch

- **SBD vs MorletDB:** SBD divides the whole CC sequence by one constant `√(R0(x,x)·R0(y,y))` and maximises over all 2m−1 shifts. MorletDB uses `1/n` (biased, NCC_b-like) and \|l\|≤30. For exactly z-scored inputs these differ only by a constant factor (n vs ‖q‖‖g‖), but the lag ranges differ, and float16 storage breaks exact unit norm.
- **MASS / UCR suite normalise per subsequence (per shift).** That equals per-lag Pearson. MorletDB does **not** renormalise per lag, so edge lags are shrunk by the zero-padded overlap (n−|l| terms over n). Neighbours under MASS-style distance can differ from ours.
- **Circular vs zero-padded:** k-Shape's FFT is padded to avoid wrap-around. Any FFT-based MorletDB scan must pad to ≥ n+30 so it does not compute a circular correlation.
- **Probe norms:** `‖shift_l(q)‖ < ‖q‖` for l≠0, so each probe must use **inner-product (MIPS)** search, not cosine. Cosine on probes would renormalise per lag and change the semantics. HNSW with inner product is not a metric space, so its recall behaviour may differ from the cosine run.
- **Zero-lag "correlation" sources** (Qiu et al.; all vector systems) match only the l=0 term. Their recall numbers say nothing about lagged recall.
- **Data-side vs query-side shift coverage:** FAST-style overlapping database windows multiply storage and index size. Query-side probes multiply query cost by the probe count. The two are not interchangeable when comparing cost.
- **Recall definitions:** FreshDiskANN reports k-recall@k against its own metric. Our target is recall of the exact *lagged* top ten, so comparisons must be computed against the exact scan.
