# F1: forgetting policies — what forgetting costs recall (FROZEN)

Frozen in the commit that changes this file's title to FROZEN, before any confirmation data was loaded into a
database. Development: PyScrew s01 and a smoke check of the seismic harness on one development recording
(`DEVELOPMENT.md`). Primary measures and confirmation data approved by the owner in chat on 2026-11-05.
Deviations are appended below.

## Question

If MorletDB forgets actively, by graded levels (`docs/memory-design.md`) instead of deleting by age, how much do the
answers to rare and to typical queries degrade at a given storage budget, and do known events stay findable? Which
policy loses the least?

## System under test

The extension's main branch with episodic memory (SQL 0.3.5; image `morletdb:mem` built from `main` after #255, id
`sha256:0823becddc98…`; the image id is recorded in every result) and the policy engine `tools/memory` (policy version 1) with the depth-first
picker: the lowest-valued episodes go straight to level 3 (summaries only) until the budget is met, so the most
valuable stay fully searchable; only when every movable episode is at level 3 do the lowest-valued go on to level 4.
A run applies its ordered moves in batches of at most 50 (sized by the dry-run estimates of what is still needed)
and measures the actual bytes after each batch, stopping once the budget is met. Up to 10 policy runs per
configuration.

## Policies

- **recency:** oldest first (the retention baseline). Age is recording time.
- **random:** a seeded uniform order (seed 20261105).
- **novelty:** lowest novelty first; novelty v1 = distance of an episode's first window to its nearest neighbour in
  the full archive (excluding itself), scaled to [0, 1] (a static approximation of "nearest kept episode").
- **novelty+importance:** novelty plus importance (weight 1 each); known-event episodes have importance 1 and are
  exempt: the event library stays searchable (the engine never moves an exempt episode).

Budgets: 50%, 25%, 10%, 5% of the bytes of chunks, kept summaries and envelopes of the full archive. A budget that
the exempt episodes alone exceed is not reachable for novelty+importance; it is reported as unreachable with the
size reached, not forced.

## Data (confirmation)

- **Cycles:** PyScrew s02 (surface friction; Zenodo 15393134 v1.2.2, CC BY 4.0, MD5
  `b5a239792b66fedadb5cf4ed4a61a36d`): 12,500 screwdriving cycles, 2,988 NOK. One recording per workpiece and screw
  position, cycles in run order, torque at 833.3 Hz; one episode per cycle, appended as its own chunk; NOK cycles are
  the known events; search windows 512 samples, stride 128. Rare queries: every NOK cycle of at least 512 samples
  (its first 512 samples); typical queries: 300 random OK cycles of at least 512 samples (seed 20261105).
- **Seismic:** the held-out CX gallery of BL1-C and E5-H (PB05, PB11, PB14, PB18, 2021-03-01..14, HHZ, 100 Hz) with
  E5-H's EMSC catalogue (`data/e5h-catalog`, SHA-256 as stored). Episodes of 15 chunks (61,440 samples, about 10.2 min)
  from each recording's start; a known-event episode contains the predicted P arrival (iasp91) of a catalogued event
  at that station. Windows 4,096 samples, stride 2,048. Rare queries: E5-H's rule (M ≥ 2.5 within 2°, P about 5 s
  into a stride-aligned window); typical queries: 300 random windows in episodes without known events (seed
  20261105). Query samples come from the source files.

## Measures

Primary:
1. **Known events searchable:** share of known-event episodes at level 0 after the policy (hard criterion for
   novelty+importance: 100% at every reachable budget).
2. **Answer quality:** per query, the distance of its nearest hit (outside the query's own episode) in the forgotten
   archive over the full archive's nearest (1 = something as similar is still found; infinite if nothing is
   searchable); median over rare queries and over typical queries, separately.

Secondary: the same ratio for the mean of the ten hits (penalises removing near-duplicates); identity recall@10 against the full archive's top ten (penalises removing redundancy by design); for
cycles, the share of NOK cycles among a NOK query's hits (confounded by the archive's composition); bytes, levels,
policy run time.

## Decision rule

At each reachable budget and dataset, compare each policy with recency on rare queries: the mean over groups of the
per-query difference (recency's nearest-hit ratio minus the policy's; an infinite ratio counts as the largest finite
ratio of that dataset plus 1), with a 95% bootstrap interval resampling groups
(10,000 resamples, seed 20261105; groups are workpieces for cycles and events for seismic rare queries). "Better on
rare queries" if the interval lies above 0 and its known events searchable are at least recency's; "worse" if below
0; otherwise "no difference demonstrated". The same comparison is reported for typical queries (the cost). The
recommended default policy is the one better on rare queries at 50% and 25% on both datasets, with its cost on
typical queries stated; if none is, the recommendation is recency (no keep-value) and the keep-value is not built
further.

## Use of the confirmation data

One run per dataset. No policy, weight, budget or measure is changed after it; anything further is labelled
exploratory.

## Changes before freezing (development data only)

- Answer quality was first the mean distance of the ten hits. On s01 it penalised novelty for what it is designed to
  do (near-duplicates fill a query's ten hits), so with the owner's approval on 2026-11-05 the nearest hit became
  primary and the mean of ten secondary (`DEVELOPMENT.md`, third run). This was decided after seeing development
  results and is disclosed as such.

## Deviations

(none yet)
