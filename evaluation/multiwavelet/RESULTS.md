# W2 results: Legendre multiwavelet vs kurtogram on XJTU-SY

Run once on 2026-10-30, as frozen in [`PROTOCOL.md`](PROTOCOL.md) at 7ace868. There were
no changes after the freeze. Horizontal channel; the last 10 snapshots of each of the 15
bearings; 19 documented bearing-element pairs.

**Decision under the frozen rule: CLOSE.**

The multiwavelet arm beats the plain kurtogram by a wide margin. The gain comes from the
**frequency-aware selection rule**, not the multiwavelet: given the same rule, the
kurtogram's filter bank does better than the multiwavelet.

| Rule | Comparison | Δ log10 peak ratio | 95% CI (bootstrap over bearings) | Pairs ahead | Met |
|---|---|---|---|---|---|
| 1 | **LMWT-RER vs FK** (primary) | **+0.363** | [+0.234, +0.522] | 15/19 | yes (≥ 0.10, CI > 0) |
| 2 | **LMWT-RER vs FK-RER** (control) | **−0.185** | [−0.364, −0.018] | 3/19 | **no** |
| 3 | Pairs detected: LMWT-RER vs FK | 13 vs 9 | | | yes |

Rule 1 holds and rule 2 fails, so the protocol says **close**, crediting the selection
rule rather than the multiwavelet.

## All arms (horizontal)

| Arm | Mean score (log10 peak ratio) | Pairs detected | Compound pairs detected |
|---|---|---|---|
| RAW (no band selection) | 1.053 | 11/19 | 1/6 |
| **FK** (kurtogram, max spectral kurtosis) | 0.661 | 9/19 | 1/6 |
| **FK-RER** (kurtogram bands, frequency-aware selection) | **1.209** | **13/19** | 2/6 |
| LMWT-K (multiwavelet, max kurtosis) | 0.848 | 11/19 | 2/6 |
| **LMWT-RER** (multiwavelet, frequency-aware selection) | 1.025 | **13/19** | 2/6 |
| EWT-K | 0.920 | 9/19 | 2/6 |
| EWT-RER | 1.124 | 10/19 | 1/6 |

The vertical channel gives the same picture: FK-RER 1.231 > EWT-RER 1.115 > LMWT-RER 1.056 >
RAW 1.083 > FK 0.708. There, LMWT-RER vs FK is +0.349 [+0.218, +0.526].

Descriptive comparisons:

| Comparison | Δ | 95% CI |
|---|---|---|
| FK-RER vs FK | +0.548 | [+0.337, +0.837] |
| EWT-RER vs FK | +0.463 | [+0.268, +0.709] |
| RAW vs FK | +0.391 | [+0.197, +0.634] |
| LMWT-K vs FK | +0.187 | [+0.078, +0.287] |
| LMWT-RER vs EWT-RER | −0.099 | [−0.230, +0.013] |
| Compound only (6 pairs), LMWT-RER vs FK | +0.267 | [+0.214, +0.371] |

## Reading

- **Kurtosis is the weak link, not the filter bank.** Maximum spectral kurtosis picks
  bands that are impulsive but do not carry the fault periodicity in these late-life
  snapshots. Plain FK scores below the unfiltered envelope (RAW).
  - Late-life XJTU-SY damage is severe and broadband, which is where kurtosis-guided
    selection is known to struggle.
  - This is not the regime of W1, which was about early alarms.
- **Frequency-aware selection fixes most of it, for any transform.** FK-RER is the best
  arm. EWT-RER and LMWT-RER follow, and the multiwavelet comes **last** of the three
  RER arms.
  - LMWT has 4 broad, leaky levels against the kurtogram grid's 100+ narrower bands, so
    it has fewer chances to isolate a good carrier.
- **The paper's own comparison does not reproduce here.** EWT scores higher than LMWT
  under both rules:
  - kurtosis rule: EWT-K 0.920 vs LMWT-K 0.848;
  - RER rule: EWT-RER 1.124 vs LMWT-RER 1.025 (Δ −0.099, CI just touching 0).

  LMWT does detect more pairs than EWT (13 vs 10 under RER), so the picture is mixed,
  but there is no clear LMWT advantage over EWT on this data.
- **Compound faults stay hard for every arm.** At best 2 of 6 compound pairs are found:
  - 1_5's outer race is detected by every arm except EWT-K, and its inner race by none;
  - 3_2's inner race only by LMWT-RER, and its outer race only by FK-RER;
  - 3_2's ball and cage by no arm.

  With 2 compound bearings, this is anecdote, not evidence.
- **This repeats the project's pattern:** the wavelet transform is not where the value
  is. Here the gain comes from using known bearing kinematics to choose the band. That
  is a selection rule that works with an ordinary filter bank.

## Validity notes

- **Not the paper's exact method.** The full text was not accessible. Multiplicity 4,
  levels 1–4 and the RER definition are ours (see PROTOCOL).
  - A different multiplicity, or a multiwavelet packet tree with more branches, might
    narrow the gap to FK-RER.
  - The decisive control would still apply: a richer multiwavelet tree would have to
    beat an equally rich classical filter bank under the same rule.
- **Selection uses the bearing's own characteristic frequencies.** It does not use the
  documented failed element. FK-RER and LMWT-RER use the same information, so the
  comparison between them is fair.
- **Nominal speed only, and late-life snapshots only.** The results say nothing about
  early detection; that was W1's question.
- **Numerics:** macOS Accelerate raises spurious floating-point warnings in matrix
  multiplication (seen before in S1). All 2,660 snapshot scores are finite. The
  transform's orthogonality and reconstruction are covered by `test_w2.py`.

Outputs: `results/w2.json` (per arm, per pair, comparisons) and `results/snapshots.json`
(per snapshot, per arm: selected band, score, detection). Cite Wang, Lei, Li & Li (2018)
for XJTU-SY and Zheng et al. (2024) for the method family.
