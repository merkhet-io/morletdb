# IU4: does a decision model on MorletDB transfer across tools and machines? (DRAFT, not frozen)

Status: **DRAFT.** Known when this was written (2026-11-12): the Data in Brief description, the dataset's file list
(`filelist.csv`: machine, tool, run, cumulated contact time, wear per file) and the structure of one HDF5 file
(dataset names, shapes, units). **No signal value has been read.** Freezing happens in the commit that changes
this line, before the first signal value is read.

## Why

Detection of tool wear from stored machining signals should not depend on hand-picking a measure on the very tool
or machine it is used on. Earlier development work here suggested that a fast decision model on MorletDB's
per-segment values transfers better across tools than a single measure chosen elsewhere, but it could not decide
this on its data. IU4 asks it on unseen data with three machines.

## Data

Stiehl, Denkena et al., *Multivariate time series data of milling processes with varying tool wear and machine
tools*, Data in Brief 2023; Mendeley Data doi:10.17632/zpxs87bjt8.3, CC BY 4.0. 6,418 runs (files) of 9 solid
carbide end mills (4 edges, TiN-TiAlN), three per machine on three five-axis milling centres (M1: T1–T3, M2:
T4–T6, M3: T7–T9), each from new (VB about 3 µm; T8 from 34 µm) to end of life (VB about 150 µm), 560–928 runs per
tool. Per run: process forces x, y, z (dynamometer, 25 kHz, 110,250 samples) and controller signals at 500 Hz
(spindle torque, axis torques x, y, z, position control deviation x, y, tool position x, y, z). Labels: flank wear
VB (µm, microscope) and cumulated contact time.

## Units, labels, splits

- **Unit:** one run. Each signal of a run is one recording; a run's recordings share the context
  (machine, tool, run).
- **Positive:** a run with **VB ≥ 100 µm** (two thirds of the life criterion of 150 µm), 18–33% of a tool's runs.
- **Development:** machine M1 (T1–T3). **Confirmation:** M2 and M3 (T4–T9), never read before the build record.
- **Causality:** a decision on run r of a tool uses only runs before r of that tool and complete, labelled lives
  of other tools (their labels exist only once their life ended).

## Arms (signals: controller only, and controller + forces; each arm reported for both)

- **A** best single measure: the per-run value and signal with the highest development AUROC (chosen on M1).
- **B** shape search alone: nearest-neighbour envelope distance to the tool's own first 10 runs.
- **C** fast decision model: logistic regression on MorletDB features (decision_state per signal: deviations
  from the same-tool history and from the tool's own first 10 runs), trained on M1, calibrated per tool by rank
  (the score is the run's quantile among the tool's earlier runs), so probabilities are on one scale.

## Hypotheses

- **H1 transfer:** on M2 and M3, mean per-tool AUROC(C) − AUROC(A) ≥ 0.03, 95% interval (bootstrap over tools,
  then runs) above 0. **Ceiling rule (fixed now):** if A's confirmation AUROC is ≥ 0.98 on 5 or more of the 6
  tools, H1 is reported as *undecidable on these data*, and H1b decides instead.
- **H1b threshold transfer:** with one threshold per arm fixed on M1, on M2 and M3: C flags every tool before VB
  150 µm and flags fewer runs below VB 50 µm than A (false alarms), counted per tool.
- **H2 latency:** on a Raspberry Pi 3B+ under X2's `e512` limits, `decision_states` for a run's signals plus the
  prediction take p95 ≤ 250 ms (100 timed runs of M2/M3 late in life). Building segments and values at the end
  of a run is reported beside it, without criterion (#414).
- **H3 evidence:** every C decision on M2/M3 cites at least one labelled precedent from a completed tool
  (decision_state examples across tools, #404, #418); 100% required.
- **Reported without criterion:** the retrieval of precedents (#416): for C's positive decisions on M2/M3, the
  share whose 3 cited precedents include a positive run, for equal-weight value distance and shape search (the
  fingerprint, tiered and abnormality-weighted rankings are not implemented yet and get their own frozen test).
  Also: Spearman of C's score and of A with VB per tool; context sensitivity of the
  neighbours (top-k overlap for match keys machine; machine + tool; #419).

## Not claimed

Diagnosis of anything but flank wear; other materials, tools or processes; production without a dynamometer is
covered only by the controller-only arm.

## Implementation (fixed at the freeze)

**Signals (9 per run).** Forces x, y, z (25 kHz); spindle torque; axis torques x, y, z; position control deviation
x, y (500 Hz). Tool positions are the trajectory, not the process, and are not used. The **controller-only** arm uses
the 6 controller signals; the **full** arm all 9. Rates are the nominal 25 kHz and 500 Hz; each run's time vectors
are checked at load and their largest departure from the nominal axis is reported (#393), not corrected.

**Storage.** One recording per run and signal, float32 values widened exactly to float64, in a released-or-main
MorletDB build recorded at the run. Context per recording: `process` = `denkena2023`, `machine`, `tool`, `run`;
one segment per run (the whole run), keys `process`, `machine`, `tool`, occurrence `run`. Labels after a tool's
life: `worn` for VB ≥ 100 µm, `sharp` otherwise, written when the tool's last run is stored.

**Per-run features (C).** For every signal: `decision_states` against the same tool's earlier runs
(`match_keys` = process, machine, tool; `min_history` 3), with the tool's first 10 runs as own baseline. Features
per signal and value (std, rms, p75, peak_abs, crest, kurtosis): the own-baseline relative deviation and the
history quantile; plus the summary score per signal. Missing values (too short a history) are 0. Model: logistic
regression, L2, C = 1, on standardised features (mean and spread from M1), trained on all M1 runs after each
tool's first 10. Runs 1–10 of every tool are baseline and never scored.

**A.** Every (signal, value) pair as its raw value and as its own-baseline relative deviation; the one with the
highest M1 AUROC (pooled over T1–T3, direction chosen on M1) is A. Ties go to the earlier pair in the order above.

**B.** Envelope distance with the product's bounded lag: per run, the median over its windows of the nearest
distance to windows of the tool's first 10 runs, same signal; windows 4096 / stride 2048 for forces, 512 / 256
(the product minimum) for controller signals; the run score is the maximum over signals.

**Measures.** Per-tool AUROC on runs 11 to end, positives VB ≥ 100 µm. Intervals: two-level bootstrap (tools, then
runs within tools), 10,000 resamples, seed 20261112; the unit-only bootstrap is reported too. H1b thresholds: each
arm's score as its causal quantile among the same tool's earlier scored runs; one threshold per arm chosen on M1
(Youden); a *flag* is a run at or above it; a *false alarm* a flag at VB < 50 µm.

**Latency (H2).** Pi 3B+ (X2's box) under `e512` (`--cpus 2 --memory 512m --memory-swap 512m`,
`shared_buffers=64MB`, `max_connections=20`, `maintenance_work_mem=32MB`), loaded with M2 and M3 (labelled as in
causal order at the end) plus M1 (labelled); timed: `segments_at` + `decision_states` for the 9 signals + the
prediction, for the last 50 runs of T4 and of T7, after 10 warm-up runs; gate: 5-minute load below 1. The end-of-run
build (context, segments, values) is timed beside it.

**Evidence (H3).** Each C decision: `decision_states(..., examples=3, example_match_keys=['process'])` on the signal
contributing most to the logit; passes when it cites at least one `worn` or `sharp` precedent from another tool.
