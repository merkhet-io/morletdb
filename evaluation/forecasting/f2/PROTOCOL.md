# F2: does retrieval help forecast a fast-degrading unit? (C-MAPSS FD001)

**Result: STOP.** Retrieval beats random and shuffled controls (0.84), but a tuned AR model beats retrieval: kNN/AR = 1.067 [1.044, 1.090] at H = 10 on fast-regime origins. The confirmation units are untouched. See [RESULTS.md](RESULTS.md).

Status: **FROZEN** in the commit that adds this file.
- **What has been read:** only the readme and the first rows of `train_FD001.txt`. No
  statistic of the data has been computed.
- **Prior use:** C-MAPSS has never been used in this repository. The confirmation units
  below are untouched.

## Why

F1 (XJTU-SY, 15 bearings) stopped at its gate. Cross-bearing retrieval did not beat
persistence, AR or controls overall. The only signal was in the two fastest failures,
far too few units to judge. F2 asks that narrower question with many more units:
**when a unit is currently degrading fast, do the continuations of similar past segments
from other units forecast better than persistence and AR?**

Not a remaining-useful-life study: failure time is never used to build inputs, normalise
age or define the regime.

## Data

- **Source:** NASA Prognostics Data Repository, "Turbofan Engine Degradation Simulation
  Data Set" (Saxena & Goebel, 2008), downloaded on 2026-10-31.
  - Location: `data/cmapss/raw/cmapss.zip`, 12.4 MB, SHA-256 in `data/cmapss/SHA256SUMS`.
  - Terms: acknowledge the repository and the donors; no licence is named.
- **Used:** `train_FD001.txt`, 100 simulated engines run to failure.
  - One operating condition and one fault mode (HPC degradation).
  - One row per cycle.
- **Target:** y = sensor 11 (Ps30, HPC outlet static pressure; column 16 of 26), raw
  units, one value per cycle.
  - Chosen from the C-MAPSS literature's degradation-sensitive sensors before looking
    at any values.
  - Sensor 4 (T50) is reported descriptively.

## Split (fixed now)

- **Assignment:** unit ids 1–100 are permuted with `numpy.random.default_rng(2026)`.
- **Development:** the first 50, used for the gate.
- **Confirmation:** the last 50, used once and only if the gate passes.
- **Isolation:** no confirmation unit is ever in a gallery, a training set or a
  threshold estimate before the confirmation run. This is asserted.

## Task

- **Origins:** as in F1, an origin t needs 30 values of history (t ≥ 29) and t + H inside
  the unit.
- **Horizon:** H = 10 cycles (primary); H = 1 and H = 30 are descriptive.
- **Fast regime** (primary subset), defined causally:
  - s(t) is the least-squares slope of y over the 20 values ending at t.
  - An origin is fast if s(t) > τ.
  - τ is the 75th percentile of s over all origins of the **development** units, fixed
    before any forecasting and reused for confirmation.

## Methods

Identical to F1 (`../gate.py`, same functions), on the same origins, with an equal budget
of 9 configurations each:
- **Persistence.**
- **AR, direct:** p ∈ {1, 2, 3, 5, 8, 12, 16, 20, 29}.
- **kNN continuation:**
  - offset-corrected context of the last L values;
  - exact Euclidean search over the gallery units' origins;
  - mean of the k nearest continuations Δ = y(t′ + H) − y(t′);
  - L ∈ {5, 10, 30} × k ∈ {1, 5, 25}.
- **Controls:** random retrieval and shuffled continuation, with kNN's tuned
  configuration, averaged over 5 seeds.

## Gate (development)

- **Splits:** leave one development unit out, with nested inner leave-one-unit-out
  tuning on the other 49.
- **Gallery:** all origins of the training units, so neighbours are not restricted to
  the fast regime.
- **Tuning criterion:** the mean over inner units of each unit's MAE **on its fast-regime
  origins**, the same criterion for every method.
- **Primary metric:** the mean over units of each unit's fast-regime MAE at H = 10. Units
  with no fast origin are excluded and counted.
- **Uncertainty:** a 95% bootstrap over units (2000 resamples, seed 7).
- **Best baseline:** whichever of persistence and AR has the lower primary metric.

**PASS** only if all hold (the same rule as F1):
1. kNN / best baseline ≤ **0.90**, with the CI upper bound below 1;
2. kNN beats random retrieval with the CI upper bound below 1;
3. kNN beats shuffled continuation with the CI upper bound below 1.

**Otherwise STOP:** the confirmation units stay untouched, and there is no architecture
search.

## Confirmation (only if the gate passes)

- **Tuning:** each method's configuration is selected by leave-one-unit-out over all 50
  development units, with the same criterion.
- **Gallery and AR fit:** all 50 development units.
- **Run:** one pass over the 50 confirmation units, with the same metric, bootstrap
  (over confirmation units) and three conditions.
- **CONTINUE** only if confirmation passes too.

## Descriptive (decides nothing)

- all origins;
- H = 1 and H = 30;
- per-unit results;
- sensor 4;
- the selected configurations per fold.
