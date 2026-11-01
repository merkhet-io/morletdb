# Diagnostics D1 and D2 — protocol

Status: **FROZEN** in the commit that adds this file, before either diagnostic runs. Both run after E1,
on the same build (`main` 68594d7), the same server configuration (defaults) and the **development
database only** (`morlet_r2`, 234,762 windows, 100 dev queries). They are diagnostics: they explain or
qualify E1's results and decide documentation and next engineering steps; they do not tune any
parameter, and nothing is run on held-out data.

They were selected after a code audit and a literature review (`research/paper/related/`) as the two
questions that the existing evidence cannot answer.

## D1. Where does probe-search time go, and what happens to the candidates?

**Question.** For the frozen probe configuration, how is wall time divided between query setup, routing
(eligible-id list and count), the 13 index statements, exact re-ranking and suppression; and how many
candidates survive each stage? Can the fixed budget leave fewer than `top_k` results?

**Hypothesis (falsifiable).** Re-ranking is not the largest component: the 13 index statements together
take more than half of the instrumented function's time. Short results (fewer than 10 rows) occur in
no dev query.

**Method.** An instrumented copy of `morlet_search_envelope_probes` in a separate schema `diag`
(same SQL statements and settings, plus `clock_timestamp()` stamps and counters), and the product
function itself for comparison. Per query (one untimed warm-up pass, then two passes):
- stage times: setup (read interval, envelope), routing (eligible ids, bounded count), each probe
  statement, re-rank statement, suppression loop;
- counts: rows per probe, unique candidates, candidates after source exclusion, rows considered by
  suppression, rows returned;
- for one query, `EXPLAIN (ANALYZE, BUFFERS)` of one probe statement with and without the
  `recording_id = ANY(eligible)` predicate, to estimate the predicate's share of index-statement time;
- instrumentation overhead = instrumented total − product function time (paired, same query).

**Limits stated in advance.** ANN graph traversal, heap fetches and visibility checks happen inside one
pgvector index-scan node and cannot be separated without patching pgvector; candidate overlap between
probes is not evidence of shared traversal work.

**Decision it informs.** Whether the next engineering step should target index statements (fewer or
batched probes, coordinated budgets), re-ranking, or per-call overhead; whether the contract needs a
short-result indicator.

## D2. Does correlation between filter and query neighbourhood change filtered probe search?

**Question.** At equal filter selectivity, does routed probe search behave differently when the filter
keeps many, few or a typical number of the query's unfiltered near neighbours?

**Filters (synthetic, query-dependent choice among real metadata predicates).** For each dev query,
take the unfiltered exact top 100. Among the 14 day filters `{"day": d}` (about 7% of windows each,
above the routing threshold, so the probe path runs), choose the day with the most of those neighbours
(**positive**), the fewest (**negative**, ties broken by smallest day), and a random day (**neutral**,
fixed seed 20261101). The same with the 4 station filters `{"station": s}` (about 25%). The choice of
predicate uses the query's neighbourhood and is therefore synthetic; it probes behaviour, not a real
workload.

**Hypothesis (falsifiable).** Mean recall@10 of the routed probe function against the exact filtered
search differs by less than 0.05 between positive and negative day filters, and p95 latency by less
than 2×.

**Metrics.** Recall@10 against `morlet_search_envelope_interval` with the same filter (mean, minimum,
queries below 0.9), short results, p50/p95 latency of the routed function, eligible windows, share of
unfiltered top-100 inside the filter.

**Decision it informs.** Whether routing by eligible-window count alone is adequate in the tested
workload, or whether the router or per-probe budget must account for neighbourhood correlation
(documented as a limitation and next step; no learned optimiser is built).

## Budget and stop rule

Together at most 60 minutes of measurement. One run each; no re-runs with changed parameters. If a
diagnostic errors, fix the harness, log a deviation here, and re-run once.

## Deviations

(none yet)
