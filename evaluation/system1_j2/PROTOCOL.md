# J2: decisions on held-out inserts, a local model, and labelled examples (#328)

Status: **FROZEN** in the commit that adds this file, before any J2 analysis of J1's data and before any J2 call.
Changes after freezing are dated amendments at the end.

## Why

J1 ([results](../system1_j1/RESULTS.md)) showed that Jev ranks worn inserts from MorletDB's decision state nearly as
well as the score built for the task (AUROC 0.948 against 0.966). It left three questions a deployment has to answer:

1. **Threshold:** Jev's probabilities stayed between 0.3 and 0.8, so 0.5 is no decision rule. Does a threshold set on
   some inserts work on another insert?
2. **Local model:** many shop floors allow no cloud calls. Does a plain local model on the same state do as well?
3. **Examples:** does Jev rank better when the state holds a few labelled cuts?

## Data and design

J1's population and states, unchanged (`../system1_j1/results/states.json`: 64 runs of 5 inserts, cases 9–13, 33
with VB ≥ 0.3 mm), and J1's Jev answers (`calls.jsonl`) as the zero-shot arm. **Leave one insert out:** everything
fitted (a threshold, a model, the examples) comes from the other four inserts and is applied to the held-out one.
The small-history flag of decision state v2 is not tested: every J1 history has fewer than 10 cuts, so every state
would carry it.

## Arms

- **Jev zero-shot:** J1's mean probability per run (no new calls).
- **Jev with four examples** (`fewshot.py`): the state is `{"examples": [...], "current_cut": <J1 state>}`, with
  two worn and two not-worn cuts from the other four inserts, each with its measured flank wear as the outcome,
  chosen once per held-out insert (seeded, seed 330 + case); the question is J1's with one added sentence (in
  `fewshot.py`). 3 calls per run, seeded order, model `jev-1.14.0`, J1's endpoint and key handling. Input-token
  cap 5,000,000 (about $0.21); measured requests are about 33,700 characters.
- **Local logistic regression:** the 48 relative deviations of the state (6 channels × 8 values; missing = 0),
  standardised on the training inserts, L2-regularised, C = 1, scikit-learn 1.7.2.
- **Conditioned score** (C1) and **time in use**, as references.

## Analysis (frozen, `analyze.py`)

- **Transferred threshold:** for each held-out insert, the threshold with the highest balanced accuracy on the other
  four inserts (midpoints between values, ties to the lowest), applied to the held-out insert; pooled balanced
  accuracy, sensitivity and specificity, and per insert how many cuts before (+) or after (−) the first worn cut the
  first flag came.
- **AUROC** of every arm over the 64 runs; bootstrap over runs (2,000, seed 7) for the paired differences below.
- **Also reported:** balanced accuracy at 0.5 and Brier score for the probability arms (do examples fix the
  calibration?), few-shot latency, errors and tokens.

## Questions and rules

1. **Q1, the threshold transfers:** YES if Jev zero-shot reaches a pooled balanced accuracy ≥ 0.80 with transferred
   thresholds.
2. **Q2, a local model matches Jev:** YES if the logistic regression's AUROC ≥ Jev zero-shot's − 0.05.
3. **Q3, examples help:** YES if Jev with examples − Jev zero-shot ≥ +0.02 in AUROC and the 95% CI's lower bound > 0.

Each answer is published whichever way it goes.

## Expectation (written before any J2 analysis or call)

- Q1: likely YES; J1's calibration table suggests a cut near 0.6 separates well, but five inserts are few, and one
  of them (case 11) rose early.
- Q2: uncertain. 48 features from 51 training runs can overfit; regularisation may hold it close to Jev.
- Q3: uncertain. Four examples are little, and the long state may dilute rather than help; examples might improve
  calibration more than ranking.

## Not covered

Other machines or tasks; more examples or chosen examples (nearest neighbours); fine-tuning; a local LLM.
