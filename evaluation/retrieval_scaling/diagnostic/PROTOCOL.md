# R2-D: why the HNSW prefilter failed — candidate metric or ANN approximation?

Status: **FROZEN** in the commit that adds this file, before any R2-D measurement.

**Diagnostic follow-up on consumed data, not an independent confirmation.** It reuses R2's
data, database, queries, galleries and representations (`morlet_r2`, loaded by R2; nothing
is re-loaded, re-downloaded or re-indexed). R2's results are known; this study can only
explain them, not confirm them.

## Why

R2 found recall@10 0.743 for H-1000 (HNSW zero-lag shortlist of 1000, exact lag
re-ranking) at 234,762 windows. Two causes are confounded:
- **candidate-metric loss:** lag-tolerant neighbours are not among the zero-lag top K even
  when that top K is exact;
- **ANN loss:** HNSW does not return the exact zero-lag top K.

R2's Z arm also came from HNSW candidates in `relaxed_order` without an explicit sort, so
it was neither exact nor reliably ordered zero-lag retrieval.

## Arms (fixed now)

Unchanged from R2: the 100 queries (stored in `../results/bench.json`), tiers 0–3,
float16 envelopes, lag 30 (fraction 0.075), tie-breaking `(distance, recording_id,
start_sample)`, exclusion of windows overlapping the source interval, greedy overlap
suppression, top 10.

- **A (reference):** R2's stored A lists (exhaustive lag-tolerant scan). A is
  deterministic; it is re-run for the first 10 queries at tier 0 and must match exactly,
  otherwise the run stops.
- **E-K, K ∈ {100, 1000}:** exhaustive zero-lag cosine top-K on the side table:
  `ORDER BY v <=> q, recording_id, start_sample LIMIT K` with `tiers @> ARRAY[t]`,
  index scans and bitmap scans disabled for the statement, then the same exact lag
  re-ranking, exclusion and suppression as R2's H.
- **H-K, K ∈ {100, 1000}:** R2's HNSW shortlist, same settings (`ef_search = max(K,100)`,
  `iterative_scan = relaxed_order`), re-run so that its candidate set can be measured,
  then the same re-ranking. Candidates are re-sorted explicitly by
  `(cosine, recording_id, start_sample)` wherever their order matters.
- **ZE:** exact zero-lag top 10: E-1000's candidates in cosine order, then exclusion and
  suppression.
- **ZH:** the same from H-1000's candidates, explicitly sorted (R2's Z, corrected).

## Plan check (before timing)

For one query per tier, `EXPLAIN (ANALYZE, FORMAT JSON)` of the E-1000 statement must show
a sequential scan of `r2_hv` with no index node, and scanned rows (returned by the scan
plus removed by its filter) equal to the table's row count; the qualifying rows must equal
the tier's window count. The plans are saved. If a plan uses an index, the run stops.

## Measures, per tier and arm

- **Final recall@10** against A.
- **Coverage:** fraction of A's top 10 present in the shortlist (before re-ranking).
- **Covered but lost:** A items in the shortlist yet missing from the final top 10
  (interaction of a partial shortlist with ranking, exclusion and suppression).
- **Returned shortlist size** (mean, min).
- **HNSW–exact agreement:** |H-K ∩ E-K| / |E-K|.
- **Latency** p50/p95, warm (one untimed pass, then one timed pass), client wall time,
  with the path stated: E = sequential scan + top-K sort + in-database re-rank; H = HNSW
  index scan + in-database re-rank.

## Interpretation rules (stated now)

At tier 3 with K = 1000:
- **ANN approximation is the main loss** if E-1000 recall − H-1000 recall ≥ 0.10.
- **The zero-lag candidate metric is the main loss** if E-1000 recall < 0.95 and the
  difference is < 0.10.
- Otherwise both are reported as contributing.
Coverage minus final recall identifies loss from interactions, not from the shortlist.

## Not in R2-D

No HNSW tuning, no larger K, no new index or data, no change to R2's committed results.

## Amendment 1 (committed after the run above, before H\* is measured)

**Finding from the first run:** `EXPLAIN` of the H statement (R2's stage 1, unchanged)
shows `Seq Scan on r2_hv` + `Sort`, not the HNSW index. The query vector comes from a
subquery, which pgvector's index scan cannot use as its ordering key. R2's H arms and this
study's H arms were therefore **exact** zero-lag shortlists, which is why they match E-K
exactly. R2's conclusions about HNSW do not hold; its numbers measure the zero-lag
candidate metric only.

**Added arm H\*-K, K ∈ {100, 1000}:** the same statement with the query vector passed as a
`halfvec` parameter (fetched first by primary key), so that the existing index
`r2_hv_hnsw` can be used; same `ef_search = max(K, 100)` and `iterative_scan =
relaxed_order`; candidates sorted explicitly; same re-ranking. No new index, no tuning.
- **Plan check:** for one query per tier, `EXPLAIN` must show an index scan using
  `r2_hv_hnsw`; otherwise H\* is reported as not exercisable at that tier (the planner may
  prefer a sequential scan for selective tier filters) and its numbers are not used.
- **Measures:** as for H, plus agreement with E-K.
- **Interpretation:** the rules above, applied to H\*-1000 instead of H-1000.
