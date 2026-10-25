# Protocol, pilot 2: does retrieval help with a learned retriever and attention fusion?

Status: **frozen before any pilot-2 run** (frozen in the commit that adds this file; later
changes go in the changelog with their reason). Pilot 1 (`PROTOCOL.md`,
`results/pilot-590445f/`) found no retrieval benefit (H1 not supported). Its H2 result was
therefore inconclusive, and every budgeted context arm scored at or below no retrieval.
Pilot 2 asks whether that null comes from the **retriever** (an unfitted spectral
similarity) and the **fusion** (mean pooling), before representation is tested again.

Everything not stated here is unchanged from `PROTOCOL.md`:
- data: PTB-XL 1.0.3, lead II at 100 Hz, 5 superclasses, folds 1-8 / 9 / 10;
- the derived signals and budgeted contexts, reused from pilot 1 by sha256 (`link.json`);
- gallery = folds 1-8, with no self or same-patient neighbours, k = 8;
- the encoder CNN, optimizer, batch size, epoch limit, patience and 3 seeds;
- the metric and the patient-level paired bootstrap (1000 resamples, seed-averaged predictions).

## Retriever (fixed before any arm is trained)

- **Cross-fit encoders.** 4 encoders with the A0 architecture, one per part of the
  training folds (part j = folds {2j+1, 2j+2}). Encoder j trains on the other three parts,
  with early stopping on fold 9 (seed 1000 + j). Validation and test records are assigned
  to part (patient_id mod 4).
- **Retrieval.** Each query is embedded by the encoder of its part, which has never seen
  it. It retrieves the exact top-8 by cosine similarity of 64-d pooled embeddings against
  the whole gallery, embedded by that same encoder. Training and held-out queries are
  therefore retrieved under the same condition.
- **Leakage.** No encoder sees fold 10 labels, and fold 9 is used only for early stopping
  (as in pilot 1). Encoders embed the test inputs, but never read their labels.

## Fusion

Single-head attention: the query embedding attends over the 8 neighbour tokens. Each token
holds what its arm delivers: the neighbour's signal embedding (shared encoder), its label
vector, and its retrieval similarity. All retrieval arms keep the similarity token, so
they differ only in the signal and label parts.

## Arms (3 seeds each)

| Arm | Neighbour tokens | Extra input |
|---|---|---|
| A0 | none | — |
| **P** (ensemble control) | none | the query's own out-of-fold retriever-encoder probabilities |
| L1 | labels + similarity | — |
| L3 | raw-signal embedding + labels + similarity | — |
| L3n | raw-signal embedding + similarity | — |
| R64(n), W64(n), C64(n) | budget-64 context embedding (+ labels unless n) + similarity | — |
| kNN (no training) | mean label of the 8 neighbours | — |

**Why P:** a learned retriever brings in a second trained model. Neighbour labels from it
can help simply as an ensemble or stacking signal, without any value in the retrieved
*examples*. P gives the model that second model's own prediction directly. Retrieval earns
credit beyond ensembling only if L3 beats P.

## Phases

- Phase 1: A0, P, L1, L3, L3n.
- Gate (**validation fold 9 only**): phase 2 runs only if the mean validation macro AUROC
  of L3n exceeds that of A0.
- Phase 2: R64n, W64n, C64n, R64, W64, C64.
- The test fold is read once, by `evaluate`, after all runs that will be run.

## Decision rules (95% patient-bootstrap intervals on test macro AUROC)

- **H1 (retrieval helps):** L3 − A0 interval above 0.
- **H1-strict (helps beyond ensembling):** L3 − P interval above 0. H1 is reported as
  "explained by ensembling" if H1 holds and H1-strict does not.
- **H2 precondition:** L3n − A0 interval above 0 (retrieved signals carry usable information).
- **H2 (primary, when phase 2 runs):** C64n − R64n and C64n − W64n intervals both above 0,
  and the precondition holds. Otherwise H2 is inconclusive (precondition fails) or not
  supported.
- Also reported, all of them: L1 vs A0, L1 vs P, L3 vs L1, P vs A0, C64 vs R64,
  C64 vs W64, C64n vs A0, and kNN vs A0. The per-class AUROC and macro AUPRC are
  computed in `evaluate` this time.

## Budget and limits

About 33 runs plus 4 encoders, local MPS, no paid resources, no tuning. This pilot still
uses one dataset, one lead and a small CNN. The retriever is trained on the same labels
as the task, so it cannot speak to label-free retrieval. Fold 9 is used for early
stopping by the encoders, the models and the gate.

## Changelog

- 2026-10-25: initial pilot-2 protocol.
