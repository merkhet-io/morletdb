# J1: can a general fast decision model decide from MorletDB's decision state? (#328)

Status: **FROZEN** in the commit that adds this file, before any call to Jev with study data. Changes after freezing
are dated amendments at the end.

## Question

A "System 1" model decides per part from compact state, not from waveforms. MorletDB's decision state
(`tools/mcp/decision.py`) describes a segment by its deviation from the same-context history. Given that state
and nothing else, does TypeSafe's Jev, a general typed-decision model used zero-shot, recognise a worn tool about as
well as the score built for the task?

## Data and population

NASA PCoE "3. Milling" (Agogino & Goebel; `data/milling/extracted/mill.mat`, C1's archive), loaded through
pg_morlet exactly as C1's SQL check does (`states.py`): six channels per run, context machine/doc/feed/material,
occurrence run. Case 2 run 1 is rejected at ingest (samples beyond ±1e9). Population as C1: runs of cases 9–16 with
VB whose setting has at least 3 sharp history runs (VB ≤ 0.2 mm in cases 1–8): **64 runs, 33 with VB ≥ 0.3 mm**.
C1's result on the same runs is public (conditioned score AUROC 0.966 here); this study does not tune anything to
the outcome and has no training step.

## State sent to Jev

Per run (`states.py`, `run_state`): the machine, the setting (depth of cut, feed, material), and per channel the
sensor's description, the history size, the eight simple-v1 values with value, history median, relative deviation
and history quantile, and the summary score and quantile. **Not sent:** case, run number, time in use, VB, the
labels. Values rounded to 6 significant digits. The states are built once and committed (`results/states.json`).

## Questions (frozen, `jev.py`)

- `replace_insert` (noul): has flank wear reached 0.3 mm or more, so the insert should be replaced? Wording, with the
  definition of relative deviation and quantile, as in `jev.py`.
- `wear_level` (score): sharp (< 0.1 mm), moderate (0.1–0.3 mm), worn (≥ 0.3 mm).

## Calls

- One connectivity call with a fixed non-study state (`jev.py check`) is allowed before the study, to confirm
  endpoint, key and model id; it is recorded, not counted.
- Study: 3 calls per run (192), in an order shuffled with seed 328, sequentially from the owner's Mac. Endpoint
  `https://api.typesafe.ai/v1/systemone` unless the connectivity call shows another documented endpoint is needed
  (recorded as an amendment before the study). Model id as recorded by the connectivity call.
- Failed calls (HTTP error, timeout) are retried by rerunning `jev.py run`, which skips successful calls; responses
  that do not match the schema count as schema errors and are not retried.
- Input-token cap 2,000,000 (about $0.08 at the listed price); the run stops before exceeding it.

## Analysis (frozen, `analyze.py`)

- Jev's probability per run: the mean `noul` of its successful calls; its wear level: the mean `score`.
- **Primary:** AUROC of Jev's probability for VB ≥ 0.3 mm, against the conditioned score (C1's: max over channels
  of |relative deviation| of the C1 value, recomputed from the same state calls) on the same runs. Bootstrap over
  runs, 2,000 resamples, seed 7, for the difference.
- **Decision:** SUPPORTED if Jev's AUROC ≥ 0.90 and its difference to the conditioned score ≥ −0.05; otherwise NOT
  SUPPORTED. Both outcomes are published.
- **Reported:** the difference to time in use (control; C1: AUROC 0.909); Spearman ρ with VB of wear level,
  probability, conditioned score and time in use; Brier score of the probability against the base rate's;
  calibration in five bins; spread between repeated calls; latency per call (p50, p95, from the Mac, including the
  network); HTTP and schema errors; input tokens and cost at the listed price.

## Expectation (written before any call)

The conditioned score is built for this task and reaches 0.966. A zero-shot general model reading 48 deviations per
run may weigh channels differently; we expect Jev between time in use (0.909) and the conditioned score, and are
uncertain whether it reaches the 0.90 / −0.05 bar. Jev's latency (company figure 70–500 ms) plus about 0.4 s to
build the state from six channels fits per-part decisions, not decisions within a cycle.

## Not covered

Other machines or tasks; fine-tuning or few-shot examples; on-premises models (the state is model-neutral and can
be sent to one later); the decision log (#328, after J1).
