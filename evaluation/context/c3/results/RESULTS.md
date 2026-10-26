# C3 result: calibrated-v1 vs the C2 score on MillingVibes

**Decision under the frozen rules: WORSE.** `calibrated-v1` ranks bad milling
segments *less* well than the uncalibrated C2 score. The C2 score stays the
default; calibration needs rework.

| Score | AUROC, bad vs good (241 test segments, 92 bad) |
|---|---|
| **A: C2 score** (relative deviation, max over std, kurtosis and crest per axis, plus duration) | **0.884** |
| B: `calibrated-v1` as implemented (all 24 `simple-v1` values) | 0.845 |
| B′: `calibrated-v1` on the C2 value set | 0.853 |
| **Δ (B − A) [95% CI]** | **−0.039 [−0.061, −0.020]** |

- **The rule:** the CI upper bound is < 0, so "worse".
- **The CI is optimistic.** Segments of one pattern share a label and are not
  independent, and pattern IDs are not published. The direction is consistent
  across both calibrated arms.
- **Protocol:** [../PROTOCOL.md](../PROTOCOL.md), frozen at `038832f` before any
  vibration value was read.
- **Harness:** `9ad6844`. It ran once.
- **Data:** MillingVibes (Langer & Widra, Fraunhofer IIS/EAS and IWU, 2022;
  doi:10.24406/fordatis/236; CC BY 4.0). It was loaded from the published pickles
  through an allowlisted unpickler.
- **Split:**
  - history: 549 good train segments;
  - test: 241 segments.

## Why calibration hurt here

- **Calibration does not know which values are informative.** It divides each
  value's deviation by the value's normal spread, so low-spread values gain
  weight.
  - Crest factor and kurtosis have small spreads here: 0.07–0.16 relative,
    against 0.12–0.21 for the level values.
  - They are also weakly informative: AUROC alone 0.48–0.66, against 0.80–0.91
    for std, rms and peak.
- **The top value under calibration shows the effect.**
  - For normal segments it is mostly mean (53), crest (38) or kurtosis (17).
  - For bad segments it is mostly a level value: p75 (45), rms (21) or peak (18).
- **Calibration makes uninformative values noisier competitors.** That raises
  scores on normal segments.

In C2's M03 case the opposite held: a noisy value *drifted* and calibration would
have suppressed it. **A spread tells how noisy a value is, not whether it matters.**
Neither the uncalibrated maximum nor calibration is safe in general.

## Reported values

- **Single values (relative deviation AUROC):**
  - std: x 0.85, y 0.91, z 0.80;
  - rms: the same as std;
  - peak: 0.76–0.86;
  - p75: 0.70–0.86;
  - kurtosis: 0.60–0.66;
  - crest: 0.48–0.65;
  - mean: 0.54–0.63;
  - duration: 0.50, since it is constant at 1 s.
- **The per-context quantile** of `morlet_compare_summary` is not tested here.
  With one context it is a monotone transform of the score.

## Consequences

1. **`morlet_compare_summary` (PR #19) must not default to `calibrated-v1`.**
   - Its default score becomes the evaluated C2 score: the maximum relative
     deviation over a level, spread and shape value set per channel, plus
     duration.
   - The calibrated score is reported next to it as experimental.
2. **Per-value deviations remain the primary output.** C2's M03 case and C3 fail
   in opposite directions under any single fixed combination. Engineers should see
   which value moved.
3. **Better combination needs labels.** A combination that weights values by how
   informative they are needs labelled examples per context; spread alone is not
   enough. That is labelled calibration, deferred until labelled history exists
   (`morlet_add_label`).

## Scope

- One machine (MAHO MH 800C), one material (aluminium), one context, surface
  quality labels.
- The test asks only how to combine values, not about context.

## Reproduce

```sh
# FORDATIS MillingVibes_Dataset.zip (sha256 4541c429...f2d7), extracted to DIR
python evaluation/context/c3/convert.py DIR millingvibes.npz   # allowlisted unpickler, no allow_pickle
python evaluation/context/c3/c3.py millingvibes.npz OUT
python -m unittest evaluation/context/c3/test_c3.py
```
