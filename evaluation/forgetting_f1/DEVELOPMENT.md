# F1 development runs (exploratory, PyScrew s01; not frozen)

Harness [`f1_pyscrew.py`](f1_pyscrew.py); results in [`results/dev/`](results/dev/). Development data only (PyScrew
s01, Zenodo 15393134 v1.2.2, CC BY 4.0, MD5-checked); nothing here is a frozen result.

## What the first runs changed

1. **Chunks must not span episodes.** With cycles concatenated into 4,096-sample chunks no chunk lay inside an
   episode, and with 256-sample chunks the partial edge chunks of every cycle stayed: the ladder could not reach the
   budgets. Each cycle is now appended on its own, as a station sends it, so it is exactly one chunk.
2. **Windows spanning adjacent episodes** (cycles are shorter than a window plus stride) never left search, because
   the rule asked one episode to contain the window. Fixed in the extension (#255): a window leaves search when
   episodes out of search cover it completely and none at level 0 overlaps it.
3. **The event library must stay searchable.** Exemption allowed level 1 (not searchable), so at small budgets the
   importance policy moved the known events out of search. The engine's exemption now vetoes every move.
4. **Identity recall penalises redundancy by design.** Recall@10 against the full archive's identical windows
   treated every forgotten near-duplicate as a loss, so all policies looked alike. Answer quality (the distance of
   the hits, forgotten over full) and the searchability of known events are added; identity recall stays secondary.
5. **Label precision is confounded.** The share of NOK cycles among a NOK query's hits rises under every policy as
   OK cycles are forgotten; it measures the archive's composition, not relevance. Reported as secondary only.

## Second run (`f1_s01_20261105T170646.json`)

200 recordings, 5,000 episodes (911 NOK), 2.9 M samples; queries: 742 NOK and 300 OK cycles of at least 512 samples.

| Policy | Budget | Kept | NOK searchable | Distance ratio NOK / OK (median) | Identity recall@10 NOK / OK |
|---|---:|---:|---:|---|---|
| recency | 50% | 50% | 67% | 1.51 / 1.70 | 0.16 / 0.12 |
| random | 50% | 50% | 66% | 1.56 / 1.69 | 0.18 / 0.13 |
| novelty | 50% | 50% | 94% | 1.68 / 2.14 | 0.16 / 0.07 |
| novelty+importance | 50% | 50% | **100%** | 1.53 / 2.07 | 0.28 / 0.08 |
| recency | 25% | 25% | 41% | 1.85 / 2.54 | 0.08 / 0.03 |
| novelty+importance | 25% | 25% | **100%** | 1.63 / 2.33 | 0.25 / 0.06 |
| novelty+importance | 10%, 5% | **22% (not reachable)** | 100% | 1.63 / 2.41 | 0.25 / 0.05 |

Reading: novelty keeps the rare (failed) cycles; with importance every known event stays searchable and the answers
to rare queries degrade least, while answers to typical queries degrade more than under recency: the expected
trade-off, measured. Budgets below the size of the event library are not reachable by design and are reported, not
forced. Open before freezing: primary measures, the treatment of unreachable budgets, dynamic instead of static
novelty, and confirmation data (s05 has only 3 NOK cycles; held-out seismic data with the EMSC catalogue as known
events is the candidate).
