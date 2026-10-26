# C2 result: same-operation history on Bosch CNC anomalies

**Decision under the frozen rules: SUPPORTED.** On real production data, scoring a
run against earlier normal runs of the *same machine and operation* separates
anomalous from normal runs better than scoring against all earlier runs:

| Score (relative deviation, max over 10 values) | AUROC, bad vs good |
|---|---|
| **Same machine + operation history** | **0.81** |
| Same operation, any machine | 0.75 |
| All history (context-free) | 0.64 |
| **ΔAUROC (machine+operation − context-free)** | **+0.17 [95% CI +0.07, +0.28]** |

The rule (ΔAUROC ≥ 0.05 and CI lower bound > 0) is met. Together with C1 (lab
milling, gradual wear), the context-conditioned comparison is now supported on
two independent datasets and two kinds of fault.

- **Protocol:** [../PROTOCOL.md](../PROTOCOL.md), frozen at `318cca6` before
  download.
- **Harness:** `77d0eb8`. The evaluation ran once.
  - The first execution computed the results but failed to write them, because
    of a NumPy scalar in the JSON. Only serialization was fixed (`c2.py`), and the
    identical deterministic computation was run again. No method change.
- **Data:** `boschresearch/CNC_Machining` at `d60581d`; per-file SHA-256 in
  `SHA256SUMS`.
  - 1,702 files loaded and none rejected: all are (n, 3) with finite values.
  - Cite Tnani, Feil & Diepold, Procedia CIRP 107 (2022). Data CC BY 4.0.
- **Split:**
  - history: 1,180 normal files from Feb 2019 to Aug 2020;
  - evaluation: 469 files from 2021, 17 of them anomalous;
  - every evaluation file had at least 9 same-context history files, so none was
    excluded.

## Read with care (reported, not decided on)

- **Duration alone does better than the combined score:** AUROC 0.91 for
  `duration_s`, against 0.81 for the maximum over all 10 values.
  - Many anomalous runs take a different time for the same operation.
  - The maximum over 10 values dilutes that signal.
  - Duration should be a first-class value, which `docs/context-model.md` already
    suggests. Combining values by maximum is not an optimal detector.
- **Not yet an alarm.** At a threshold set on history alone (99th percentile of
  leave-one-out history scores in their own context):
  - the conditioned score flags 2 of 17 anomalies, with 13 false alarms among 452
    normal runs;
  - the context-free score flags 0 of 17, also with 13 false alarms.

  The ranking is informative, but an untuned, history-only threshold is far from a
  usable alarm. Thresholds need labelled history or per-context calibration.
- **Per machine:**
  - M01: 0.96 conditioned vs 0.72 context-free;
  - M02: 0.92 vs 0.74;
  - **M03: 0.39 vs 0.26.** These values do not catch M03's six anomalies. The
    reason is not investigated here.
- **Per period:** Feb 2021: 0.84 vs 0.68; Aug 2021: 0.75 vs 0.55. Both periods
  show the gain, despite about 1.5 years of drift since the history.
- **Per value, conditioned:**
  - duration 0.91;
  - std z 0.84, std y 0.82, kurtosis x 0.81, std x 0.79;
  - crest and the other kurtosis values 0.59–0.72.
- **Cross-machine transfer:** same-operation history from any machine (0.75)
  keeps most of the gain over context-free. History from other machines helps
  when a machine's own history is short.

## What this does and does not show

- **Shows:** on industrial data with drift, comparing within the same context
  ranks anomalous runs markedly better than comparing against everything.
- **Does not show:**
  - a deployable alarm;
  - performance where operations are not separate files: NC-block segmentation
    within a recording is untested;
  - why M03 fails;
  - other sites or sensors.
- **Scope:**
  - **17 anomalies in the evaluation set.** The CI is correspondingly wide, but
    clear of zero.
  - **The labels are the dataset authors'.** Their operational definition is not
    published in the README.

## Consequences for the product

1. Context-conditioned comparison is the right core mechanism: C1 and C2 agree.
2. Duration per context segment should be shown and scored on its own, not only
   folded into a maximum.
3. Alarms need calibration per context, using labels where they exist. A global,
   history-only threshold is not enough. This belongs in phase 2 next to
   multichannel acquisitions.

## Reproduce

```sh
git clone --depth 1 https://github.com/boschresearch/CNC_Machining.git   # commit d60581d
python evaluation/context/c2/c2.py CNC_Machining/data OUT
python -m unittest evaluation/context/c2/test_c2.py
```
