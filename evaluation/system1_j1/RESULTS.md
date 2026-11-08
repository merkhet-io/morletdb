# J1 result: a general fast decision model decides from MorletDB's decision state

**Decision under the frozen rule (amendment 1): SUPPORTED.**

Jev (`jev-1.14.0`, zero-shot, no examples) read each milling run's decision state, 48 deviations from the
same-setting history, and answered whether the insert should be replaced (flank wear VB ≥ 0.3 mm):

| Score | AUROC, VB ≥ 0.3 mm | Spearman ρ with VB |
|---|---:|---:|
| Conditioned score (C1, built for the task) | 0.966 | 0.891 |
| **Jev, probability "replace insert"** | **0.948** | 0.842 |
| Jev, wear level (sharp / moderate / worn) | | 0.814 |
| Time in use (control) | 0.909 | 0.794 |

- **Δ AUROC, Jev − conditioned score:** −0.018 [95% CI −0.081, +0.041]. The rule (AUROC ≥ 0.90 and Δ ≥ −0.05) is
  met. The interval is wide: 64 runs cannot show that Jev equals the purpose-built score, only that it comes close.
- **Δ AUROC, Jev − time in use:** +0.039 [−0.014, +0.103], not a clear gain over the control.
- **Protocol:** [PROTOCOL.md](PROTOCOL.md), frozen at `7969500` before any study call; amendment 1 (`jev-1.14.0`
  pinned after the connectivity calls). Run once on 2026-11-08 from the owner's Mac: 192 calls, 3 per run, in a
  seeded random order. Raw responses: `results/calls.jsonl`; analysis: `results/result.json` (`analyze.py`).

## Behaviour of the model

- **Errors:** 0 HTTP errors, 0 schema errors in 192 calls.
- **Repeatability:** the three calls per run differed by 0.012 on average (probability scale).
- **Calibration: poor in absolute terms.** Every probability fell between 0.3 and 0.8; none above 0.8, though half the
  runs were worn. 0.4–0.6: 22 runs, 9% worn; 0.6–0.8: 38 runs, 82% worn. So the ranking is good, but 0.5 is the wrong
  cut-off: a decision threshold has to be set on known cases (here about 0.6). Brier score 0.196 against 0.250 for
  the base rate.
- **Latency per call:** 245 ms median, 532 ms p95, from the Mac over the internet, with about 3,800 input tokens per
  call. Building the state from six channels took about 0.4 s more (64 ms median per channel, `states.py`).
- **Cost:** 729,768 input tokens, $0.031 at the listed price for the whole study (about $0.00016 per decision).

## What this says, and what not

- A general typed-decision model, given only MorletDB's same-context deviations and no examples, ranked worn
  inserts nearly as well as the score built for the task, and better than time in use (not significantly). The
  state carries the information; the model does not need waveforms.
- It does not beat the purpose-built score, and its probabilities need a threshold set on known cases before they
  can drive a decision.
- One machine and one task, 64 runs, a single test with a public baseline. The model is a cloud service in early
  access; the decision state is model-neutral, so an on-premises model can be tested the same way.
- Per-part decisions (state about 0.4 s plus a call about 0.25 s) fit; decisions within a cycle do not.

Data: NASA PCoE "3. Milling" (Agogino & Goebel); cite Goebel (1996) and the NASA Prognostics Data Repository.
