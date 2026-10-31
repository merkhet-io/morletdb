# F1: does a similar past imply a similar future? Retrieval forecasting of bearing log-RMS trajectories

Status: **development gate FROZEN** in the commit that adds this file.
- **Computed so far:** only the trajectories (`trajectories.py`). No forecast or baseline
  has been computed, and the trajectories have not been inspected beyond their lengths.
- **Exploratory:** every XJTU-SY bearing was used in earlier studies (C7 used whole-life
  window features; W2 and G1 used the last 10 snapshots). No untouched test data exists,
  so no result here is held-out confirmation.

## Question

When forecasting a slow observable of a degrading bearing, do the continuations of the
most similar past trajectory segments **from other bearings** beat persistence and a
tuned autoregressive model?
- **Not a remaining-useful-life study:** failure time is never used to build inputs,
  normalise age or select origins.

## Data and target

- **Data:** XJTU-SY (Wang, Lei, Li & Li, IEEE Trans. Reliability 2018).
  - 15 LDK UER204 bearings run to failure under 3 conditions: 35 Hz/12 kN (1_1–1_5),
    37.5 Hz/11 kN (2_1–2_5) and 40 Hz/10 kN (3_1–3_5).
  - One 32,768-sample snapshot (1.28 s at 25.6 kHz) every **60 s**; between 42 and
    2,538 snapshots per bearing.
  - Files are hashed in `results/trajectories.json`.
- **Target:** y = ln RMS of the mean-removed **horizontal** channel over each whole
  snapshot, one value per minute.
  - No other smoothing or normalisation.
  - The vertical channel is computed but not used in the gate.

## Forecast task

- **Origin t:** the index of the last observed snapshot.
- **Prediction:** ŷ(t + H) for **H = 10** snapshots (10 min, primary). H = 1 and H = 30
  are also reported, descriptively.
- **Origins:** every t with t ≥ 29, so 30 values of history (the longest context), and
  t + H ≤ n − 1.
  - The origin set is the same for every method and configuration.
  - Bearing 2_4 (42 snapshots) and 1_5 (52) still have a few origins at H = 10. A
    bearing with no origin at a horizon is reported and excluded at that horizon.
- **Splits:** leave one bearing out, as the outer loop.
  - The query bearing is never in the gallery or the training data. This is asserted
    for every prediction.
  - Origins within a bearing overlap. That bearing is the unit of analysis and of
    resampling, so windows are never split randomly.

## Methods (gate)

| Method | Prediction | Tuned over (equal budget, 9 configurations each) |
|---|---|---|
| Persistence | y(t) | – |
| AR, direct | y(t) + f(last p differences of y, intercept). f is least squares (ridge 1e−6) fitted on the training bearings' origins, pooled | p ∈ {1, 2, 3, 5, 8, 12, 16, 20, 29} |
| **kNN continuation** | y(t) + mean of the k nearest gallery Δ, where Δ = y(t′ + H) − y(t′) | L ∈ {5, 10, 30} × k ∈ {1, 5, 25} |

**kNN details:**
- **Context:** c = (y(t − L + 1), …, y(t)) − y(t), offset-corrected with the last value
  only. That uses context information only.
- **Distance:** Euclidean.
- **Gallery:** every origin of every training bearing, with the same origin rule, paired
  with its own continuation through the bearing and snapshot index.
- **Search:** exact. No index, so index approximation cannot affect the result.

**Controls:** both use the tuned kNN configuration of each outer fold, averaged over 5
seeds.
- **Random retrieval:** the mean Δ of k random gallery entries.
- **Shuffled continuation:** the kNN neighbours, but each paired with the Δ of a randomly
  permuted gallery entry. This breaks the context-to-future pairing.

**Tuning:** nested. For each held-out bearing, every method selects its configuration by
inner leave-one-bearing-out on the 14 training bearings. The criterion is the mean over
inner bearings of each bearing's MAE.

## Metric and gate

- **Per bearing:** the mean absolute error of ŷ against y at H = 10, over its origins.
- **Primary metric:** the mean of the per-bearing MAEs over the bearings with origins,
  so each bearing weighs equally.
- **Uncertainty:** a 95% bootstrap over bearings (2000 resamples, seed 7) of the ratio
  of mean MAEs.
- **Best baseline:** the lower primary metric of persistence and AR.

**PASS** (continue to representations and integration) only if all hold:
1. kNN's primary metric ≤ **0.90 ×** the best baseline's (a 10% lower error), with the
   CI upper bound of the ratio below 1.0;
2. kNN beats random retrieval with the CI upper bound of the ratio below 1.0;
3. kNN beats shuffled continuation the same way.

**Otherwise STOP:** report and do not search architectures. The verdict also states
whether 15 bearings are enough to support it: if the CI is wide, it is "inconclusive",
not "no effect".

## Reported descriptively (decides nothing)

- H = 1 and H = 30;
- per-bearing results;
- a "degrading" subset: origins where y(t) exceeds the median of that bearing's first 10
  values by more than ln 1.5. That is causal, using only the bearing's own past;
- a leave-one-condition-out variant, where the gallery and training data come only from
  the other two conditions;
- the selected configurations per fold.

## Not in the gate

Representations other than offset-corrected raw context, distance weighting, metadata or
age matching, MorletDB integration, the vertical channel, and any learned model. These
follow only if the gate passes, under a protocol written then.
