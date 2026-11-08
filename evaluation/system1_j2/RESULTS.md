# J2 result: thresholds transfer, a local model keeps up, examples help Jev

**All three frozen questions: YES.** Protocol [PROTOCOL.md](PROTOCOL.md), frozen at `003dbf8` before any J2 analysis
and before any J2 call; analysed once with `analyze.py` on 2026-11-08 (`results/result.json`). Leave one insert out
over J1's 64 runs of 5 inserts (33 worn, VB ≥ 0.3 mm).

| Arm | AUROC | Held-out decisions with a transferred threshold: balanced accuracy (sensitivity / specificity) | At 0.5: balanced accuracy | Brier |
|---|---:|---|---:|---:|
| Jev zero-shot (J1's answers) | 0.948 | 0.826 (0.88 / 0.77) | 0.677 | 0.196 |
| **Jev with four labelled examples** | **0.977** | 0.859 (0.88 / 0.84) | **0.855** | 0.121 |
| **Local logistic regression** (48 deviations, no cloud) | 0.969 | **0.904** (0.97 / 0.84) | **0.890** | **0.069** |
| Conditioned score (C1) | 0.966 | 0.859 (0.88 / 0.84) | | |
| Time in use | 0.909 | 0.795 (0.85 / 0.74) | | |

1. **Q1, the threshold transfers: YES.** Thresholds set on four inserts and applied to the fifth gave Jev zero-shot a
   balanced accuracy of 0.826 (rule ≥ 0.80). The chosen thresholds hardly moved between folds (0.595–0.627), so
   J1's "about 0.6" was not luck on the same data.
2. **Q2, a local model matches Jev: YES.** A logistic regression on the same state reached AUROC 0.969, +0.021 over
   Jev zero-shot [95% CI −0.011, +0.058] (rule ≥ −0.05), and the best held-out decisions of all arms, with by far
   the best calibration. No cloud is needed for this task.
3. **Q3, examples help: YES, narrowly.** Four labelled cuts from other inserts raised Jev's AUROC from 0.948 to 0.977,
   +0.028 [+0.001, +0.066] (rule ≥ +0.02 and lower bound > 0; the lower bound clears 0 by 0.001). They mostly fixed
   the calibration: at a plain 0.5, balanced accuracy rose from 0.677 to 0.855, Brier from 0.196 to 0.121.

## When the first alarm came (cuts before (+) or after (−) the first worn cut, per held-out insert 9 / 10 / 11 / 12 / 13)

| Arm | 9 | 10 | 11 | 12 | 13 |
|---|---:|---:|---:|---:|---:|
| Jev zero-shot | −3 | 0 | +6 | +1 | +2 |
| Jev with examples | −2 | −2 | +4 | +1 | 0 |
| Local logistic regression | 0 | 0 | +2 | +3 | 0 |
| Conditioned score | +5 | −1 | −2 | +5 | −1 |
| Time in use | 0 | −1 | +5 | +3 | −4 |

Insert 11, the longest insert life here (23 cuts), drew early alarms from most arms. A late alarm (negative) means cutting
on with a worn insert; an early one, a replaced insert with life left.

## Behaviour and cost

- Few-shot calls: 192, 0 HTTP and 0 schema errors, latency 320 ms median (requests of about 19,000 tokens, five
  times J1's), 3,316,245 input tokens, about $0.14 at the listed price.
- The local model fits in milliseconds and needs no network.

## What this says, and what not

- MorletDB's decision state carries what this task needs: a plain local model on it matched the purpose-built
  score and a general cloud model alike, and decided best on held-out inserts. For shop floors without cloud
  access, that is the practical answer.
- Jev zero-shot ranks well but needs a threshold; with four examples it ranks and calibrates better.
- Five inserts, 64 runs, one machine and one task. The intervals are wide; Q3 passed by a hair. The
  logistic regression's lead over Jev is not significant.

Data: NASA PCoE "3. Milling" (Agogino & Goebel), NASA Prognostics Data Repository.
