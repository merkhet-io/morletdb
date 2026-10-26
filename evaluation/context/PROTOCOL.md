# C1: does comparing within the same operating context track tool wear better?

**Result: SUPPORTED.** Δρ (conditioned − context-free) = +0.31 [+0.08, +0.54]. See
[results/RESULTS.md](results/RESULTS.md).

Status: **FROZEN** in the commit that adds this line. The archive was downloaded
and its documentation and structure inspected (field names, runs per case,
signal shapes, which runs have a VB entry). **No signal value or VB value was
looked at.** Changes after the freeze are logged under "Changes after freeze".

## Why

MorletDB's product bet ([docs/context-model.md](../../docs/context-model.md))
is "compare a cut with the same cut in the past": deviations are computed
against history from the *same operating context*, not against everything. This
is the first test of that bet on real machining data with measured wear.

## Data

- **NASA PCoE "3. Milling"** (Agogino & Goebel; Goebel 1996):
  - archive SHA-256 `bdba8d52…c474`; inner `mill.zip` hash in
    `data/milling/archive/SHA256SUM`;
  - Matsuura MC-510V, 70 mm face mill with 6 KC710 inserts, 826 rev/min.
- **Structure:** 167 runs in 16 cases. Each case is one insert set under one
  setting, and the setting is fixed within a case:
  - depth of cut 1.5 or 0.75 mm;
  - feed 0.5 or 0.25 mm/rev;
  - cast iron or steel.

  The 8 settings were each run twice with a new insert set. Flank wear VB (mm) is
  measured after some runs; 143 runs have a VB entry.
- **Signals:** 250 Hz, 9000 samples per run (one run has 15360):
  - spindle motor current, AC and DC portion, unprocessed;
  - table and spindle vibration, and table and spindle acoustic emission. These
    four are hardware RMS (8 ms time constant) after band-pass filtering.

  There are no raw high-rate waveforms.
- **Split by insert set, fixed by case number before any value was seen:**
  - **Reference (history):** cases 1–8.
  - **Evaluation:** cases 9–16.
  - Twins by setting: 1↔9, 2↔12, 3↔11, 4↔10, 5↔16, 6↔15, 7↔13 and 8↔14. Every
    evaluation case has exactly one reference case with the same setting.

## Values per run (fixed)

Six simple values, the kind the context model stores:

| Value | Definition |
|---|---|
| `dc` | 75th percentile of `smcDC` |
| `ac` | standard deviation of `smcAC` |
| `vt`, `vs`, `at`, `as` | 75th percentile of `vib_table`, `vib_spindle`, `AE_table`, `AE_spindle` |

The 75th percentile reflects the cutting plateau without detecting entry and
exit. No value or definition is tuned.

## Compare-to-history

- **History (reference) runs:** runs of cases 1–8 with VB ≤ 0.2 mm, i.e. a
  sharp-tool history. Runs without VB are not used as reference.
- **Robust z per value:** z = (x − median_ref) / (1.4826 · MAD_ref). A value
  with MAD = 0 in its reference is skipped.
- **Score:** the run's maximum |z| over the values, as defined in
  `docs/context-model.md` §5. *Amended (change 1): the primary score is the
  maximum relative deviation |x − median_ref| / |median_ref|; robust z is
  reported.*
- **Arms:**
  - **conditioned:** the reference is the history runs with the same (depth of
    cut, feed, material), i.e. the twin case's sharp runs;
  - **context-free:** the reference is all history runs from all settings.
- **Minimum history:** 3 runs. If an evaluation case's conditioned reference has
  fewer than 3 runs, that case is excluded from the primary comparison and
  reported. This is likely for case 15, whose twin, case 6, has a single run.

## Primary measurement (evaluation cases 9–16, runs with VB)

- **Correlation:** ρ = Spearman rank correlation between score and VB, pooled over
  the evaluation runs that have VB.
- **Paired difference:** Δρ = ρ(conditioned) − ρ(context-free), computed over the
  same runs.
- **CI:** bootstrap over evaluation cases, 2000 resamples, seed 7. Each resample
  draws cases with replacement and recomputes both pooled ρ on the drawn runs.

## Decision rules

- **Supported:** Δρ ≥ 0.10 and the CI lower bound of Δρ is > 0.
- **Not supported:** the CI upper bound of Δρ is < 0.10. Context conditioning
  does not materially improve wear tracking here.
- **Inconclusive:** anything else. There are 8 evaluation cases; report and stop.

## Reported, not decided on

- **Each value alone**, both arms. Spearman ρ with VB is sign-aware: it shows
  which channels carry wear information.
- **Time in use:** Spearman ρ of the run's `time` field with VB. Wear grows with
  use, so a sensor score that does not beat this simple clock adds little for
  wear. This is the key context for reading the result.
- **AUROC** of each score for VB ≥ 0.3 mm (the common uniform-flank-wear tool-life
  criterion), if both classes occur.
- **Cases without a usable reference,** for each arm.

## Controls and limitations

- **Same for both arms:** runs, values, score function and history pool. The
  arms differ only in which history runs count as the same context.
- **Lab data with known settings.** The context is clean and given, which is the
  best case for conditioning. A real shop has noisier context; this tests the
  mechanism, not field performance.
- **No NC blocks.** The context here is the cutting setting, not program or
  block. Within-run segmentation is not tested.
- **Small sample:** 8 evaluation cases, about 80 runs with VB, one rig.
- **Signals are hardware RMS at 250 Hz,** so no waveform-level representation is
  tested.
- **Expected direction.** Different settings shift current and vibration levels,
  so conditioning is expected to help. The test measures how much, and the
  time-in-use control shows whether the sensor score adds anything beyond the
  clock.

## Changes after freeze

1. **Primary score changed from robust z to relative deviation.** This was
   prompted by the synthetic controls (`test_c1.py`), before any milling value
   was read.
   - **Problem:** with 3–4 sharp reference runs per setting, the MAD in the
     conditioned arm is so noisy that the conditioned robust z loses 0.35 in
     Spearman ρ on synthetic data *without any context effect*. Each case gets its
     own noisy scale. The comparison would then measure history size, not context.
   - **New primary score:** max over values of |x − median_ref| / |median_ref|.
     It needs no spread estimate, so the arms differ only in which runs count as
     the same context.
   - **Robust z** (the score sketched in `docs/context-model.md`) is still
     computed and reported for both arms.
   - **Kept:** the decision rules, split, values and reference set.
   - **Product finding:** robust z needs either more history or a pooled or
     floored scale. This feeds back into the context-model design.
