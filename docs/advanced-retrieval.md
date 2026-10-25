# Advanced signal retrieval and wave-token indexes

Status: **Proposed**. Date: 2026-10-25. This is a design for review, not an
implemented API, migration, benchmark result or production commitment.

## Decision proposed

Extend MorletDB's continuous-recording workflow with versioned temporal
representations and explicit retrieval plans. Treat wave tokens as rebuildable
derived indexes over authoritative recordings. Keep the portable Rust core,
PostgreSQL 17 integration and thin Python tooling; do not introduce another
storage engine or require a vector service.

Start with a bounded, exhaustive temporal representation experiment. Introduce
token candidate indexes only after measuring their benefit and recall. Preserve
the existing exact pooled-feature search as a reference and compatibility path.

The product hypothesis is that engineers need to retrieve **where signal
patterns occur, in what order, at which scales, and across which channels**.
Neither a wavelet tokenizer nor a new database category is itself the customer
benefit.

## Current implementation and the missing bridge

The [architecture](architecture.md) defines an append-only, uniformly sampled
single-channel recording with global half-open sample coordinates. Chunk
boundaries do not define search windows.

- `crates/morlet-core/src/morlet.rs` emits localized `Token` values containing
  offset, duration, frequency and mean energy. They pool up to 128 valid
  coefficients per band; valid support differs by band. These are continuous
  energy summaries, not discrete vocabulary IDs, complex coefficients or motifs.
- `Signature::features()` pools each window into 12 band energies and three
  statistics. Temporal arrangement is absent from the ranking vector.
- `extensions/pg_morlet/sql/streams.sql` persists raw chunks, pooled segment
  features and one extraction checkpoint per recording. It does not persist or
  index the localized tokens.
- Stream candidate search exhaustively ranks eligible stored segments. Its
  default budget is 4096, with explicit experiments up to 1,000,000; this is exact
  over that representation and segment grid, not exact waveform equivalence or
  a search over every sample offset.
- Recording identity and extraction configuration are currently coupled.
  Multiple representations need a schema migration, not just another column.

No existing numerical contract or golden fixture changes as a consequence of this
proposal. The original [design](design.md) and [roadmap](product-roadmap.md)
already identify temporal retrieval and harder negatives as future work.

## User questions and staged scope

| Question | Proposed mechanism | Stage |
|---|---|---|
| Find this burst pattern despite similar total band energy elsewhere | Ordered time-band energy comparison | A |
| Find motif A followed by B within 200 ms | Positional token postings and bounded interval join | B |
| Search a long archive coarsely, then refine candidate offsets | Hierarchical summaries plus detailed reranking | B |
| Find similar behavior using a trained tokenizer or encoder | Versioned codebook tokens or embeddings | C |
| Find a vibration burst coincident with a current transient | Explicit acquisition-group clock mapping and interval join | D |
| Export reproducible windows for a wavelet-pooling CNN | Dataset manifest and bounded raw-window reader | C |

These stages are evaluation gates, not a promise to implement all mechanisms.
Initial scope retains compatible channel, unit and exact sample rate. No implicit
resampling, time warping, RPM invariance, physical diagnosis or cross-unit matching.

## Representation design space

Distinguish three concepts:

1. **Energy cell:** localized mean wavelet energy with time/band coordinates.
   Existing core tokens are the starting evidence, with a new persisted format.
2. **Coefficient token:** a quantized transform coefficient with its position and
   scale. Signed/complex information and inverse-transform properties depend on
   the transform; current energy cells cannot supply them.
3. **Learned patch or motif token:** a codebook ID assigned to a signal patch.
   A learned ID does not inherently name a physical event or fault.

Stage A evaluates energy cells before selecting a discrete vocabulary. Quantizing
`log1p(energy)` into bins is a candidate for Stage B, not an established optimum.
Fit bins/codebooks on training recordings only and freeze them before evaluation.
Quantizer saturation, silence, unknown codes and missing data need explicit policies.

Morlet energy pooling, an invertible DWT, and a learned VQ tokenizer are separate
representation families. Do not claim reconstruction or compression guarantees
for pooled energies. A token's coefficient magnitude or energy is not calibrated
fault confidence.

### Logical records (proposed, not executable DDL)

| Record | Required fields and invariants |
|---|---|
| Recording | Stable identity, append-only raw data, sample rate, channel, unit, acquisition metadata |
| Representation specification | Content-derived ID; canonical format/version; transform family and implementation version; frequency/scale layout; support and boundary policy; normalization and window/stride; pooling; quantizer/codebook digest where applicable |
| Representation generation | Recording ID, spec ID, immutable generation ID, source extent and content digest, extraction state, committed coverage, status/failure |
| Energy cell / token occurrence | Generation ID, parent window start/end, global sample start/end, band/scale ID, value or code ID, validity/support metadata |
| Embedding | Generation ID, source interval, model/checkpoint digest, shape, metric and normalization |
| Dataset manifest | Source identities/extents/digests, interval list, labels and provenance, split assignments, representation/model versions |

