# J3 result: Laya does not decide here; the compact state is enough for Jev

**NOT SUPPORTED.** Protocol [PROTOCOL.md](PROTOCOL.md), frozen at `ac79b7e` before Laya was installed and before
any call; run once on 2026-11-09, 09:42–09:47, with `run_j3.sh` (`results/`). The compact state fitted with K = 3
deviations per channel: at most 399 tokens, against room for 410 (`laya`) and 922 (`laya-typed-decisions`). No
state was truncated and no call failed.

| Arm (compact state, about 400 tokens) | AUROC | Held-out decisions with a transferred threshold: balanced accuracy (sensitivity / specificity) | Brier (recalibrated) | Wear level ρ with VB | Latency p50 |
|---|---:|---|---:|---:|---:|
| `laya-typed-decisions`, Mac CPU (**primary**) | 0.593 | 0.599 (0.94 / 0.26) | 0.244 (0.254) | 0.50 | 771 ms |
| `laya`, Mac CPU | 0.302 | 0.464 (0.12 / 0.81) | 0.301 (0.252) | 0.10 | 790 ms |
| **Jev `jev-1.14.0`** | **0.966** | **0.860** (0.85 / 0.87) | 0.198 (**0.083**) | **0.87** | 198 ms |
| *References on the same runs:* Jev on J1's full state (~3,800 tokens) | 0.948 | 0.826 (0.88 / 0.77) | 0.196 (0.120) | | |
| Conditioned score (C1) | 0.966 | 0.859 | | | |
| Local logistic regression (J2) | 0.969 | 0.904 | 0.069 | | |

The Brier score of always predicting the base rate is 0.250. Differences in AUROC (bootstrap over runs, 95%):

- laya-typed-decisions − Jev, both on the compact state: **−0.373** [−0.517, −0.236]. Rule ≥ −0.05: not met.
- laya − Jev (compact): −0.664 [−0.789, −0.528].
- Jev compact − Jev full state: **+0.018** [−0.024, +0.066].

## What this answers

1. **Laya, zero-shot, does not recognise a worn insert from this state.** The typed-decisions checkpoint ranks
   little better than chance (0.593). Its yes/no probabilities stay between 0.42 and 0.66 for all 64 runs, and
   recalibration does not help (Brier 0.254 against 0.250 for the base rate). The English checkpoint ranks below
   chance (0.302) with every probability between 0.52 and 0.85. Both passed the control (10/12 and 11/12; their misses
   were with swapped labels, which the control only reports), so this is not upstream issue #156. The result is what
   the vendor's own description predicts for the base checkpoints: a base to fine-tune, not a zero-shot decision model.
2. **The compact state is enough.** From about 400 tokens, a tenth of J1's state, Jev decided at least as well as
   from the full state (0.966 against 0.948; the difference is not significant). Recalibrated, it reached a balanced
   accuracy of 0.89 at 0.5. MorletDB's decision state can therefore be cut to a model's input limit by keeping
   each channel's largest deviations from the asset's own baseline.
3. **Latency.** Laya took about 0.8 s per decision on the Mac's CPU (load average 2.8–3.9 during the run, other work
   on the machine); the vendor reports 193–464 ms on a CPU. Jev over the network took 0.2 s.

## What this does not say

Laya is not tested here as its vendor recommends using it: fine-tuned on labelled cases. With 64 runs and five
inserts, a fine-tuning study needs its own protocol (leave one insert out), and J2's local logistic regression
already reaches 0.969 with no cloud and in milliseconds, which sets a high bar for it. One task, one machine, 64
runs; the GPU, the multilingual checkpoint and the Raspberry Pi were not tested. On loading, both checkpoints
warned that they ship a temperature outside [0.5, 5] for choice questions, which this study does not use.

Data: NASA PCoE "3. Milling" (Agogino & Goebel), NASA Prognostics Data Repository. Laya: Convai Innovations,
Apache-2.0, laya 0.4.30 at its reviewed checkpoint revisions.
