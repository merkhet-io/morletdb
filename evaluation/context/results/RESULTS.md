# C1 result: context-conditioned compare-to-history on NASA milling

**Decision under the frozen rules (with amendment 1): SUPPORTED.**

The comparison scores each run by its deviation from history:
- **Conditioned:** history from the same cutting setting.
- **Context-free:** history from all settings.

Measured against flank wear:

| Score | Spearman ρ with VB | AUROC, VB ≥ 0.3 mm |
|---|---|---|
| **Conditioned** | **0.908** | **0.974** |
| Context-free | 0.598 | 0.792 |
| **Δρ (conditioned − free)** | **+0.31 [95% CI +0.08, +0.54]** | |
| *Time in use (control)* | 0.794 | 0.909 |

The rule (Δρ ≥ 0.10 and CI lower bound > 0) is met.

- **Protocol:** [../PROTOCOL.md](../PROTOCOL.md). It was frozen at `f8af300`
  after reading only the documentation and structure.
- **Amendment 1:** the primary score was changed to relative deviation, before any
  value was read, prompted by synthetic controls. It is committed at `7e424e3`.
- **Run:** one run of `c1.py` at `7e424e3`, on 2026-10-26.
- **Data:** NASA PCoE "3. Milling" (Agogino & Goebel), archive SHA-256
  `bdba8d52…c474`. Cite Goebel (1996) and the NASA Prognostics Data Repository.

## Population

- **Evaluation cases 9–16,** each the second insert set of a setting whose first
  set (cases 1–8) provides the history.
- **History:** reference runs with VB ≤ 0.2 mm, 26 runs.
- **Excluded by the frozen minimum-history rule (< 3 runs):** steel cases 14, 15
  and 16. Their same-setting history had 2, 1 and 2 sharp runs.
- **Scored:** 64 runs with VB from cases 9–13, four cast-iron settings and one
  steel setting. 33 of them have VB ≥ 0.3 mm.

## Data-quality finding after the run (sensitivity check)

The database rejected one run when the data was loaded through pg_morlet for the
phase-1 implementation check, because of samples beyond ±1e9.
- **The run:** case 2, run 1, a sharp reference run (VB 0.08 mm).
- **The corruption:** values up to about 1e34 on every channel, in roughly 1% of
  samples at each tail.
- **Effect on C1:** `c1.py` did not screen for it, so the run entered C1's history
  for its setting and the global history.
  - Its 75th-percentile values look normal.
  - Its standard deviation (`ac`) is meaningless.

**Sensitivity:** the C1 analysis without that run (descriptive; the frozen
result above stands as run):

| | ρ conditioned | ρ context-free | Δρ [95% CI] | Decision |
|---|---|---|---|---|
| As run (frozen) | 0.908 | 0.598 | +0.31 [+0.08, +0.54] | supported |
| Without case 2 run 1 | 0.891 | 0.576 | +0.31 [+0.06, +0.57] | supported |

The conclusion does not depend on the corrupt run. **Lesson for the product:**
input validation at ingest catches corrupt acquisitions that an offline
analysis silently averages over.

## What else the run shows (reported, not decided on)

- **Robust z fails with small histories, as the synthetic control predicted.**
  The conditioned robust z reaches ρ = 0.505, below the context-free 0.640.
  - With 3–6 reference runs, the MAD is too noisy to put runs on a common scale.
  - **For the product:** `morlet_compare_to_history` as sketched in
    `docs/context-model.md` §5 needs a scale that survives small histories, or a
    relative-deviation mode. A minimum-history count alone does not guarantee
    a stable scale.
- **Channels:**
  - spindle current (DC level, AC spread) and acoustic emission rise with wear
    (per-value ρ 0.71–0.88, conditioned);
  - the vibration RMS channels fall (ρ −0.10 and −0.58).

  The primary score uses |deviation|, so both directions count.
- **Against the clock** (exploratory; not in the protocol):
  - pooled over cases, conditioned − time-in-use Δρ = +0.11 [−0.05, +0.24];
  - within each case, time is perfectly monotone with VB (ρ = 1.00), because
    lab wear only grows, while the conditioned score reaches 0.87–1.00.

  For gradual wear under constant conditions, counting cutting time is
  nearly as good. The sensor score earns its keep where the clock cannot:
  - different settings wear at different rates;
  - no tool age is known;
  - sudden events (chipping, breakage, wrong setup, air cuts) are not a function
    of time.

  This dataset does not test those.

## What this does and does not show

- **Shows:** on real machining data with measured wear, a deviation score is
  materially more informative when history is restricted to the same operating
  context. This is the mechanism the context model is built on.
- **Does not show:**
  - performance with noisy, partly wrong context from a real controller;
  - NC-block-level context;
  - steel settings, which were mostly excluded for lack of history;
  - sudden events;
  - an advantage over time in use for gradual wear.
- **Scope:** one machine, 250 Hz pre-processed signals, 5 scored cases. The wide
  CI reflects that.

## Consequences

- The context-conditioned comparison is supported as the core mechanism. Phase 1
  of `docs/context-model.md` is worth building.
- Two design corrections come out of this run:
  1. compare-to-history needs a small-history-safe score, either relative
     deviation or a floored or pooled scale;
  2. time in use (or part count) is a necessary baseline value in every
     comparison report, so a sensor alarm can be read against tool age.

## Reproduce

```sh
# NASA PCoE "3. Milling.zip" -> mill.zip -> mill.mat
python evaluation/context/c1.py data/milling/extracted/mill.mat OUT
python -m unittest evaluation/context/test_c1.py
```
