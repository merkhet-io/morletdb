# Episodic memory and graded compaction: design

Status: design, being implemented in phases (issues #215–#221, #204, #219). The rationale, the compaction ladder
and the evaluation are in the second report, [Machines with a Memory](../research/paper2/README.md). This page
fixes how the system is built. Nothing here changes what existing functions return for data at level 0.

## Principles

1. **Raw samples are exact until a declared policy says otherwise.** Every move down the ladder is a recorded
   step (the ledger), names its policy version and reason, and is visible to every later read, search and scrub.
2. **Fail closed, and say why.** Reading samples that were compacted raises an error naming the level and the
   ledger entry; statistics come from kept summaries where they are exact; search does not see what was removed
   from search, and a coverage report says which periods it could not see.
3. **The database applies moves; policies decide them outside it.** The SQL layer offers one primitive that moves
   episodes between levels transactionally and writes the ledger. Policies (scoring, clustering, budgets) run in a
   worker process and are ordinary, versioned code, so they can be compared in experiments without changing the
   extension.
4. **Only whole chunks are compacted.** Episodes need not be chunk-aligned; a move affects the chunks that lie
   entirely inside the episode. Edge chunks stay at the higher level.

## Data model (per collection, opt-in: `morlet_enable_memory(collection)`)

| Table | Holds |
|---|---|
| `<c>_episodes` | `episode_id`, `recording_id`, `start_sample`, `end_sample`, `level` (0–4), `context` (jsonb), `importance`, `exempt` (event library), `cluster_id`, `representative`, `distance_to_representative`, timestamps |
| `<c>_ledger` | one row per move: `run_id`, `episode_id`, `from_level`, `to_level`, policy name and version, reason (the scores that decided it, jsonb), checksum of the removed data, optional location of a cold copy, time |
| `<c>_memory_runs` | one row per policy run: policy, budget, dry run or not, status, summary |
| `<c>_kept_summaries` | chunk summaries (count, min, max, compensated sums) of chunks whose samples were removed at levels 2 and 3 |

Episodes come from three sources: registered directly (`morlet_add_episodes`), derived from context segments,
or derived from labels. A labelled incident is an exempt episode (event library).

## The levels in the database

| Level | Rows that remain | Reads | Statistics | Search |
|---|---|---|---|---|
| L0 full | everything | exact | exact | yes |
| L1 not searchable | chunks; envelopes, probe rows and feature segments of the episode's interval are deleted, and builders skip it | exact | exact | no |
| L2 represented (member) | the cluster's representative stays at L0/L1; for the member, the chunks are deleted, their summaries kept, and the member keeps its context, time and distance to the representative | error naming L2 and the representative | exact for chunk-aligned intervals (kept summaries) | through the representative |
| L3 summarized | kept summaries only | error naming L3 | exact for chunk-aligned intervals | no |
| L4 gone | the episode row and its ledger; optionally a checksummed cold copy outside the database | error naming L4 and the cold copy | no | no |

`morlet_verify` (#193) treats removed chunks that are covered by ledger entries as compacted, not as gaps, and
checks kept summaries like stored ones.

## Moving episodes: one primitive

`morlet_set_level(collection, run_id, moves jsonb)` applies a batch of moves in one transaction. Each move names an
episode, the target level and, for L2, the representative. It checks that moves go down only, that exempt episodes
stay at L0 or L1, and that a representative is at L0 or L1. After all moves of a call it checks that no L2 episode is left
with a representative above L1, so a representative cannot be compacted while it still represents an episode (in a
later call, the same call or a dry run); the members must leave L2 in the same call. A representative is locked while
it is assigned, so concurrent moves cannot break this either (#440). Envelope builds keep their grid after compaction: the builder accepts
missing windows that it would skip itself, and still refuses another stride (#441). It removes the rows of that level, writes one ledger row
per move with the checksum of what it removed, and returns what it did. With `dry_run` it returns what it would do
and how many bytes that frees, and changes nothing. Moving up happens only by restoring from a cold copy or by
rebuilding derived data, both recorded in the ledger.

## The policy engine (worker)

The engine runs outside the database (`tools/memory/`, Python), pulls a run, decides moves and applies them through
`morlet_set_level`. Its structure follows the compaction design of RisingWave's Hummock storage engine
(Apache-2.0; [repository](https://github.com/risingwavelabs/risingwave), `src/meta/src/hummock/compaction`,
`src/storage/src/hummock/compactor`): the design is adopted, the code is our own.

- **Selectors** decide *why* a run happens: storage budget exceeded, age (the retention baseline), manual request,
  emergency (free space critical).
- **Pickers** decide *what* moves: they score episodes with the keep-value and choose moves until the budget is met.
- **Filters** are composable vetoes and rules, the counterpart of Hummock's `CompactionFilter`: exemptions (event
  library), minimum age, protected contexts.
- **Tasks** carry a run: dry run first, then execution in resumable batches with progress, validated before they
  are applied.

Keep-value per episode (version 1, all terms in [0, 1], weights part of the policy version):

    v = w_n·novelty + w_i·importance + w_u·use + w_c·context_rarity − w_a·age

Novelty is the distance to the nearest kept episode under the search distance (computed with the existing exact
search). Importance comes from labels, confirmed findings and the event library; use from retrievals and citations;
context rarity from the frequency of the episode's context values. Clustering for L2 uses k-medoids under the same
distance, so a representative is always a real episode.

Baseline policies for the evaluation (study F1): recency only, random (reservoir), novelty, novelty with importance.

## Phases

1. **SQL layer (SQL 0.3.5):** memory tables, episodes, `morlet_set_level` for L1, L3 and L4 and L2 members, kept
   summaries, ledger and runs; reads, statistics, builders and `morlet_verify` aware of levels; tests for every
   level and every refusal.
2. **Policy engine:** selectors, pickers, filters, tasks; keep-value v1; the four baseline policies; k-medoids
   clustering; dry runs.
3. **Study F1:** frozen protocol, then the measurements that become version 0.2 of the second report.
4. **Later:** calendar roll-ups (#219), cold tier on object storage (#204), event-library workflow (#216),
   investigation write-back (#217), coverage in search results (#215), ranking option (#218).
