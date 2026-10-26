# C3: does calibrated scoring rank bad milling segments better than the C2 score?

Status: **FROZEN** in the commit that adds this line. Read so far:
- the archive README and its example loader;
- the `.npy` headers;
- after a safe conversion: array shapes, finiteness and label counts.

**No vibration value has been looked at.**

## Why

PR #19 adds `calibrated-v1`: each value's relative deviation from history is
divided by its pooled typical spread (floored at 1%), and the score is the maximum.
It was designed from what C1 and C2 taught, especially C2's M03 case, where an
uncalibrated maximum let one noisy value hide anomalies. Those datasets are
consumed, so its benefit must be shown on data not used before.

## Data

- **MillingVibes** (Langer & Widra, Fraunhofer IIS/EAS and IWU, 2022;
  doi:10.24406/fordatis/236; CC BY 4.0). Archive `MillingVibes_Dataset.zip`,
  SHA-256 `4541c429…f2d7`.
- **Content:**
  - aluminium (EN AW 7075) milled on a MAHO MH 800C;
  - enDAQ S5-E25D40 sensor, 3 axes at 8 kHz;
  - 1 s segments of single-axis movements.
- **Labels:** "good" or "bad" surface quality after milling, assigned by a domain
  expert to all movements of a (partial) pattern.
- **Split, given by the authors:**
  - train: 872 segments (549 good, 323 bad);
  - test: 241 segments (149 good, 92 bad).
- **Safe loading.** The `.npy` files are pickled object arrays.
  - `pickletools` showed only three globals: `numpy.core.multiarray._reconstruct`,
    `numpy.ndarray` and `numpy.dtype`.
  - They were loaded with an unpickler restricted to exactly those, and converted
    to `millingvibes.npz` (SHA-256 `3ccf822f…fa2f`).
  - All values are finite.
- **No context per segment.** Direction and pattern are not recorded, so this
  test has a single context. It tests scoring, not context conditioning.

## History and evaluation (fixed)

- **History:** the 549 *good* train segments. Bad train segments are excluded, as
  `exclude_labels` would do.
- **Evaluation:** all 241 test segments. Their labels are used only for scoring.

## Values (pg_morlet `simple-v1`, per axis X, Y, Z)

The eight `simple-v1` values per axis (`morlet_core::values`):
- duration_s, mean, std, rms;
- p75 (numpy linear interpolation);
- peak_abs, crest, kurtosis.

That makes 24 values.

## Scores

| Arm | Definition |
|---|---|
| **A: C2 score (baseline)** | Max over C2's value set (std, kurtosis, crest per axis, plus duration) of the relative deviation \|x − median_hist\| / \|median_hist\|. |
| **B: calibrated-v1 as implemented (candidate)** | Max over all 24 values of the relative deviation divided by the value's spread. |
| B′: calibrated, C2 value set (reported) | As B, over arm A's values. It isolates calibration from the value set. |

- **Spread** (arms B and B′), as `morlet_value_spreads` computes it with a single
  context: median(\|x − median\|) / \|median\| over history, floored at 0.01.
- **Undefined values:** a value with median 0 is skipped in every arm.

## Primary measurement

- **AUROC** of each score for bad vs good over the 241 test segments.
- **ΔAUROC** = AUROC(B) − AUROC(A).
- **CI:** bootstrap over test segments, stratified by label, 2000 resamples,
  seed 7.
  - Labels were given per pattern and pattern IDs are not published, so segments
    are not independent and this CI is optimistic.
  - The rule below therefore asks for a margin.

## Decision rules

- **Supported** (adopt `calibrated-v1` as the default summary score): ΔAUROC
  ≥ 0.05 and the CI lower bound > 0.
- **Worse** (keep the C2 score as default; calibration needs rework): CI upper
  bound < 0.
- **No material difference:** CI within (−0.05, +0.05). Keep both, with no
  default change.
- **Inconclusive:** anything else.

## Reported, not decided on

- Arm B′, and the AUROC of each value alone, per arm.
- The values `top_value` picks for bad and for good segments under arm B.
- **The per-context quantile is not tested here.** With one context it is a
  monotone transform of the score.

## Controls and limitations

- **Identical across arms:** history, test segments and value definitions.
- **One machine, one material, one context.** The labels judge surface quality,
  not machine faults.
- **No tuning.** The floor (0.01), the value sets and the rules are fixed now.
  Nothing is fitted on test segments. The train labels are used only to choose
  history.

## Changes after freeze

(none)
