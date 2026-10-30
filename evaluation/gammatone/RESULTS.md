# G1 results: gammatone filter bank vs kurtogram bands (CWRU)

Run once on 2026-10-30, as frozen in [`PROTOCOL.md`](PROTOCOL.md) at 4525571. There were
no changes after the freeze. Drive-end channel, first 10 s of each of the 60 CWRU fault
files: 15 fault conditions × 4 loads.

**Decision under the frozen rule: CLOSE.**

Given the same bands and the same frequency-aware selection rule, swapping the
Butterworth band-pass for a gammatone of identical centre and equivalent bandwidth changes
nothing measurable. The matched-filter argument for the asymmetric shape did not show up.

| Rule | Comparison | Δ log10 peak ratio | 95% CI (bootstrap over 15 fault conditions) | Files ahead | Outcome |
|---|---|---|---|---|---|
| Primary | **GTFK-RER vs FK-RER** | **−0.021** | [−0.071, +0.025] | 28/60 | CI upper < 0.10 → **close** |
| Secondary | GTERB-RER vs FK-RER | +0.074 | [−0.014, +0.168] | 39/60 | inconclusive; claims nothing |

## All arms (drive end, 60 fault files)

| Arm | Mean score | Detected | Inner | Ball | Outer | False detections on 4 normal files (12 checks) |
|---|---|---|---|---|---|---|
| RAW | 1.752 | 41/60 | 13/16 | 4/16 | 24/28 | 0/12 |
| FK (kurtogram, max kurtosis) | 1.647 | 41/60 | 12/16 | 5/16 | 24/28 | 0/12 |
| **FK-RER** | 1.860 | 46/60 | 15/16 | 5/16 | 26/28 | 4/12 |
| GTFK-K | 1.624 | 38/60 | 12/16 | 2/16 | 24/28 | 0/12 |
| **GTFK-RER** | 1.839 | **47/60** | 16/16 | 6/16 | 25/28 | 3/12 |
| GTERB-K | 1.682 | 38/60 | 11/16 | 3/16 | 24/28 | 0/12 |
| GTERB-RER | **1.934** | 40/60 | 12/16 | 3/16 | 25/28 | 4/12 |

