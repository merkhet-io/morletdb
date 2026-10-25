# Wave-cell posting index PoC

Status: **offline experiment**, not a PostgreSQL index or supported retrieval API.
Implements a bounded temporal reference and an approximate posting-list candidate
experiment from [the proposal](advanced-retrieval.md). It does not implement the
A1 database migration or persistence/publication lifecycle. Those gates still
apply before integrating an index with the recording SQL API.

## Run

From the repository root:

```sh
python -m pip install -r evaluation/requirements.txt
cargo build --locked --release -p morlet-core --example features
python evaluation/wave_index.py --size 128 --output wave-index-output/report.json
python -m unittest discover -s evaluation -p 'test_wave_index.py'
```

The deterministic demo contains a localized burst, its exact repeat, its time
reversal and tone distractors. Reversal preserves pooled energies but changes
their temporal positions. The report asserts this control and compares indexed
results to exhaustive temporal retrieval. Synthetic order discrimination is not
field relevance, diagnosis or a production capacity result.

Rust owns normalization and extraction. `morlet::extract_with_cells` obtains
integer coordinates directly from the same extraction loop used by the frozen
`morlet-energy-v1` signature. The default extractor and serialized signature are
unchanged. The evaluation adapter additionally emits experimental cells with
band, start/end coefficient-center coordinates, support radius and mean energy.
The Python harness neither computes wavelets nor implements another raw store.

## Two retrieval plans

For a cell j in band b, x[j] = log1p(mean energy). Its weight is its duration
divided by the total valid-center duration in band b, then divided by twelve.
Temporal distance is sqrt(sum(weight[j] * (query[j]-candidate[j])²)). All twelve
bands have equal total weight. Only identical complete layouts, window lengths,
sample rates, channel, units, normalization and representation specifications
are compatible. Unsupported boundary regions are absent, never padded with zeros.

1. **Exhaustive temporal reference:** score all eligible windows with that metric.
   Deterministic ties use recording ID and start sample. Exclude overlapping source
   intervals and apply greedy overlap suppression before final top-k.
2. **Approximate cell postings:** quantize each cell as floor(log1p(energy)/bin_width).
   A posting key contains band, local start/end and code, preserving within-window
   temporal position. Visit query-key postings, count equal-cell votes, take the
   configured candidate budget (ties by source coordinates), then rerank candidates
   with the same full temporal metric and overlap rules as the exhaustive plan.

The quantizer is fixed, not fitted on evaluation data; its width is in the spec
namespace. This first experiment has no adjacent-bin probes, learned codebook,
hierarchical summaries or arbitrary A-before-B motif/interval-join language.
Sign/phase information remains absent: opposite-polarity signals can still match.
This is a candidate index over localized energy cells, not coefficient tokens,
an invertible transform or a general motif database.

Equal codes are only candidate evidence. Bin boundaries, common silence cells,
vote ties and candidate caps can lose exact neighbors. Queries with no shared
codes return no approximate candidates rather than silently scanning everything.
Reranking cannot recover discarded candidates. Even when all neighbors happen
to be recovered, the plan remains labeled approximate.

## Snapshot, provenance and budgets

`Spec` has a canonical SHA-256 identifier including representation/metric versions,
normalization, rate/channel/unit, window and quantizer width. The fully built
in-memory generation includes selected recording IDs, parent starts, raw-window
SHA-256 (little-endian float64 samples), metadata, cells and features. These are
bounded selected windows, not a claim of continuous full-recording coverage.
Generation identity is independent of input enumeration order. There is no
on-disk serialization, incremental update or restart recovery for this index.

Cell coordinates are local half-open coefficient-center intervals. Add the parent
window start for global coordinates; extend by support radius for filter input
dependency. Per-window normalization additionally depends on the whole parent
window. Query and gallery windows must use the same normalization/layout.