Code IDs are meaningful only inside their immutable vocabulary namespace. Never
compare equal integers from different codebooks as the same token. Frequency
coordinates must have defined physical semantics; a band number alone is insufficient.

Use integer sample coordinates in persisted occurrences; do not reconstruct exact
offsets from floating-point seconds. In Stage A, obtain coordinates directly from
the extraction loop without altering `morlet-energy-v1`. Identify the parent window:
overlapping windows and per-window normalization can give different values for the
same global sample location.

A cell's interval denotes coefficient centers pooled into it, not the full raw
dependency footprint. Preserve kernel support and window normalization extent.
Cross-chunk extraction must reproduce whole-window extraction. Do not stitch
independently normalized windows into a supposedly continuous coefficient stream.

### Migration and lifecycle

Separate acquisition identity from extraction specifications while keeping old
entry points pinned to their current representation. Backfill derived generations
from raw samples; verify counts, coordinates and parity before publishing.
Specify an extension upgrade script and rollback strategy in the implementation PR.

Raw append and derived publication must have explicit snapshot semantics.
Stage A can build synchronously over a frozen source extent in a transaction.
A failed build exposes no partially complete generation. Publish a generation
only after all required cells for its declared window grid exist. New appends
do not silently extend its declared coverage.

If asynchronous extraction becomes necessary, introduce durable jobs, retries,
per-spec coverage and backpressure in that separate change. Distinguish received
raw extent from searchable extent. Tail windows without full support remain
unsearchable; never treat missing cells as zero energy.

Rebuilds publish a new generation atomically; queries pin one generation per
recording/spec. Deletion removes all derived occurrences and embeddings for the
source. Permissions and RLS apply to sources, derived indexes and raw reads;
candidate counts, postings and cross-channel joins must not reveal hidden data.
Use invoker privileges consistent with the current SQL path.

## Retrieval semantics and execution

### Stage A temporal reference

Represent a fixed-size window as an ordered time-band energy grid. Existing
band-specific support and partial blocks require an explicit layout: compare
only identical layouts initially, and mask unsupported regions. Define distance
as a versioned, weighted mean squared difference of transformed energies over
valid cells, with documented weights and a minimum comparable coverage.
Report temporal distance separately from current pooled-feature distance.

Keep equal sample rates, window sizes, layouts and normalization. No alignment
search or DTW initially. The reference exhaustively scores every eligible stored
window; deterministic ties use recording ID and start sample. Preserve source
exclusion and apply overlap suppression before final top-k.

### Stage B token and hierarchical candidate indexes

A possible posting key is `(spec_id, band_id, code_id)`, mapping to recording,
generation, parent window and sample interval. Intersect rare postings before
bounded positional joins. Measure frequency skew: common silence tokens can
produce enormous posting lists. Add hierarchical time/scale summaries only if
they reduce measured work.

For a sequence query, define A-before-B as
`0 <= B.start - A.end <= max_gap_samples`; adjacency at a half-open boundary is
allowed. Define overlap as `max(starts) < min(ends)`. A self-join must not reuse
the same occurrence where distinct events are required. Variable durations,
repeated motifs and overlap suppression need explicit controls.

Approximate bins or embeddings generate candidates, followed by detailed scoring.
Reranking cannot recover a true match that candidate generation discarded.
No exactness claim follows from using raw samples at the final step.

Only use lossless pruning or lower bounds when proved for the chosen metric.
Otherwise expose the plan as approximate and measure its recall against the
exhaustive reference for the **same** representation, cohort and window grid.

### Proposed request/result contract

Names below are illustrative; no new public function is supplied by this document.

A request selects source interval or uploaded samples, representation spec,
metadata/cohort filter, retrieval mode, top-k, source exclusion, overlap policy
and work limits. Sequence requests additionally supply motif predicates and timing
relations. Unsupported mode/spec combinations fail explicitly.

A result contains source interval and sample rate; spec and generation IDs;
score and score semantics; matched cells/motifs and their coordinates; raw-data
provenance; candidate/scored counts; coverage/snapshot; effective budgets; and
whether the plan is exhaustive, approximate or incomplete.

Separate limits for posting visits, candidates, raw samples read, alignment work,
output hits and elapsed time. Default behavior is to fail explicitly on budget
exhaustion. Any opt-in partial response must carry an incomplete flag and reason,
and cannot be represented as the globally best top-k. Rejected candidates,
selectivity and reranking costs belong in an explain record.

A future planner may choose exact scan, positional postings, vector candidates
or coarse-to-fine execution. Do not implement a general optimizer in Stage A.

## Relationship to vector databases and model architectures

Vector search remains a useful candidate mechanism and benchmark. Qdrant already
supports multivectors and token-level MaxSim; it would be misleading to compare
only against a single pooled embedding. MaxSim alone does not enforce elapsed-time
or ordering constraints. Benchmark vector retrieval plus equivalent filtering and
reranking, including integration costs.

