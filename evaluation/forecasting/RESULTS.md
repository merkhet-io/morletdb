# F1 results: retrieval forecasting of bearing log-RMS trajectories — STOP at the gate

Run once on 2026-10-31 as frozen in [PROTOCOL.md](PROTOCOL.md) (9314a3d). The harness
and tests were committed before the run (ac5a7be).
- **Exploratory:** every XJTU-SY bearing was seen in earlier studies.
- **No settings changed after the freeze.**

**Gate: STOP.** At H = 10 minutes, kNN continuation's mean error is 11% below the best
baseline, but:
- the confidence interval includes no improvement;
- kNN is better on only 7 of 15 bearings;
- it is not distinguishable from random retrieval or from shuffled continuations.

The apparent gain comes almost entirely from two very short, fast-failing bearings.
Representations, metadata controls and MorletDB integration were therefore not run, as
the protocol requires.

## Gate (H = 10, leave one bearing out, nested tuning)

| Method | Mean of per-bearing MAE (ln RMS) |
|---|---|
| Persistence | 0.265 |
| AR, direct (tuned) | **0.263** (best baseline) |
| **kNN continuation (tuned)** | **0.235** |
| Random retrieval (control) | 0.264 |
| Shuffled continuation (control) | 0.264 |

| Gate condition | Ratio | 95% CI (bootstrap over bearings) | Bearings better | Met |
|---|---|---|---|---|
| kNN / best baseline ≤ 0.90, CI below 1 | 0.894 | [0.836, 1.076] | 7/15 | **no** |
| kNN / random retrieval, CI below 1 | 0.891 | [0.833, 1.042] | 8/15 | **no** |
| kNN / shuffled continuation, CI below 1 | 0.891 | [0.834, 1.032] | 8/15 | **no** |

**Tuning:** AR selected p = 8 or 12; kNN almost always selected L = 10, k = 5.

**Other horizons** (predefined, descriptive):
- **H = 1:** 0.052 for persistence against 0.051 for kNN.
- **H = 30** (13 bearings): AR 0.269, kNN 0.266, ratio 0.99 [0.94, 1.07].

## Why the mean favours kNN (per bearing, H = 10)

| Bearing | Origins | Persistence | AR | kNN | Random |
|---|---|---|---|---|---|
| 1_5 | 13 | 1.086 | 1.019 | **0.813** | 1.056 |
| 2_4 | 3 | 1.318 | 1.368 | **1.084** | 1.290 |
| 1_1 | 84 | 0.204 | 0.170 | 0.176 | 0.194 |
| 1_3 | 119 | 0.170 | 0.141 | 0.131 | 0.155 |
| 2_3 | 494 | 0.078 | 0.078 | 0.106 | 0.079 |
| 3_1 | 2,499 | **0.029** | 0.041 | 0.056 | 0.053 |
| 3_2 | 2,457 | **0.093** | 0.115 | 0.119 | 0.102 |
| 3_4 | 1,476 | **0.048** | 0.054 | 0.059 | 0.065 |

All 15 rows are in `results/gate.json`.
- **The fast failures dominate the mean.** 1_5 and 2_4 fail within minutes of their
  first forecast origins. Their errors are 4–40 times those of other bearings, and kNN,
  which retrieves rising segments from other bearings, helps there.
- **The long, slowly degrading bearings go the other way.** On 3_1, 3_2, 3_4 and 2_3
  persistence wins, and kNN is the worst or near the worst.

**Post hoc** (`posthoc.py`, `results/posthoc.json`; not in the protocol, decides nothing):
- **Without 1_5 and 2_4:** kNN against AR is 1.045 [0.987, 1.127] at H = 10, better on
  5 of 13 bearings.
- **Weighting every origin equally** instead of every bearing: persistence is best at
  every horizon. At H = 10, persistence scores 0.074, random retrieval 0.087 and kNN
  0.092.

