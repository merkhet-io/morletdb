# Pilot results (protocol amendment 1, commit 590445f, clean tree)

All 69 runs (23 arms x 3 seeds) trained at commit `590445f` with a clean tree: phase 1
(21 runs, `phase1.log`), then the validation gate (`gate.json`), then phase 2 (48 runs,
`phase2.log`, 0 failures). The test fold (fold 10: 2,158 records, 1,877 patients) was
read once, by `evaluate` (`evaluation.json`). Intervals are paired patient-level bootstrap
95% intervals (1000 resamples) on seed-averaged predictions.

## Gate (validation fold 9 only)

A3n − A0 = +0.0023 in mean validation macro AUROC, so the pre-registered gate passed and
phase 2 ran. The margin was smaller than the seed spread, as noted before phase 2.

## Pre-registered decisions

| Comparison | Δ test macro AUROC | 95% CI | Role |
|---|---|---|---|
| C64n vs R64n | +0.0087 | [+0.0063, +0.0115] | primary H2 |
| C64n vs W64n | +0.0067 | [+0.0042, +0.0093] | H2 |
| A3n vs A0 | +0.0015 | [−0.0010, +0.0041] | H2 precondition |
| A3 vs A0 | +0.0013 | [−0.0011, +0.0040] | H1 |
| C64 vs R64 | +0.0097 | [+0.0067, +0.0126] | secondary |
| C64 vs W64 | +0.0080 | [+0.0058, +0.0104] | secondary |
| A3 vs A2 | +0.0016 | [−0.0010, +0.0044] | selection |
| A3 vs A1 | +0.0000 | [−0.0020, +0.0023] | signal content |

- **H1: not supported.** Retrieved neighbours (raw signals plus labels) do not beat no
  retrieval: the interval includes 0. Neither do labels alone (A1 vs A0 +0.0013,
  [−0.0005, +0.0034]) nor retrieved vs random neighbours (A3 vs A2).
- **H2: inconclusive about representation, under the pre-registered rule.** C-64n beats
  both comparators with intervals above 0, but the precondition fails: A3n does not beat
  A0, so retrieved signals carry no demonstrable usable information in this setup.

## What the C > R, W ordering does and does not mean

Every budgeted context arm is at or **below** the no-retrieval baseline (A0 = 0.8371):

| Budget | R-Bn | W-Bn | C-Bn | R-B | W-B | C-B |
|---|---|---|---|---|---|---|
| 32 | 0.8288 | 0.8289 | 0.8355 | 0.8293 | 0.8293 | 0.8341 |
| 64 | 0.8260 | 0.8280 | 0.8348 | 0.8265 | 0.8283 | 0.8362 |
| 128 | 0.8318 | 0.8309 | 0.8368 | 0.8300 | 0.8307 | 0.8359 |

Exploratory comparisons (not pre-registered; `supplementary.json`): C64n vs A0 −0.0024
[−0.0045, −0.0000], C128n vs A0 −0.0003 [−0.0026, +0.0019], R64n vs A0 −0.0111
[−0.0140, −0.0081], W64n vs A0 −0.0091 [−0.0122, −0.0063].

The coarse+refine advantage is therefore **less damage, not added benefit**: lossy
neighbour reconstructions degrade the shared encoder, and the arms degrade it in the order
of their reconstruction error (`../pilot-0b26f8e/contexts.json`: C ≪ R ≈ W). C also
transmits indices, so C-64 is 640 bytes against 512 for R-64 and W-64. The test-fold
ordering matches the validation ordering seen at the gate.

## Secondary metrics

Per-class AUROC and macro AUPRC are protocol secondary metrics that `evaluate()` did not
compute. They were computed afterwards from the same saved test predictions by
`experiments/ptbxl_h2_supplementary.py` (`supplementary.json`). They agree with the
macro AUROC picture: macro AUPRC is A0 0.6485, A1 0.6533, A3n 0.6529, C64n 0.6458,
W64n 0.6302 and R64n 0.6256. Against A0, R-64n loses most in CD (−0.021) and W-64n in
STTC (−0.015) and CD (−0.013).

## Seeds

The seed-averaged ensemble AUROC is at or above the best single seed in most arms (A0:
0.8371 vs best seed 0.8372). Single-seed spreads (min–max, `seed_macro_auroc`) are about
0.002–0.009 per arm, the same size as most of the pre-registered differences.

## Validity limits

These results cover one dataset, lead II only, a 40k-parameter CNN, one fixed spectral
retriever with k = 8, and mean-pooled neighbour embeddings. A null H1 here may reflect
the retriever or the fusion (mean pooling) rather than retrieval in general. No
hyperparameters were tuned.