WaveToken demonstrates discretized wavelet coefficients for forecasting; TOTEM
demonstrates learned discrete signal representations. Neither proves a dedicated
token database is needed. Symbolic time-series indexing also predates this design:
iSAX is an important baseline and prior-art boundary.

For the battery SoC paper, MorletDB could supply raw voltage/current/temperature
windows, conditions, labels and evaluation splits. Its wavelet pooling acts on
intermediate CNN activations; precomputed raw-input wavelets do not reproduce it.
SPECTRE's optional DWT refinement likewise acts inside a neural forward pass.
Differentiable layers, GPU tensor execution and model training stay in the model
framework. MorletDB may supply datasets and retain predictions/embeddings with
provenance; it does not claim to accelerate those internal layers.

## Evaluation and promotion gates

Agree relevance thresholds and resource budgets before examining held-out results.
Publish data provenance, parameter manifests, raw reports, hardware and failures.

| Experiment | Controls and evidence |
|---|---|
| Temporal discrimination | A-then-B versus B-then-A; equal-duration reversed burst order with similar pooled energies; impulses, chirps, shifted events and silence |
| Retrieval quality | Held-out runs/assets; harder operating-condition negatives; source exclusion and overlapping-hit collapse; precision/recall@k, localization error, negative-query false matches |
| Candidate correctness | Recall@k against exhaustive temporal reference, separately from human relevance; report losses at each pruning stage and ties |
| Scale | End-to-end p50/p95 latency, extraction/ingest CPU, memory, bytes/sample, posting visits, raw bytes read, rebuild cost and concurrent-ingest behavior |
| Robustness | Chunk-boundary parity, support boundaries, partial tails, constants, saturation, corrupted generations, restart, rollback, RLS and deterministic ties |
| ML utility | Frozen manifests; splits by run/asset rather than random neighboring windows; tokenizer fitting on training only; throughput and reproducibility |

Compare statistics, FFT, pooled Morlet, ordered energy grids, symbolic/SAX-style
retrieval, pooled learned embeddings and token-level multivectors with temporal
reranking. Add raw-waveform or constrained-DTW references for tasks that need them;
cap their work explicitly. Compare quality at matched resource budgets rather
than only latency at unrelated recall levels.

Synthetic fixtures establish controlled discrimination and correctness, not field
usefulness. The existing CWRU slice ties simple statistics and does not validate
this proposal. Learned tokenization or a new index proceeds only if it improves a
predeclared quality/cost tradeoff on held-out data.

## Implementation sequence and acceptance

1. **A1 — representation separation:** design/test migration, immutable spec IDs,
   generation coverage, raw compatibility and permission contracts. Keep existing
   callers working and existing features numerically unchanged.
2. **A2 — temporal reference:** expose integer-coordinate localized energy cells in
   Rust, persist bounded generations in PostgreSQL, implement one exhaustive
   temporal scorer, and add Python inspection of matched cells. Pass cross-chunk
   parity, lifecycle and ordered-distractor tests; publish a baseline report.
3. **B — indexed retrieval experiment:** add one quantizer and positional posting
   index with bounded A-before-B queries. Demonstrate recall and end-to-end benefit
   against A2, including storage and verification costs. Retain exact fallback.
4. **C — ML bridge:** export pinned datasets and permit external tokenizer/model
   adapters; evaluate learned tokens and multivectors without requiring a new
   service. Freeze codebook/checkpoint identities and evaluation splits.
5. **D — synchronized channels:** require an explicit acquisition-group timebase,
   offsets, drift/uncertainty and gap policy before cross-channel joins. Current
   independent single-channel metadata does not establish synchronization.

The first implementation PR should target A1, followed by A2. B–D remain gated
experiments. If temporal representations fail to improve useful retrieval, retain
the evaluation findings and narrow or retire this direction.

## References and evidence boundaries

- [WaveToken, ICML 2025](https://proceedings.mlr.press/v267/masserano25a.html):
  wavelet coefficient tokenization for forecasting; not a retrieval-index result.
- [TOTEM](https://arxiv.org/abs/2402.16412):
  learned discrete time-series representations across analysis tasks.
- [iSAX](https://www.cs.ucr.edu/~eamonn/iSAX.pdf):
  multiresolution symbolic indexing and exact/approximate time-series search.
- [Qdrant multivectors](https://qdrant.tech/documentation/manage-data/vectors/):
  existing token-level vector storage and MaxSim comparison.
- [Wavelet-pooling CNN for battery SoC](https://doi.org/10.1016/j.est.2025.116202):
  architectural application; reviewed accessible abstract/method excerpts only.
- [SPECTRE v7](https://arxiv.org/abs/2502.18394v7):
  FFT token mixing with optional wavelet refinement; author-reported preprint results.

These sources motivate alternatives and establish related work. They do not
validate MorletDB performance, market demand, novelty or freedom to operate.
