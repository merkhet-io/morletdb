# J3: can an open-weights decision model (Laya) decide from MorletDB's decision state on the machine? (#328)

Status: **FROZEN** in the commit that adds this file, before Laya is installed and before any call with study data.
Changes after freezing are dated amendments at the end.

## Question

J1 showed that Jev, a hosted general decision model, recognises a worn insert from MorletDB's decision state
(AUROC 0.948); J2 that a local logistic regression on the same state does as well (0.969). Laya (Convai
Innovations, Apache-2.0, weights published) is an open-weights model of the same kind: typed questions in,
probabilities out, one forward pass, no generated text. It reads at most 512 tokens (`laya`) or 1,024
(`laya-typed-decisions`); J1's states were about 3,800. So: **from a compact form of the same state, does Laya,
running on the machine's own CPU with no network, decide about as well as Jev does from that compact state?**
And what does compacting cost Jev?

## Data and population

J1's 64 runs (NASA PCoE "3. Milling", cases 9–16, 33 worn with VB ≥ 0.3 mm) and J1's committed states
(`system1_j1/results/states.json`); nothing is re-ingested. Same labels and the same not-sent fields (case, run,
time in use, VB).

## Compact state (`compact.py`)

Per run, as text: material, depth of cut and feed; the number of earlier sharp cuts; one sentence defining deviation
and rank; then per channel its sensor description, the summary deviation and its rank, and the **K values with the
largest |relative deviation|** (percent, with rank; ties in J1's value order). K is the largest of 3, 2, 1 for which
every state fits both Laya checkpoints' room for the state beside each question (`decide.py fit`, from the tokenizer
and `laya.common.state_room` only; no answers are produced). The same compact states go to all three arms.

## Arms (`decide.py`)

| Arm | Model | Where |
|---|---|---|
| `laya-td` (**primary**) | `convaiinnovations/laya-typed-decisions` @ `1a793eb568e6` | the Mac's CPU, laya 0.4.30 (`2e50c4e`) |
| `laya` | `convaiinnovations/laya` @ `55cf4c4ebb4e` | the Mac's CPU, laya 0.4.30 |
| `jev` | `jev-1.14.0` (J1's pin), J1's endpoint | TypeSafe's API |

Revisions are laya 0.4.30's reviewed pins (`laya/revisions.py`), not the moving head. Questions: J1's two
(`replace_insert` noul, `wear_level` score), with the definition sentence adapted to the compact wording (`QUESTIONS`).
One call per run per arm (Laya is deterministic; Jev's repeat spread is known from J1), in J1's seeded order.

## Control before the study (`decide.py check`)

Laya's yes/no answer has a reported failure mode (upstream issue #156: it can follow the option labels rather than
the state). Per arm, four fixed non-study statements with an obvious answer, with the shipped labels, neutral labels
(A/B) and swapped labels. **The control passes** when all eight shipped-label and neutral-label answers are right
(swapped: reported only). If an arm fails the control, its yes/no answer is not used; its probability is the wear
level's expected value / 2 instead (recorded in the result). The control runs once and is recorded.

## Analysis (frozen, `analyze.py`)

- Per arm: AUROC of the probability for VB ≥ 0.3 mm; the threshold transferred leave one insert out (J2's rule:
  best balanced accuracy on four inserts, applied to the fifth); balanced accuracy at 0.5; Brier; Brier and balanced
  accuracy at 0.5 after a recalibration fitted leave one insert out (Platt on the logit; it does not change AUROC);
  Spearman ρ of the wear level with VB; truncated states; schema errors; latency p50/p95 per call (Laya: CPU time
  of the call on the Mac, model loaded; Jev: including the network).
- References on the same runs: Jev on J1's full state (0.948), the conditioned score (0.966), the local logistic
  regression (0.969, recomputed as J2).
- Differences by bootstrap over runs (2,000 resamples, seed 7): laya-td − jev (compact), laya − jev (compact),
  jev (compact) − jev (full state), laya-td − local logistic regression.
- **Decision:** SUPPORTED if `laya-td`'s AUROC ≥ 0.90 and laya-td − jev (compact) ≥ −0.05; otherwise NOT
  SUPPORTED. Both outcomes are published.

## Expectation (written before any call)

The vendor reports weak zero-shot accuracy for the base checkpoints and recommends recalibration, and Laya's
ordinal score is its weakest primitive; independent benchmarks put Jev ahead on most tasks. We expect Laya below
Jev and are uncertain whether it reaches 0.90. Recalibration should repair the Brier score but cannot change the
ranking. We expect Jev to lose little from the compact state (most of J1's 48 values deviate together), and
Laya's latency to be a few hundred ms per call on the CPU, as the vendor reports. A NOT SUPPORTED result would say
that a zero-shot open-weights model of this size is not yet enough here; J2's local regression remains the
no-cloud answer.

## Not covered

Fine-tuning Laya (the vendor's recommended use) or few-shot examples; the multilingual checkpoint; the GPU; the
Raspberry Pi (the checkpoints need about 0.9 GB of memory each); other machines or tasks.