The fan-end channel (52 files; the 0.028" files have none) gives the same picture.
GTFK-RER vs FK-RER is −0.019 [−0.062, +0.037].

Descriptive comparisons (drive end):

| Comparison | Δ | 95% CI |
|---|---|---|
| FK-RER vs FK | +0.213 | [+0.077, +0.344] |
| RAW vs FK | +0.105 | [+0.009, +0.215] |
| GTFK-K vs FK | −0.022 | [−0.091, +0.047] |
| GTERB-K vs FK | +0.035 | [−0.048, +0.128] |
| GTERB-RER vs GTFK-RER | +0.095 | [−0.010, +0.202] |

## XJTU-SY (descriptive; not blind, since W2 used these snapshots)

| Arm (horizontal) | Mean score | Pairs detected |
|---|---|---|
| RAW | 1.053 | 11/19 |
| FK | 0.661 | 9/19 |
| FK-RER | 1.209 | 13/19 |
| GTFK-K | 0.673 | 10/19 |
| GTFK-RER | 1.149 | 12/19 |
| GTERB-K | 0.678 | 10/19 |
| GTERB-RER | **1.564** | 13/19 |

| Comparison | Δ | 95% CI (bootstrap over bearings) |
|---|---|---|
| GTFK-RER vs FK-RER | −0.061 | [−0.111, −0.017] |
| GTERB-RER vs FK-RER | +0.354 | [+0.246, +0.448] |
| GTFK-K vs FK | +0.011 | [−0.006, +0.039] |
| GTERB-K vs FK | +0.017 | [−0.016, +0.045] |

The vertical channel agrees: GTERB-RER 1.587, FK-RER 1.231 and GTFK-RER 1.155, with
13 vs 12 vs 11 pairs detected.

## Post-hoc check: the ERB score gain is a bandwidth effect of the measure

This analysis was not in the protocol. It was run after both results were known, because
the ERB arm raised the score without raising detections.

| Width of the ERB band selected by GTERB-RER | XJTU-SY: n, mean gain vs FK-RER | CWRU: n, mean gain vs FK-RER |
|---|---|---|
| < 300 Hz | 159, **+0.423** | 32, **+0.181** |
| 300–500 Hz | 13, −0.044 | 23, −0.017 |
| ≥ 500 Hz | 18, −0.023 | 5, −0.193 |
| Correlation of width with gain | −0.40 | −0.54 |

The whole gain comes from bands narrower than 300 Hz.
- **Why this inflates the score:** a band of width B carries little envelope content above
  B. The median over 5–500 Hz then falls, so the peak/median ratio rises regardless of
  how well the fault line stands out from its neighbours.
- **The detection count does not depend on the median, and it shows no gain:** CWRU 40 vs
  46, XJTU-SY 13 vs 13.
- **So the ERB "advantage" is a property of the score, not of the filter.**
- The kurtogram grids already respect bandwidth ≥ 500 Hz, so they are not affected. W2's
  LMWT levels were all wider than 500 Hz.

## Reading

- **Filter shape does not matter here.** With centres, bandwidths and selection rule held
  equal, the asymmetric gammatone and the flat Butterworth score within 0.02 of each
  other, under both selection rules and on both channels. They detect 47 vs 46 files.
- **The auditory ERB bank is mixed, not better.**
  - Its mean peak ratio is the highest, on CWRU and much more so on XJTU-SY. The post-hoc
    check above traces this to bands narrower than 300 Hz, which deflate the median of the
    score.
  - It detects fewer files: 40 vs FK-RER's 46. It finds none the kurtogram bands miss,
    and misses 6 they find.
  - The secondary CI crosses zero. By the protocol this claims nothing.
- **On XJTU-SY the gammatone shape is slightly worse** than Butterworth under the same
  rule (−0.06, CI below zero). That is descriptive only, but it points the same way as the
  CWRU tie.
- **W2's finding replicates on a second, unseen dataset.** Frequency-aware selection beats
  kurtosis selection (FK-RER vs FK +0.21, CI above zero), and plain kurtosis selection
  again does no better than the unfiltered envelope.
- **New: frequency-aware selection has a false-alarm cost.**
  - On the 4 healthy recordings, the RER arms report a fault frequency among the top 5
    envelope peaks in 3–4 of 12 checks, mostly BPFI.
  - The kurtosis-rule arms and RAW report none.
  - Because the rule hunts for the band where the kinematic frequencies stand out, it also
    finds incidental peaks there. W2 had no healthy controls, so it could not see this.
  - The count is tiny: 4 files, one rig. But any use of RER as an alarm, rather than as a
    diagnosis after an alarm, needs a healthy-baseline threshold.
- **Ball faults are hard for every arm** (at most 6/16), as Smith & Randall's benchmark
  reports for this dataset.

## Validity notes

- **Seeded faults on one rig, 15 independent fault conditions.** The loads share a
  bearing.
- **6 of the 60 files were seen before,** in the early retrieval baseline. That was a
  different task with no envelope analysis.
- **Nominal speeds:** the ±2% windows absorb the slip.
- **Numerics:** all 784 fault-file and 84 normal-file scores are finite.

Outputs:
- `results/g1_cwru.json`: arms, comparisons, decision.
- `results/cwru_files.json`: per file and arm, the selected band, score and detection.
- `results/g1_xjtu.json` and `results/xjtu_snapshots.json`: the descriptive XJTU-SY run.

Cite the CWRU Bearing Data Center (Case Western Reserve University), Smith & Randall
(MSSP 2015), and Glasberg & Moore (1990) for the ERB scale.
