# F2 results: retrieval forecasting in the fast-degradation regime (C-MAPSS FD001) — STOP

Run once on 2026-10-31 as frozen in [PROTOCOL.md](PROTOCOL.md) (59c3500). The harness
and tests were committed before the run (2ab9a31).
- **No settings changed after the freeze.**
- **The 50 confirmation units were not used:** the gate failed, so they remain
  untouched.

**Gate: STOP.** Retrieval carries real information:
- kNN beats random retrieval and shuffled continuations by 16–23%, with tight intervals;
- the context-to-future pairing matters.

But a tuned AR model forecasts better than retrieval:
- **At H = 10 on fast-degradation origins,** kNN's error is 6.7% *higher* than AR's,
  with a CI entirely above 1.
- **The same holds** at every horizon, on all origins, and for the second sensor.

## Gate (development: 50 units, leave one out with nested tuning)

- **Fast regime:** slope over the last 20 cycles above τ = 0.0069 per cycle, the 75th
  percentile of development origins.
- **Coverage:** 1,778 of 8,158 origins are fast, and all 50 units have some.

**Mean of per-unit MAE, sensor 11 (Ps30), fast-regime origins:**

| Method | H = 1 | **H = 10 (primary)** | H = 30 |
|---|---|---|---|
| Persistence | 0.118 | 0.130 | 0.201 |
| **AR (best baseline)** | **0.089** | **0.099** | **0.129** |
| kNN continuation | 0.091 | 0.106 | 0.143 |
| Random retrieval | 0.119 | 0.126 | 0.164 |
| Shuffled continuation | 0.119 | 0.126 | 0.164 |

| Gate condition (H = 10, fast) | Ratio | 95% CI over units | Units better | Met |
|---|---|---|---|---|
| kNN / AR ≤ 0.90, CI below 1 | **1.067** | [1.044, 1.090] | 10/50 | **no** |
| kNN / random retrieval | 0.837 | [0.811, 0.863] | | yes |
| kNN / shuffled continuation | 0.835 | [0.809, 0.861] | | yes |

**Other comparisons** (descriptive):

| Comparison | Ratio | 95% CI |
|---|---|---|
| kNN / AR, H = 1, fast | 1.030 | [1.014, 1.045] |
| kNN / AR, H = 30, fast | 1.107 | [1.071, 1.147] |
| kNN / AR, all origins, H = 1 | 1.028 | [1.022, 1.035] |
| kNN / AR, all origins, H = 10 | 1.037 | [1.026, 1.049] |
| kNN / AR, all origins, H = 30 | 1.049 | [1.032, 1.068] |
| kNN / AR, sensor 4 (T50), H = 10, fast | 1.057 | [1.037, 1.077] |

## Reading

- **A similar past does carry information about the future here,** unlike in F1.
  Matched continuations beat random and shuffled ones decisively, on 50 units.
- **That information is already available to a linear model of the unit's own recent
  history.** AR on 29 lagged differences forecasts the next 1–30 cycles better than
  averaging 25 matched continuations from other units. The gap grows with horizon.
- **The fast regime doesn't change that.** It is where F1 hinted at a benefit, but here
  AR's advantage is, if anything, larger on fast origins than on all origins.
- **Together with F1:** on two independent fleets (real bearings, simulated turbofans),
  retrieving other units' continuations has not beaten a tuned AR model of the unit's
  own trajectory.

## Limitations

- **Both tuned methods sat at the edge of their equal-budget grids in every fold:** AR
  at p = 29, kNN at L = 30 and k = 25. Both preferred the most smoothing available,
  consistent with per-cycle sensor noise.
  - A wider grid (longer contexts, more lags, more neighbours) might improve both. It
    was not tried, because the protocol forbids re-tuning after the run.
  - Whether wider grids would reverse the ordering is unknown. The gap at the grid edge
    is 6.7% with a tight CI.
- **The data is simulated,** with one operating condition and one fault mode. The fault
  modes and conditions of FD002–FD004 were not tested.
- **The point predictor is a uniform mean of k continuations.** Distance weighting or a
  learned head (as in RAFT) might do better. That would be a different method, and the
  F1 and F2 briefs ruled out searching for one after a failed gate.

## Verdict

| Hypothesis | Verdict |
|---|---|
| Retrieved continuations carry predictive information beyond chance | **Supported** on C-MAPSS FD001 (not on XJTU-SY, F1) |
| Retrieval beats the strongest inexpensive baseline (AR), overall or in the fast regime | **Rejected** on FD001 development data: AR is better, CI excludes no difference |

**Next step justified by the evidence:** none for retrieval forecasting as a point
predictor.
- If retrieval is pursued again, the open question is whether retrieved continuations
  **add** to AR, for example as extra inputs to the same AR model, compared with AR
  alone.
- That would need its own frozen protocol, and the 50 untouched confirmation units are
  available for it.

## Reproduce

```sh
# data: NASA PCoE repository, entry 6 (12.4 MB); see PROTOCOL.md and data/cmapss/SHA256SUMS
.venv-seismic/bin/python -I evaluation/forecasting/f2/test_f2.py
.venv-seismic/bin/python -I evaluation/forecasting/f2/f2.py gate data/cmapss/extracted/CMAPSSData/train_FD001.txt evaluation/forecasting/f2/results
```

`results/gate.json` holds the per-unit results for every method, horizon and subset, the
selected configurations, the split and τ.

Acknowledgement: A. Saxena and K. Goebel (2008), "Turbofan Engine Degradation Simulation
Data Set", NASA Prognostics Data Repository, NASA Ames Research Center.
