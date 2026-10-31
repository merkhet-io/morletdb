# X1 results: reference coding across repeated machining operations — PAUSE

Run once on 2026-10-31 under the feasibility gate frozen in [PROTOCOL.md](PROTOCOL.md)
(135be68). Development data only (Bosch CNC, Feb and Aug 2019); the 2020–2021
confirmation split was not touched.

**Decision: PAUSE at the feasibility gate.** **Cause: poor similarity between
executions.**
- **The gate:** even with the best earlier execution, the best shift within ±50 ms and
  an optimal gain all chosen with hindsight, coding an execution as a reference plus a
  lossless residual would save **0.002%** of total storage. The gate required 25%.
- **Why:** separate executions of the same operation are essentially uncorrelated sample
  by sample.
- **Nothing was built:** no codec, format or PostgreSQL prototype.

## Gate

| | Value |
|---|---|
| Development executions (all in the denominator) | 862 |
| Eligible: Aug 2019 with a Feb 2019 execution of the same machine and operation | 461 (277 Aug 2019 executions had none) |
| **Aggregate upper-bound saving** | **0.0019%** (gate: 25%) |
| Same, eligible executions only | 0.0037% |
| Executions with a saving of at least 25% | 0 of 461 |
| Best single execution | 0.1% (M01 OP13, example 3) |
| Codec cost-model rate | 9.03 bits/sample |

## Why: correlation between executions

Distributions are over all eligible execution-axis pairs, with the best of all candidate
references.

| Correlation with the reference | 10th percentile | Median | 90th percentile |
|---|---|---|---|
| Raw samples, no shift | −0.058 | −0.005 | 0.065 |
| Raw samples, best shift ≤ ±100 samples | −0.019 | 0.019 | 0.110 |
| Codec predictor residuals, no shift | −0.016 | 0.000 | 0.016 |
| Codec predictor residuals, best shift | 0.007 | 0.018 | 0.042 |
| Gaussian bound on saving (bits/sample) | 0.00004 | 0.0002 | 0.0013 |

- **Highest values anywhere:** a residual correlation of 0.21 and a raw correlation of
  0.55.
- **What would be needed:** saving 25% at about 9 bits per sample requires a residual
  correlation of about 0.97 or more.
- **What it means:** vibration at 2 kHz from repeated cuts does not repeat at the sample
  level. The codec's own short predictor already captures what is predictable within an
  execution, and what's left is effectively independent between executions.

## Checks

- **Cost model against the real codec** (`model_check.py`, `results/model_check.json`):
  60 random eligible executions (seed 7), 180 axis pairs.
  - The model's saving differs from `morlet_encode_chunk`'s by at most 0.9% of cost(x)
    (median 0.05%); the protocol allows 10%.
  - The model's cost(x) is 0.994 of the real size.
  - **With the real codec, the residual is 2.0% larger than the original** on this subset:
    reference coding loses outright.
- **Pairs within a period** (`within_period.py`, `results/within_period.json`), descriptive
  only. Each Aug 2019 execution is paired with the nearest lower example number of the
  same machine and operation, assuming but not knowing that numbers are chronological.
  - Results: 695 pairs, upper-bound saving 0.018%, median best residual correlation 0.036
    (90th percentile 0.126).
  - Executions possibly closer in time are no more alike.
- **Synthetic self-check of the helpers before running:** a known 37-sample shift was
  recovered with a = 1.0 and a zero residual over the overlap.

## Limits

- **Data:** one dataset, 2 kHz vibration on three machines.
  - **Not covered:** signals that are deterministic sample by sample, such as spindle
    current or encoder positions, or higher-rate data with phase-locked triggering. Those
    might correlate far better, but nothing here shows it.
- **Context:** without NC-block boundaries, operations are whole files. Segments aligned
  to NC blocks might align better.
  - The ±100-sample shift search would already have found a moderate misalignment.
  - The near-zero correlations after the best shift argue against alignment being the
    missing piece.
- **Development data only.** No confirmation run was needed, because the bound fails by
  four orders of magnitude.

## Reproduce

```sh
.venv-compress/bin/python -I evaluation/compression/reference/feasibility.py data/bosch/repo/data evaluation/compression/reference/results
.venv-seismic/bin/python -I evaluation/compression/reference/model_check.py evaluation/compression/reference/results
.venv-compress/bin/python -I evaluation/compression/reference/within_period.py data/bosch/repo/data evaluation/compression/reference/results
```

`feasibility.py` writes `model_check.npz`, the 60 executions' samples and residuals
(68 MB). It is not committed; `model_check.py` reads it.
