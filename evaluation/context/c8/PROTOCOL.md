# C8: how much does the choice of `match_keys` change the nearest past cases?

Status: **FROZEN** on 2026-11-16 in the commit that adds this file, before any neighbour
of this study was computed and before the harness was written. Read so far: the Bosch
CNC README and file listing, the per-file array shapes and dtypes of the Bosch files
(no values), the PHM 2010 directory layout and file sizes, and C2's committed history
counts per (machine, operation). Changes after the freeze are logged, dated, under
"Changes after freeze".

This is a **descriptive measurement** (issue #419, part 2). It tests no detector and
makes no claim about which context is right. It measures how much the neighbour list a
user sees depends on the context the user chose.

## Why

`morlet_history_neighbors(collection, segment_id, match_keys, ...)` returns the nearest
other occurrences of a segment among those whose context matches the query on
`match_keys`. Distances are scaled by the history's own medians, so the context choice
changes both which occurrences qualify and how they are ranked. C2 found that the
context decides which past runs are useful for scoring. If the neighbour lists under
different, reasonable context choices overlap little, the context is part of the answer
and every result must state it. If they overlap almost fully, the choice is a detail.

## Function under test

pg_morlet's public `morlet_history_neighbors` (`api/stable.txt`), run in the released
image `ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1` (SQL 0.3.11; the only later SQL change
on main, 0.3.12, concerns envelope builds). Every argument except `match_keys` is fixed
per dataset:

- `metadata_filter`: Bosch `{"split": "history"}`, PHM `{}` (see below);
- `exclude_labels`: `{}` (no labels are stored);
- `top_k`: 51 (k = 50 plus one row to detect ties at the boundary);
- `max_candidates`: 10000 (above every candidate set here);
- `before_only`: false (see "History" per dataset);
- `score_values`: the default (`duration_s`, `std`, `crest`, `kurtosis`);
- the value rule is the collection default of this release; the harness records
  `morlet_value_rules` and reports it.

Each recording carries its context as intervals covering the whole recording, so there
is exactly one segment per recording (one run of one channel). History is always
restricted by the product to the same channel, rate and unit and excludes the query's
own occurrence.

## Data and context choices

### Bosch CNC Machining (as C2)

- `boschresearch/CNC_Machining` at `d60581d`; every file's SHA-256 is checked against
  `data/bosch/SHA256SUMS` before use; a mismatch stops the run. CC BY 4.0, Tnani, Feil
  & Diepold (2022).
- Three channels per run: acceleration X, Y, Z (2 kHz, unit `counts` as stored).
  Values are stored as float64 (int64 and float32 source arrays convert exactly; the
  harness asserts it).
- **History:** C2's history, the *good* runs of Feb 2019 – Aug 2020 (1,180 runs),
  metadata `split = history`.
- **Queries:** C2's evaluation set, **every** run of Feb 2021 and Aug 2021, good and bad
  (469 runs), metadata `split = query`. All queries are used (a census of the set, no
  sampling). The labels are not used.
- A run is identified by its file stem **and** label folder (file numbers restart per
  label folder).
- Context keys on every segment: `site` (one constant value), `machine`, `operation`;
  occurrence key `run`. Segments are built with keys `site, machine, operation`.
- **Context choices** (`match_keys`):
  - **S** `site`: no context, all history runs of the channel;
  - **M** `site, machine`;
  - **O** `site, operation`;
  - **MO** `site, machine, operation` (the context C2 found best).
  The dataset has no tool key; the operation is the nearest available equivalent.
- **Pairs:** all six (S–M, S–O, S–MO, M–O, M–MO, O–MO). The chain S → M → MO and the
  pair S–MO are the ones the write-up leads with; every pair gets its own descriptive
  verdict below.
- Known before the run (C2's counts): MO history has 9–42 runs per context, so **MO
  never fills a top-50** and some MO lists have fewer than 10 entries. This is counted
  and reported, not avoided.

### PHM 2010 milling (cutters c1–c6)

- PHM Society 2010 Data Challenge, the six cutters at `data/phm2010` (315 cut files
  each, 1,890 in all). No checksums are published; the harness records the SHA-256 of
  every file it reads.
- Seven channels per cut, 50 kHz: force X, Y, Z (N), vibration X, Y, Z (g), AE-RMS (V),
  parsed from the CSV with round-trip float precision. A file without 7 columns or with
  a non-finite value is rejected and reported.
- **History and queries:** every cut of every cutter is stored and **every** cut is a
  query (a census of 1,890 cuts). There is no common clock across cutters, so
  `before_only` cannot be used without inventing times. The measurement is therefore
  of an archive search: a cutter's history includes its later cuts. This is stated with
  every result.
- Context keys: `process` (one constant value), `cutter`; occurrence key `cut`.
- **Context choices:** **P** `process` (all cutters) and **C** `process, cutter`. All
  cuts ran under one set of cutting conditions, so there is no operation key; the
  cutter (one tool life) is the context the issue names. One pair: P–C.

## Measures (fixed now)

For one query segment (one run or cut, one channel), two context choices A and B, and
k ∈ {10, 50}: let `A_k` and `B_k` be the first k neighbours returned (fewer if the
history has fewer than k), identified by recording id.

- **Overlap@k** = |A_k ∩ B_k| / k. **Primary.** A list shorter than k counts its
  missing places as not shared: the user sees a shorter list.
- **Jaccard@k** = |A_k ∩ B_k| / |A_k ∪ B_k|.
- **Containment@k** = |A_k ∩ B_k| / min(|A_k|, |B_k|): whether the shorter list is
  contained in the longer one (reported, not decided on).
- **Outside share@k** (reported): for a pair where B's keys include A's (A wider), the
  share of A_k whose context differs from the query on B's extra keys, e.g. the share of
  the context-free top-10 that come from another machine. Computed from the stored
  metadata of each neighbour.

**Ties.** The product orders by distance, then recording id, then start sample, and that
deterministic order is what a user sees, so it is used as returned. The number of query
lists whose k-th and (k+1)-th distances are exactly equal is reported per dataset,
choice and k.

**Short lists.** Per dataset, choice and k: the number of query lists with fewer than k
neighbours and their length distribution (min, median). Every measure is reported on all
queries; Overlap@k is also reported on the queries where both lists are full, if any.

**Aggregation.** A query run (cut) is the unit: its value is the mean over its channels.
The dataset value is the mean over query runs (cuts). Also reported: median and the
10th/90th percentiles over runs, per channel means, and (PHM) per-cutter means.

## Uncertainty

- **Bosch:** cluster bootstrap over the (machine, operation) groups of the query runs
  (as C2): 2000 resamples, seed 20261116, percentile 95% interval of the mean.
- **PHM:** cuts of one cutter are strongly dependent and there are only six cutters, so
  no bootstrap interval is computed. The six per-cutter means are reported and the
  verdict uses their minimum and maximum.

## Verdicts (descriptive, per dataset, pair and k)

With `U` the upper and `L` the lower end (Bosch: the 95% interval of mean Overlap@k;
PHM: the maximum and minimum of the six per-cutter means):

- **most neighbours change:** U < 0.5;
- **neighbours change:** 0.5 ≤ U < 0.8 (on average at least one in five differs);
- **little change:** L ≥ 0.9;
- **intermediate:** anything else.

**"The context choice matters" for a dataset** if at least one pair of choices gets
"neighbours change" or "most neighbours change" at k = 10. **It does not matter** if
every pair gets "little change" at k = 10 and k = 50. Otherwise the result is mixed and
reported as such. No verdict is combined across datasets.

## Development and checks

- **Synthetic tests** (`test_c8.py`) of the overlap measures, short lists, ties and
  aggregation, before any data is loaded.
- **Development run:** the harness is run end to end once on a small labelled part
  (Bosch: one machine, two operations; PHM: cutters c1 and c2, cuts 1–10) in a separate
  collection, only to check that it runs. Its numbers are not reported as results and
  nothing is changed in response to them except bugs, which are logged below.
- **Product check:** for 5 Bosch and 5 PHM queries (the first query run/cut in id order
  per dataset, all channels, every choice), the harness recomputes the ranking in Python
  from the stored `simple-v1` values with the documented rule (RMS over score values of
  (x_q − x_h)/|median_h|, values with a zero or floored median skipped, ties by recording
  id) and reports agreement of the returned ids and order. A disagreement is reported,
  not fixed in the measurement.
- The full run is executed once. A failed run is repeated only for a harness or
  infrastructure fault, logged below.

## Not claimed

- No statement about which context is better for any task (C2 covers detection on
  Bosch); only how different the lists are.
- Only two datasets, one site each, `simple-v1`/default score values and whole-run
  segments. Other value sets, block-level segments or `before_only` histories may give
  other overlaps.
- PHM is an archive search including later cuts of the same cutter.

## Changes after freeze

(none)
