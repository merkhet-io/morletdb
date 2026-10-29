# L1 results: Kalman level track vs simple smoothing

Run once on 2026-10-28, as frozen in [PROTOCOL.md](PROTOCOL.md) (commit 64a4555), with one
post-hoc analysis logged after the run. Output: [results/l1.json](results/l1.json).
Synthetic data only.

## Verdict under the frozen rule: **unclear**

- **Criterion 1 holds.** The filter's level RMSE is within 5% of the best baseline's on S1–S5;
  it is in fact the lowest on each.
- **Criterion 3 holds.** Prediction-interval coverage is 0.97 on S1 and 0.95 on S5.
- **Criterion 2 fails on S1.** On the constant level, the 95% level interval covers 0.99, above
  the 0.98 bound. The process variance calibrated on drifting development data assumes the
  level can move, so on a truly constant level the interval is too cautious.
- On S5 the level interval covers 0.95.
- **The gap advantage holds:** S5 RMSE is 19% below the frozen EMA's and 39% below the
  post-hoc EMA's.

## Numbers (200 sequences per scenario, episodes after the first 20)

Calibrated on development data: q = 0.001 per hour, r = 0.01 (the true development values);
EMA α = 0.5; rolling window 5.

| Scenario | RMSE Kalman | RMSE EMA (α 0.5) | RMSE rolling median | RMSE EMA (α 0.27, post hoc) | Level coverage | Prediction coverage |
|---|---|---|---|---|---|---|
| S1 constant | 0.040 | 0.058 | 0.054 | 0.040 | 0.99 | 0.97 |
| S2 drift | 0.040 | 0.058 | 0.054 | 0.040 | 0.99 | 0.97 |
| S3 spike | 0.046 | 0.068 | 0.054 | 0.046 | 0.97 | 0.96 |
| S4 step | 0.046 | 0.059 | 0.060 | 0.046 | 0.97 | 0.96 |
| S5 gaps | **0.063** | 0.078 | 0.122 | 0.104 | 0.95 | 0.95 |
| S6 correlated | 0.070 | 0.083 | 0.082 | 0.070 | **0.85** | 0.99 |

- **Step (S4), median episodes to reach the new level:**
  - half the step: Kalman 2, EMA 0, rolling median 2;
  - 90%: Kalman 6, EMA 2, rolling median 3.
- **Spike (S3):**
  - The spike stands out in every method's residuals by about 7 standard deviations
    (Kalman's standardized innovation 7.3, EMA 6.7, rolling median 7.1).
  - The largest level error it causes in the next 5 episodes (leakage): Kalman 0.22, EMA
    0.40, rolling median 0.08.

## What it shows

- **On evenly spaced episodes the filter is an EMA.** At the same gain, the RMSEs match to
  three decimals in S1–S4 and S6. Its lower RMSE than the frozen EMA comes from the EMA's
  compromise α, not from the filter.
- **What the filter adds is real but narrow:**
  - **Irregular gaps.** Uncertainty grows with elapsed time, so the level catches up after a
    48 h pause: 39% lower RMSE than an EMA with the same hourly gain.
  - **Uncertainty.** Prediction intervals are calibrated when the model is close to right
    (0.95–0.97 on S1–S5). Level intervals are too cautious on a truly constant level (0.99)
    and **overconfident when episode noise is correlated** (0.85 on S6), as the documentation
    warns.
- **Short disturbances stay visible.** A spike is a 7σ standardized innovation even though the
  filtered level smooths it. A rolling median smooths it best (leakage 0.08), but by the same
  token says nothing about uncertainty.
- **Steps take a few episodes to follow** (6 to reach 90%). That is the price of smoothing, and
  why residuals, not the level, should be read for sudden changes.

## Recommendation

- **Keep it as an opt-in inspection function**, documented as "an EMA with time-aware
  uncertainty". Its value is irregularly spaced episodes, which is the normal case under
  varying operating conditions: each context is visited at random times (C5's Paderborn
  bearings). The intervals are useful but model-based, and they are overconfident when
  episodes are correlated.
- **Do not build stored trackers.** Nothing here needs persisted state.
- **Real-world value is not established.** No untouched condition-monitoring data remains:
  Paderborn is consumed, and XJTU-SY is reserved for C7.

## Limitations

- Synthetic Gaussian data: it shows that the implementation behaves as the model says, not
  that real vibration levels follow the model.
- One noise level and one development family; heavier-tailed noise is not tested.
- The step and spike sizes were fixed in advance, not varied.
