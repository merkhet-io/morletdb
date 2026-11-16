# C8 results: the choice of `match_keys` changes the nearest past cases

**Descriptive measurement, under the rules frozen in [PROTOCOL.md](PROTOCOL.md) (`00aae8f`).**
On both datasets, every pair of context choices gets the verdict **"neighbours change"
at k = 10**: on average only 47–68% (Bosch) and 55% (PHM 2010) of the top-10 are the
same under two reasonable choices of `match_keys`. Under the protocol's rule, **the
context choice matters** on both datasets. A neighbour list is only interpretable
together with the context that produced it, so every result should state its
`match_keys`.

- **Harness:** `452aa6b`, committed before the full run. The full run was executed
  once, with no harness fault and no changes after the freeze.
- **Database:** `ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1` (SQL 0.3.11). The value
  rules are `simple-v2` (the collection default of this release).
- **Function:** `morlet_history_neighbors`, with `top_k` 51, the default score values
  (`duration_s`, `std`, `crest`, `kurtosis`) and `before_only` false.
- **Every number** is in `results/c8.json`. The neighbour files (3 MB and 16 MB) are not
  committed. Their SHA-256 are in `results/SHA256SUMS`; they are regenerated
  deterministically by `c8.py query`.

## Data as run

- **Bosch CNC** (`d60581d`):
  - 1,649 runs exported, every file's SHA-256 matching `SHA256SUMS`, none rejected;
  - history: 1,180 good runs of 2019–2020;
  - queries: all 469 runs of 2021, × 3 axes = 1,407 query segments;
  - load: 90 s.
- **PHM 2010** (c1–c6):
  - 1,890 cuts, none rejected (all have 7 columns and finite values). The SHA-256 of
    every file is in `results/phm_inputs_SHA256SUMS`;
  - every cut × 7 channels = 13,230 query segments;
  - load: 204 s. This is an **archive search**: a cutter's history includes its later cuts.
- **Product check:** the ranking was recomputed in Python from the stored values for the
  first 5 query runs (cuts) per dataset, on every channel and every choice. The ids and
  order agree in **60/60 (Bosch) and 70/70 (PHM)** lists, with distance error ≤ 1e-9
  (`results/check_*.json`).
- **Ties at the k-th place:** none, for any choice or k.

## Main table: Overlap@k = |A_k ∩ B_k| / k

Unit: a query run (cut), averaged over its channels. Mean over units, with Bosch's 95%
cluster-bootstrap interval over 43 (machine, operation) groups, or PHM's range of the six
per-cutter means.

Bosch context choices:
- **S:** no context (all history);
- **M:** same machine;
- **O:** same operation;
- **MO:** same machine + operation.

| Dataset | Pair | k | Overlap@k | Interval | Jaccard | Containment | Wider list outside narrower context | Verdict |
|---|---|---|---|---|---|---|---|---|
| Bosch | S–M | 10 | 0.62 | [0.57, 0.65] | 0.52 | 0.62 | 37% other machine | neighbours change |
| Bosch | S–M | 50 | 0.52 | [0.49, 0.54] | 0.38 | 0.52 | 48% | neighbours change |
| Bosch | S–O | 10 | 0.68 | [0.63, 0.73] | 0.59 | 0.68 | 28% other operation | neighbours change |
| Bosch | S–O | 50 | 0.54 | [0.49, 0.58] | 0.42 | 0.54 | 46% | neighbours change |
| Bosch | S–MO | 10 | 0.47 | [0.40, 0.53] | 0.37 | 0.47 | 53% other machine or operation | neighbours change |
| Bosch | S–MO | 50 | 0.28* | [0.24, 0.31] | 0.24 | 0.53 | 72% | most neighbours change* |
| Bosch | M–O | 10 | 0.50 | [0.44, 0.56] | 0.40 | 0.50 | (not nested) | neighbours change |
| Bosch | M–O | 50 | 0.33 | [0.30, 0.37] | 0.21 | 0.33 | (not nested) | most neighbours change |
| Bosch | M–MO | 10 | 0.64 | [0.58, 0.71] | 0.56 | 0.64 | 33% other operation | neighbours change |
| Bosch | M–MO | 50 | 0.35* | [0.32, 0.39] | 0.32 | 0.67 | 65% | most neighbours change* |
| Bosch | O–MO | 10 | 0.62 | [0.57, 0.68] | 0.53 | 0.62 | 37% other machine | neighbours change |
| Bosch | O–MO | 50 | 0.43* | [0.40, 0.48] | 0.40 | 0.80 | 57% | most neighbours change* |
| PHM 2010 | all cutters–same cutter | 10 | 0.55 | [0.45, 0.60] | 0.44 | 0.55 | 44% other cutter | neighbours change |
| PHM 2010 | all cutters–same cutter | 50 | 0.41 | [0.33, 0.48] | 0.28 | 0.41 | 59% | most neighbours change |

\* **The MO list is always shorter than 50.** Every machine + operation history has 9–42
runs (median list length 28), so Overlap@50 counts the missing places as not shared, as
the protocol fixed. Containment shows how much of that short list the wider top-50
contains: 53% (S), 67% (M), 80% (O). Even ignoring the length difference, a fifth to a
half of the same-context runs drop out of the wider list.

**Other short lists:**
- MO@10: 12 of 1,407 lists, minimum 9;
- O@50: 99 lists, minimum 48;
- PHM: none.

**Spread over queries (Overlap@10):**
- Bosch S–MO: median 0.47, 10th–90th percentile 0.13–0.77;
- Bosch M–MO: median 0.63, 0.33–0.93;
- PHM: median 0.57, 0.36–0.71.

## Reading (descriptive)

- **Two mechanisms.** A wider context brings in occurrences from other machines,
  operations or cutters: 28–53% of a wider Bosch top-10 and 44% of the PHM all-cutters
  top-10. It also rescales distances, because the history medians that scale each
  value differ. As a result, even the occurrences that pass both filters are ranked
  differently. In S–M at k = 10, 63% of the context-free top-10 come from the query's
  machine, and the overlap with the same-machine list is 0.62.
- **Per channel:**
  - Bosch S–MO@10: 0.51 (x), 0.41 (y), 0.48 (z);
  - PHM P–C@10: 0.43 (AE-RMS) to 0.70 (force y and z).
  No channel reaches "little change".
- **Per cutter** (PHM, k = 10): 0.45 (c1) to 0.60 (c2).

## What this does and does not show

- **Shows:** on two public machining datasets, the nearest past cases that
  `morlet_history_neighbors` returns depend strongly on `match_keys`. Typically 3–5 of
  the top-10 differ between two reasonable context choices, and more of the top-50.
  The context is part of the answer, so a result without its context cannot be read.
- **Does not show:**
  - which context is better. C2 covers detection on Bosch; C4 found neighbour labels a
    poor classifier;
  - behaviour for other value sets, block-level segments or `before_only` histories;
  - other sites or sensors.
- **PHM is an archive search** that includes later cuts of the same cutter. A causal
  ("earlier cuts only") search could overlap differently.
- **Development run:** the harness was run once on the labelled development subset
  (Bosch M01 OP00/OP01; PHM c1–c2 cuts 1–10), in separate `_dev` collections, only to
  check that it ran. Its numbers are not results and nothing was changed after it.
