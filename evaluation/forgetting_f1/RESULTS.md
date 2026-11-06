# F1 results: what forgetting costs recall (confirmation)

Protocol: [`PROTOCOL.md`](PROTOCOL.md), frozen in `235286f` before either run. One run per dataset, 2026-11-05,
image `sha256:0823becddc98…` (recorded in each result), policy version 1. Decision rule applied by
[`analyze.py`](analyze.py); raw results and per-query values in [`results/`](results/). No deviations.

## Outcome under the decision rule

**Recommended default: recency.** The keep-value (novelty+importance) is not better than recency on rare queries
at 50% and 25% on both datasets: on PyScrew s02 it is worse at 50% and cannot reach 25%. As the protocol says, the
keep-value is not built further as a default. It is the only policy that keeps every known event searchable, and
on seismic data it is better on rare queries at the budgets it reaches below 50%; both are reported as measured.

## PyScrew s02 (surface friction)

12,500 cycles in 500 recordings, 2,988 NOK (24%). Rare queries: 2,053 NOK cycles in 169 workpiece groups; typical:
300 OK cycles. Run time 21 min.

| Budget | Policy | Kept | NOK searchable | Nearest-hit ratio rare / typical (median) | Rare vs recency [95% CI] | Verdict |
|---:|---|---:|---:|---|---|---|
| 50% | recency | 50.0% | 46% | 1.01 / 1.04 | | |
| 50% | random | 49.9% | 37% | 1.06 / 1.06 | −0.058 [−0.089, −0.028] | worse |
| 50% | novelty | 50.0% | 65% | 1.02 / 1.25 | −0.678 [−0.811, −0.557] | worse |
| 50% | novelty+importance | 50.0% | **100%** | 1.00 / 1.27 | −0.052 [−0.094, −0.014] | worse |
| 25% | recency | 24.9% | 13% | 1.41 / 1.28 | | |
| 25% | random | 24.8% | 15% | 1.31 / 1.26 | +0.077 [+0.014, +0.135] | better |
| 25% | novelty | 24.8% | 45% | 1.66 / 1.87 | −0.927 [−1.109, −0.761] | worse |
| 25% | novelty+importance | 29.8% | **100%** | 1.00 / 1.49 | | unreachable |
| 10% | recency | 9.9% | 4% | 1.82 / 1.75 | | |
| 10% | random | 9.9% | 4% | 1.72 / 1.73 | +0.043 [−0.027, +0.110] | no difference |
| 10% | novelty | 9.8% | 22% | 3.32 / 2.70 | −2.105 [−2.321, −1.904] | worse |
| 5% | recency | 4.9% | 0.3% | 3.70 / 3.70 | | |
| 5% | random | 4.8% | 0.2% | 3.42 / 3.68 | +0.772 [+0.327, +1.205] | no difference (fewer known events) |
| 5% | novelty | 4.9% | 1% | 4.38 / 4.24 | −1.789 [−2.555, −1.087] | worse |

Novelty+importance stops at 29.8% (the NOK cycles alone) for every budget below that. Differences are means over
groups of recency's ratio minus the policy's, so positive favours the policy; means are pulled by tails (an
unsearchable answer counts as the dataset's largest finite ratio plus 1), which is why a policy with the same median
can be "worse".

## Seismic held-out (CX, 2021-03-01..14, EMSC events)

59 recordings, 7,849 episodes of about 10.2 min, 563 with a known event (7.2%). Rare queries: 116 in 92 event
groups; typical: 300. Run time 2 h 11 min.

| Budget | Policy | Kept | Known searchable | Nearest-hit ratio rare / typical (median) | Rare vs recency [95% CI] | Verdict |
|---:|---|---:|---:|---|---|---|
| 50% | recency | 50.0% | 45% | 1.00 / 1.00 | | |
| 50% | random | 50.0% | 49% | 1.04 / 1.00 | −0.006 [−0.042, +0.032] | no difference |
| 50% | novelty | 50.0% | 53% | 1.07 / 1.07 | −0.029 [−0.072, +0.016] | no difference |
| 50% | novelty+importance | 50.0% | **100%** | 1.00 / 1.05 | +0.014 [−0.023, +0.054] | no difference |
| 25% | recency | 25.0% | 23% | 1.12 / 1.10 | | |
| 25% | random | 25.0% | 24% | 1.14 / 1.10 | −0.014 [−0.062, +0.036] | no difference |
| 25% | novelty | 25.0% | 26% | 1.17 / 1.18 | −0.052 [−0.114, +0.008] | no difference |
| 25% | novelty+importance | 25.0% | **100%** | 1.05 / 1.16 | +0.069 [+0.024, +0.117] | **better** |
| 10% | recency | 10.0% | 9% | 1.21 / 1.25 | | |
| 10% | random | 10.0% | 9% | 1.25 / 1.23 | −0.007 [−0.044, +0.033] | no difference |
| 10% | novelty | 10.0% | 10% | 1.35 / 1.36 | −0.117 [−0.179, −0.055] | worse |
| 10% | novelty+importance | 10.0% | **100%** | 1.10 / 1.26 | +0.192 [+0.121, +0.271] | **better** |
| 5% | recency | 5.0% | 4% | 1.34 / 1.33 | | |
| 5% | random | 5.0% | 3% | 1.32 / 1.34 | +0.083 [+0.012, +0.161] | no difference (fewer known events) |
| 5% | novelty | 5.0% | 4% | 1.51 / 1.49 | −0.133 [−0.213, −0.048] | worse |
| 5% | novelty+importance | 7.5% | **100%** | 1.10 / 1.27 | | unreachable |

On typical queries novelty+importance costs little here (no difference demonstrated at 25% and 10%).

## Reading

- **Known events.** Exempting the event library is what keeps it: novelty+importance keeps 100% at every budget it
  reaches; recency keeps roughly the budget's share (46% → 0.3% on s02, 45% → 4% on seismic).
- **Novelty alone does not help.** Static nearest-neighbour novelty is worse than recency or no different, on both
  datasets, for rare and typical queries. It removes the near-duplicates that are the nearest neighbours of rare
  queries too.
- **Importance helps where the events are rare.** On seismic data (7% known episodes) the keep-value is better on
  rare queries at 25% and 10% with little cost on typical ones. On s02 (24% NOK) the event library takes most of
  the budget: at 50% the rare-query answers are slightly worse on average (same median) and typical answers degrade
  clearly, and below 30% the budget cannot be met without forgetting events.
- **Random vs recency** shows no consistent difference; the single "better" (s02, 25%) is not repeated on seismic
  data.

Not tested here: dynamic novelty (against the kept archive rather than the full one), summaries (level 2) as
representatives, and budgets as a share of non-event data. Any of these is a new, separately frozen study.
