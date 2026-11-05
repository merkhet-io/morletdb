# F1: forgetting policies, retrieval regret against storage saved (DRAFT, not frozen)

**Question.** If MorletDB forgets actively, by graded levels instead of deleting, how much storage and index memory
can be saved before known events stop being findable? Which keep-value policy loses the least for the bytes it
saves? F1 measures this, so that forgetting policies are chosen from evidence, not taste.

Status: draft. It freezes (commit recorded here) before any measurement on the confirmation data; policies,
weights and thresholds are developed on development data only.

## Levels (issues #219–#221)

0 full · 1 not searchable · 2 represented (cluster medoid and outliers raw; members as time, context, distance,
summaries) · 3 summaries only (exact roll-ups) · 4 gone (ledger entry; cold copy optional).

## Policies compared (each at several budgets: 50%, 25%, 10%, 5% of the full archive's bytes)

- **P-recency:** oldest first, one level at a time (the baseline that plain retention gives).
- **P-novelty:** lowest novelty first (distance to the nearest kept neighbours, from the bounded-lag search).
- **P-novelty+importance:** as P-novelty, with labelled, incident and library episodes exempt and a recency
  decay; weights fixed on development data.
- **P-random:** random episodes, as a floor.

## Data

- **Seismic:** the development archive (dev, days 1–14) for development; the held-out stations and year for
  confirmation, with the EMSC catalogue as known events (as in E5 and E5-H).
- **Cycles:** PyScrew (screwdriving, CC BY 4.0, Zenodo record 15393134) scenario s01 for development, another
  scenario for confirmation, with its fault labels as known events. Download to be approved before development.

## Metrics

- **Retrieval regret:** for a frozen query set (all known-event queries, plus random and high-energy windows as in
  E1), recall@10 of the forgotten archive against the full archive's exact top ten; and the share of known-event
  queries whose relevant hits remain findable (at any level that search can reach).
- **Known-event survival (hard criterion):** every labelled event stays at level 0 or 1 under the importance
  policy; any loss is reported as a failure of that policy.
- **Drift visibility:** on cycle data with gradual change, the level track over the forgotten archive detects the
  change at the same cycle (± 1 %) as over the full archive.
- **Cost:** bytes and index memory per level, policy run time, ledger size.

## Decision rule

A policy is "better" than another at a budget if its recall@10 is at least 0.02 higher, with a bootstrap 95 %
interval excluding 0, on the confirmation data, and it loses no known event. The output is the regret-versus-
storage curve per policy and dataset, and the recommended default policy (or none, if none dominates
P-recency).

## Not in F1

Lossy compression of samples (forgetting removes or represents whole episodes; kept samples stay exact), learned
policies, multi-node archives.
