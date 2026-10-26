# Pilot 2 results (PROTOCOL_PILOT2.md, commit 73d852e, clean tree)

- 4 cross-fit retriever encoders and 33 runs (11 arms × 3 seeds), all at `73d852e` with a
  clean tree and no failures.
- Retriever: out-of-fold training macro AUROC 0.850, assigned validation 0.840.
  Untrained kNN-label validation 0.804 (`encoders.json`, `retrieve.json`).
- Gate (validation only): L3n − A0 = +0.0007, so phase 2 ran. The margin is far inside
  the seed spread.
- The test fold (2,158 records, 1,877 patients) was read once. Intervals are paired
  patient-level bootstrap 95% intervals.

## Pre-registered decisions

| Comparison | Δ test macro AUROC | 95% CI | Rule |
|---|---|---|---|
| L3 vs A0 | −0.0013 | [−0.0043, +0.0016] | H1 |
| L3 vs P | −0.0019 | [−0.0047, +0.0009] | H1-strict |
| L3n vs A0 | −0.0019 | [−0.0044, +0.0006] | H2 precondition |
| C64n vs R64n | +0.0089 | [+0.0052, +0.0123] | H2 primary |
| C64n vs W64n | +0.0057 | [+0.0023, +0.0089] | H2 primary |

- **H1: not supported**, now with a learned retriever and attention fusion as well.
- **H2: inconclusive** (the precondition fails), as in pilot 1.

## Other reported comparisons

| Comparison | Δ | 95% CI |
|---|---|---|
| L1 vs A0 | +0.0027 | [−0.0001, +0.0055] |
| L1 vs P | +0.0020 | [−0.0004, +0.0045] |
| L3 vs L1 | −0.0040 | [−0.0063, −0.0017] |
| P vs A0 | +0.0006 | [−0.0014, +0.0029] |
| C64 vs R64 | +0.0086 | [+0.0057, +0.0116] |
| C64 vs W64 | +0.0072 | [+0.0046, +0.0101] |
| C64n vs A0 | −0.0008 | [−0.0032, +0.0015] |
| kNN vs A0 | −0.0354 | [−0.0435, −0.0271] |

Test macro AUROC / macro AUPRC per arm:

| Arm | Macro AUROC | Macro AUPRC |
|---|---|---|
| A0 | 0.8357 | 0.6505 |
| P | 0.8363 | 0.6524 |
| L1 | 0.8383 | 0.6566 |
| L3 | 0.8344 | 0.6504 |
| L3n | 0.8338 | 0.6471 |
| R64 | 0.8278 | 0.6363 |
| R64n | 0.8260 | 0.6239 |
| W64 | 0.8291 | 0.6400 |
| W64n | 0.8292 | 0.6349 |
| C64 | 0.8363 | 0.6547 |
| C64n | 0.8349 | 0.6484 |
| kNN | 0.8003 | 0.5836 |

Per-class AUROC is in `evaluation.json`.

## Reading

- **Retrieved signals hurt.** Adding neighbour signals to neighbour labels lowers quality:
  L3 is below L1, with the interval below 0. The best arm uses neighbour labels only
  (L1), and even it is not distinguishable from no retrieval or from the ensemble
  control.
- **Pilot 1's representation ordering replicates under a different retriever and fusion.**
  C beats R and W at 64 scalars, with and without labels. C is the only budgeted
  representation that is indistinguishable from A0; R and W sit below it. This is again
  consistent with reconstruction fidelity: less damage, not added benefit.
- Across two pilots, this task gives no setting in which retrieved ECG context helps a
  small supervised classifier trained on 17k labelled records. That is plausible: with
  enough training labels, the classifier already captures what neighbours would add.

## Limits

These results cover one dataset with lead II only, a 36–49k-parameter CNN, k = 8, and a
retriever trained on the task labels. Fold 9 is used for early stopping and the gate. No
hyperparameters were tuned.