Limits: 4096 selected windows, 1,000,000 posting entries, 1..100 output hits;
query posting visits default to 1,000,000 and candidates to 128. Candidate budgets
bound reranking, not posting-list traversal. Common cells can cost more than an
exact scan. Posting-budget exhaustion raises an error and returns no partial
top-k. Query results expose mode, exhaustive/incomplete flags, eligible/scored
counts, candidate intervals, visits, budgets, spec/generation IDs and scores.

The offline process has access to every supplied window. Metadata filtering and
source exclusion are investigation semantics, not authorization. There is no
RLS/security boundary; do not expose this harness as a multi-tenant service.
SQL integration requires the proposal's source/derived permission and atomic
publication contracts, upgrade migration and deletion/rebuild behavior.

## What to read in the report

- Pooled versus temporal distance for the reversed-burst control.
- Candidate recall@3 and final result recall@3 against the exact temporal top-3,
  separately from semantic/human relevance. Exact tie-breaking fixes the oracle.
- Scored windows and posting visits as the candidate budget increases.
- Extraction, index-build, exhaustive query and indexed-query durations.
- Posting keys/entries, versions, parameters, source hashes, host and failures.

The timings are single-query Python harness diagnostics, with no cache control,
confidence intervals or speedup assertion. Extraction and index construction are
reported separately; no persistent size or bytes/sample estimate is invented from
Python containers. The exact path intentionally remains available even if postings
are slower. CI uploads the JSON report; it checks correctness rather than enforcing
a latency or minimum-recall threshold on this uncalibrated candidate plan.

## First reproducible synthetic result

The [committed report](../evaluation/reports/wave-index-synthetic-v1.json) records
127 gallery windows, 186 cells per window, 1707 posting keys and 23,622 posting
entries. On this one controlled query, pooled burst/reversal distance is
2.3e-14, while temporal distance is 1.1807. The exact repeat ranks first.

| Candidate budget | Scored windows | Posting visits | Candidate recall@3 | Final recall@3 |
|---|---:|---:|---:|---:|
| 1 | 1 | 17,835 | 0.333 | 0.333 |
| 4 | 4 | 17,835 | 1.000 | 1.000 |
| 16 | 16 | 17,835 | 1.000 | 1.000 |
| 127 | 127 | 17,835 | 1.000 | 1.000 |

The exact scan scores 127 windows. Budget four recovers this query's exact top-3
with fewer full scores, but traverses roughly three quarters of the index entries
because many quiet cells share codes. This is an important skew/cost limitation,
not evidence of an asymptotically efficient index. Single-run timings in the JSON
are diagnostic only. This width/budget is not tuned or validated on held-out data;
no generalized recall, field relevance or speedup is established.

## Use selected approved recordings

`evaluation/wave_index.py` also exposes `extract_windows`, `Spec` and `WaveIndex`.
Supply a bounded list of dictionaries containing `id`, integer global `start`,
`samples` as a Python list, and optional string metadata. Each list must contain
exactly `spec.window` raw samples from a known compatible acquisition cohort.
Use the existing recording client's `read_interval` to obtain those samples;
there is no new SQL write path. The caller supplies/cohort-checks rate/channel/unit.
The CLI demo itself does not contact PostgreSQL or download third-party data.

Build an index from gallery windows and query an independently extracted window.
Use `search(query)` as the exhaustive oracle and
`search(query, approximate=True, candidate_budget=...)` for candidate experiments.
Do not put a query's overlapping source windows in a gallery unless the default
source exclusion is desired; for held-out evaluation, split whole runs/assets,
not random neighboring windows. Do not share reports containing private metadata
or source identities without permission.

Next promotion gates: test approved run-separated recordings with negatives,
measure repeated end-to-end cost and recall against this exact reference, then
decide whether native/Rust postings and PostgreSQL representation generations are
justified. The existing CWRU baseline and this synthetic demo do not validate
wave-token retrieval as a product advantage.