**Also descriptive:**
- **Degrading origins** (causal rule: more than 1.5 times the bearing's early median):
  kNN against the best baseline is 0.83 [0.71, 1.03]. That's the same picture, dominated
  by the same bearings.
- **Leave one condition out** (gallery from the other two conditions only): kNN against
  the best baseline is 0.99 [0.93, 1.17], better on 5 of 15. Matching the operating
  condition appears to be what little retrieval had.

## Implementation checks

- `test_gate.py` (6 tests):
  - each continuation is the future of its own context;
  - every method uses the same origins;
  - the query bearing can never train or be retrieved (asserted on every prediction);
  - an identical twin bearing is retrieved exactly with zero error;
  - the shuffled control breaks that pairing;
  - AR recovers a linear trend exactly.
- The controls behave as expected: random and shuffled come out close to persistence.

## Verdicts

| Hypothesis | Verdict | Basis |
|---|---|---|
| A similar past implies a usefully similar future (cross-bearing kNN continuation, H = 10) | **PAUSE**: not supported, possibly a small effect in fast-failure tails | Gate failed; CI includes no improvement; no better than random or shuffled retrieval; the gain is confined to 2 of 15 bearings |
| Retrieval adds value beyond metadata and cheap predictors | **Not supported** | Persistence or AR wins on most bearings; the gain vanishes when retrieval must cross operating conditions |
| Wavelets or tokens improve retrieval | **Not tested** | The gate stopped first. Earlier studies already found Morlet ≈ FFT and no wavelet or filter family better than band-pass |
| MorletDB adds measurable benefit over arrays | **Not tested** | Integration was gated on a pass. On speed it would not win (exact search costs about 11 µs per candidate; numpy or pgvector are faster). Its possible contribution is provenance, not shown here |

## Answers to the brief's questions

1. **Does a similar past imply a usefully similar future here?** Not reliably. Within the
   15 XJTU-SY bearings, the continuations of the most similar log-RMS segments from other
   bearings forecast no better than persistence, AR, random retrieval or shuffled
   pairing, except plausibly in the last minutes of the two fastest failures.
2. **Value beyond metadata and inexpensive predictors?** No evidence of it.
3. **Do wavelets or tokens help?** Untested here, because the gate stopped. Prior
   repository evidence makes a representation gain unlikely to rescue a retrieval signal
   that is absent with raw contexts.
4. **Does MorletDB help?** Untested here. It would not help on speed. Exact read-back of
   retrieved evidence remains a plausible systems contribution (see RELATED_WORK.md),
   but needs a task where retrieval works first.
5. **Which next experiment is justified?**
   - **Not a representation or architecture search.**
   - **Fast-failure question:** if anything, a fresh run-to-failure set with many more
     units (e.g. a larger public bearing or turbofan fleet) would show whether retrieval
     helps in the fast-failure regime. That needs a protocol aimed at that regime,
     defined causally.
   - **Separate track:** current→vibration operator learning on Paderborn stays an
     independent proposal, unaffected by this result.

## Sample size

15 bearings, of which 2 drive the result, can detect only large, consistent effects. The
CIs are about ±10–15%. This rules out a large, general benefit, but not a small one or one
confined to particular regimes.

## Files

- `PROTOCOL.md`, `trajectories.py`, `gate.py`, `test_gate.py`, `posthoc.py`
- `results/trajectories.json`: per-snapshot ln RMS for both channels, with file hashes
- `results/gate.json`, `results/posthoc.json`
- `RELATED_WORK.md`: the bounded review. The method is established (RAFT, similarity-based
  prognostics, analog ensembles), and this is a re-test on a new dataset and target.
- `FCN3_FEASIBILITY.md`: no downloads. Blocked on hardware: Linux plus an NVIDIA GPU with
  40 GB or more.

## Reproduce

```sh
.venv-seismic/bin/python -I evaluation/forecasting/trajectories.py data/xjtu-sy/extracted/XJTU-SY_Bearing_Datasets evaluation/forecasting/results/trajectories.json
.venv-seismic/bin/python -I evaluation/forecasting/test_gate.py
.venv-seismic/bin/python -I evaluation/forecasting/gate.py evaluation/forecasting/results/trajectories.json evaluation/forecasting/results
.venv-seismic/bin/python -I evaluation/forecasting/posthoc.py evaluation/forecasting/results
```

XJTU-SY: Wang, Lei, Li & Li (2018). Acquisition and checksums are described in
`evaluation/context/c7` and `evaluation/multiwavelet/PROTOCOL.md`.
