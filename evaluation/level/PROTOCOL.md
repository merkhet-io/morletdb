# L1: is a Kalman level track worth keeping over simple smoothing?

Status: **FROZEN** in the commit that adds this line, before `evaluate.py` is run. Changes
after the freeze are logged under "Changes after freeze".

## Question

`morlet_level_track` (SQL 0.3.1) runs a local-level Kalman filter over one value per episode,
for example ln(std), in one operating context of one machine. Simpler options exist: an
exponential moving average (EMA) and a rolling median. With fixed variances and evenly spaced
episodes, the filter's gain settles to a constant, which *is* an EMA. So the filter can only
earn its maintenance through what an EMA lacks:

- **uncertainty:** a level variance and a prediction variance for the next observation;
- **irregular gaps:** uncertainty that grows with elapsed time, so the level catches up faster
  after a long pause.

L1 asks whether those two properties hold on sequences with a known latent level, and whether
the filter's level is no worse than the baselines'. It does not ask whether the level predicts
faults; there is no untouched real data for that (see "Real data").

## Model and settings (fixed now)

- Observations y = latent ln-level + noise, noise standard deviation 0.1 (variance 0.01).
- **Kalman:** `kalman.track`, identical to the SQL function (checked by `sql_check.py`).
- **Calibration, on development sequences only:** q and r by maximum likelihood on a fixed
  log10 grid (−8 to 2 in steps of 0.25, `kalman.calibrate`), with the log-likelihoods of all
  development sequences summed.
- **EMA:** α from {0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5}, chosen on the development sequences
  by the smallest mean squared one-step prediction error.
- **Rolling median:** window from {5, 10, 20, 40} episodes, chosen the same way.
- **Development sequences:** 50, seeds 0–49. Each has 200 episodes of a random walk
  (q = 0.001 per hour, r = 0.01). Half have hourly episodes. Half have gaps: 85% of intervals
  are 1 h and 15% are 48 h.
- **Evaluation sequences:** 200 per scenario, seeds 1000–1199, with 200 episodes each.

| Scenario | Latent ln-level | Intervals | Noise |
|---|---|---|---|
| S1 constant | 0 | 1 h | iid |
| S2 drift | 0 → 0.3, linear | 1 h | iid |
| S3 spike | 0; observation 100 gets +0.8 (8 σ) | 1 h | iid |
| S4 step | 0, then +0.3 from episode 100 | 1 h | iid |
| S5 gaps | random walk, q = 0.001 per hour | 85% 1 h, 15% 48 h | iid |
| S6 correlated | as S1 | 1 h | AR(1), ρ = 0.7, same marginal variance |

S6 breaks the independence assumption on purpose, to measure how overconfident the intervals
become.

## Metrics (episodes after the first 20)

- **Level RMSE** against the latent level (S3: the latent level, without the spike).
- **Coverage** (Kalman only): the share of episodes with the latent level inside level ±
  1.96·√level_var, and with the observation inside predicted_level ± 1.96·√innovation_var.
- **Step response** (S4): episodes after the step until the estimate first exceeds 0.15 (half
  the step) and 0.27 (90%).
- **Spike visibility** (S3): the spike's residual divided by the standard deviation of the
  residuals at other episodes. For Kalman, the standardized innovation. **Spike leakage:** the
  largest |level − latent| in the 5 episodes from the spike on.

## Decision (fixed now)

- **Keep** if all of these hold:
  1. Kalman's level RMSE is within 5% of the best baseline's on each of S1–S5;
  2. its 95% level coverage is between 0.90 and 0.98 on S1 and S5, where the model is
     correctly specified or close to it;
  3. its prediction-interval coverage is between 0.90 and 0.98 on S1 and S5.
- **Gap advantage** is reported separately: S5 level RMSE at least 10% below the EMA's.
- **Drop** if criterion 1 fails on two or more scenarios, or if coverage is outside 0.80–0.99
  on S1 or S5.
- **Otherwise unclear.** Report and do not tune.

## Real data

- **Paderborn run-to-failure** (C5, C6) is consumed. It may be used only to illustrate the
  function in the walkthrough, labelled as development data.
- **XJTU-SY** is reserved for C7.
- No untouched condition-monitoring data remains, so real-world value is **not established**
  by L1.

## Changes after freeze

None yet.
