# C4 result: labelled earlier occurrences vs the well's last label (3W)

Protocol: [`../PROTOCOL.md`](../PROTOCOL.md), frozen in `b0d0ebd` before the download.
Changes after freeze are logged there.

**Result: NOT SUPPORTED.** The labels of the 5 nearest earlier occurrences name the
event worse than the well's most recent label:
- balanced accuracy 0.39 vs 0.61;
- Δ −0.21, 95% CI [−0.28, −0.05].

## Provenance

- **Data:** 3W Dataset 2.0.0, `3w_dataset_2.0.0.zip` from Figshare.
  - MD5 `2c23b87b60c5d19ed9cf9559efa6ffa7` matches the record.
  - SHA-256 `c037a6a9…fcbea`; file hashes in `SHA256SUMS`.
  - 1,119 real instances, matching the article's Table 6 per class. They come from
    **40** distinct wells; the article says 42.
- **Runs:**
  - **run 2 (decision):** commit `9741676`, clean tree, 84 s;
  - **run 1 (superseded):** commit `c84d784`.
  - Both used Python 3.12.13, numpy 2.2.6 and pyarrow 26.0.0, in an isolated
    interpreter (`-I`).
- **Product check (`sql_check.json`):** pass. For the earliest query of classes 0, 7
  and 8, `morlet_history_neighbors` (pg_morlet 0.3.0) returns the same neighbours in
  the same order as the harness. Its distances agree to ≤ 2.3e-16 relative.

## Population

- **Windows:** 8,111 one-hour windows, 6,941 of them labelled.
- **Unknown context:** in every instance, the first hour has no state label, giving
  1,119 windows with context `unknown`.
- **Queries:** 6,235 query windows from 1,044 instances in 38 wells.
- **Excluded:**
  - 1,170 windows without a label;
  - 26 windows with a mixed context;
  - 677 queries with fewer than 5 comparable earlier references.
- **Eligible classes:** 0, 1, 2, 3, 4, 5, 7, 8 and 9. Class 6 (quick restriction in
  PCK) had too few query instances.
- **Bootstrap:** 443 of 2,000 well resamples were dropped because they lacked an
  eligible class (mostly the rare classes 1 and 2).

## Primary decision

| | Balanced accuracy |
|---|---|
| **N:** 5 nearest earlier occurrences, same state | **0.394** |
| **B:** the well's most recent label | **0.607** |
| Δ (N − B) | **−0.213**, 95% CI [−0.284, −0.048] |

The CI upper bound is below +0.05, so by the frozen rule the result is **not supported**.

## Reported

| Arm | Balanced accuracy |
|---|---|
| prior (most frequent earlier label) | 0.181 |
| N, own well only (896 queries fell back to the prior) | 0.575 |
| N, context-free | 0.319 |

| Class | N | B |
|---|---|---|
| 0 normal operation | 0.75 | 0.88 |
| 1 abrupt increase of BSW | 0.00 | 0.00 |
| 2 spurious closure of DHSV | 0.11 | 0.33 |
| 3 severe slugging | 0.24 | 0.91 |
| 4 flow instability | 0.64 | 0.96 |
| 5 rapid productivity loss | 0.37 | 0.63 |
| 7 scaling in PCK | 0.62 | 0.86 |
| 8 hydrate in production line | **0.80** | 0.54 |
| 9 hydrate in service line | 0.00 | 0.36 |

- **Where the neighbours' signal comes from:**
  - 48% of N's neighbours come from the query's own well, and 32% from the own well
    within the preceding 30 days.
  - When N is right, 63% of its neighbours are from the own well; when it is wrong,
    20% are.
  - Neighbours from other wells rarely carry the right label. That is the Paderborn
    pattern again: similarity in these values mostly identifies the well and
    period, not the event.
- **What dominates distances:** `std` contributes 47% and `kurtosis` 36%; `p75` 16%
  and `crest` 1%.
- **Hydrate in the production line** is the one class where neighbours beat the
  well's history (0.80 vs 0.54). It is also the only class that occurs mostly
  outside the open state: 511 of its 782 query windows, mostly shut-in.
  - Of the shut-in query windows, 478 of 482 are hydrate.
  - So the win comes from the context (the state), not from the signal.
- **Context helps:** same state 0.39, context-free 0.32. This is consistent with C1
  and C2, but it does not rescue the comparison with B.

## Run 1 (superseded)

Run 1 summed values pairwise (numpy), not left to right as the product does. The
product check exposed the difference. For frozen channels, the rounding residue is
divided by a median near zero, so distances differed by up to 98% on one channel.
- Run 1 gave N 0.423 vs B 0.607, Δ −0.184 [−0.252, −0.036]: also not supported.
- The fix changed only the summation; no other part of the analysis moved.
  `summary-run1.json` keeps it.

## What this means

- **Neighbours are a browsing aid, not a diagnosis.**
  - On real labelled history, the labels of the nearest earlier occurrences named the
    event worse than simply remembering the well's last label.
  - They beat knowing nothing (0.39 vs 0.18), mostly through the own well's recent
    windows.
  - The viewer and docs should present neighbours as "similar earlier occurrences to
    look at", and should not suggest that their label applies.
- **A product weakness surfaced on the way.** A value whose history median is nearly
  0 makes relative differences explode. Constant (frozen) channels produce exactly
  this, through rounding residues in `std` and `kurtosis`. The product only skips
  medians that are exactly 0. A floor relative to the channel's level would be more
  robust, but it is not changed here.
- **Limits:**
  - process data at 1 Hz, not vibration;
  - one operator's offshore wells;
  - the summary values the product uses. A different representation (shape, trend)
    might do better. This test does not show that.
