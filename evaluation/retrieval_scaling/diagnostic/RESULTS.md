# R2-D results: the shortlist loss is the zero-lag candidate metric, not ANN approximation

**Diagnostic follow-up on consumed data, not an independent confirmation.** Run on
2026-10-31 on R2's database, queries and galleries, as frozen in [PROTOCOL.md](PROTOCOL.md)
(7a7b276; harness 043fcbb), with Amendment 1 (fe5eb19; harness c2c2198, committed before
H\* was measured).

## What R2-D found about R2 itself

- **R2's "HNSW" arms never used the index.** Their query vector came from a subquery; pgvector
  cannot use that as an index ordering key, so the planner ran a sequential scan and an exact
  sort. R2's H-K were exact zero-lag shortlists and its Z was exact zero-lag retrieval
  (confirmed: E-K and H-K agree 1.000 on every query at every tier, and ZE = ZH = R2's Z).
- R2's A lists are reproducible: re-running 10 queries at tier 0 gave identical lists.

## Arms and execution paths

| Arm | Shortlist | Path (verified with `EXPLAIN`) |
|---|---|---|
| A | none: exhaustive lag-tolerant scan | R2's stored lists (PL/pgSQL scan, Rust scorer) |
| E-K | exact zero-lag top-K | sequential scan of `r2_hv` (all 234,762 rows read, tier filter), top-K sort |
| H-K | R2's statement | sequential scan + sort (no index, see above) |
| H\*-K | query vector as parameter | index scan on `r2_hv_hnsw` (`ef_search` = max(K,100), relaxed order), candidates re-sorted |
| ZE / ZH | E-1000 / H-1000 in cosine order, then exclusion and suppression | as E / H |

All shortlist arms re-rank with the same in-database exact lag distance, tie-breaking,
exclusion and overlap suppression. H\*-1000 is **not exercisable at tier 0**: the planner
chose a parallel sequential scan there (Amendment 1's plan check), so it has no numbers.

## Results (100 queries per tier)

Recall@10 against A; coverage = share of A's top ten inside the shortlist.

| Tier (windows) | E-100 | H\*-100 | E-1000 | H\*-1000 | ZE (= ZH) |
|---|---|---|---|---|---|
| 0 (4,217) | 0.714 | 0.716 | 0.977 | not exercised | 0.468 |
| 1 (16,868) | 0.638 | 0.637 | 0.866 | 0.873 | 0.447 |
| 2 (67,472) | 0.612 | 0.612 | 0.755 | 0.756 | 0.425 |
| 3 (234,762) | 0.605 | 0.604 | 0.743 | 0.744 | 0.451 |

- **Coverage equals final recall for every shortlist arm and tier**, and no query lost an
  A item that its shortlist contained ("covered but lost" = 0). Exclusion and overlap
  suppression caused no loss of their own here; all loss is A items absent from the
  shortlist.
- **Shortlist sizes:** every shortlist returned exactly K candidates; ZE and ZH returned 10
  results for every query.
- **HNSW vs exact shortlist agreement (H\* vs E):**

  | Tier | K = 100 mean (min) | K = 1000 mean (min) |
  |---|---|---|
  | 0 | 0.991 (0.94) | — |
  | 1 | 0.994 (0.95) | 0.889 (0.746) |
  | 2 | 0.991 (0.93) | 0.993 (0.949) |
  | 3 | 0.980 (0.89) | 0.996 (0.981) |

  Where HNSW's shortlist differs from the exact one, the swapped-in candidates were about
  as likely to be in A's top ten as those dropped (tier 1: 0.889 agreement, recall 0.873
  vs 0.866).

**Latency** (p50 / p95 ms, warm, one client; A from R2):

| Tier | A exhaustive | E-100 | E-1000 | H\*-100 | H\*-1000 |
|---|---|---|---|---|---|
| 0 | 54 / 54 | 56 / 59 | 239 / 248 | 68 / 72 | — |
| 1 | 209 / 211 | 82 / 120 | 361 / 485 | 46 / 54 | 275 / 282 |
| 2 | 858 / 866 | 73 / 148 | 318 / 555 | 34 / 38 | 255 / 274 |
| 3 | 2,832 / 2,858 | 86 / 125 | 330 / 394 | 26 / 31 | 231 / 338 |

E and H\* include fetching the query vector, the shortlist and the in-database re-rank of K
stored envelopes; at K = 1000 the re-rank dominates.

## Interpretation (rules frozen in the protocol)

At tier 3, E-1000 − H\*-1000 = −0.001 (< 0.10) and E-1000 = 0.743 < 0.95: **the zero-lag
candidate metric is the main loss.** At these budgets, an exact zero-lag shortlist and the
HNSW shortlist find the same lag-tolerant neighbours to within 0.007 recall; the shortlist
misses a quarter of the exact top ten because those neighbours are not among the 1000
nearest windows by zero-lag cosine.

What the controls separate:
- **Candidate-metric loss** = 1 − E-K recall: 0.26 at K = 1000 and 0.40 at K = 100 on the
  largest gallery.
- **ANN loss** = E-K − H\*-K recall: within ±0.007 everywhere measured.
- **Interaction loss** (exclusion, suppression, partial shortlist) = coverage − recall: 0.

## Limits

- Consumed data; one HNSW configuration; two budgets; one machine, warm, one client.
- Larger K, other indexes or shift-invariant first stages were out of scope and remain
  untested; nothing here shows what they would achieve.
- H\*-1000 has no tier-0 measurement (planner chose a sequential scan).

## Reproduce

```sh
MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/retrieval_scaling/diagnostic/diag.py \
    evaluation/retrieval_scaling/results evaluation/retrieval_scaling/diagnostic/results
MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/retrieval_scaling/diagnostic/hstar.py \
    evaluation/retrieval_scaling/results evaluation/retrieval_scaling/diagnostic/results
```
