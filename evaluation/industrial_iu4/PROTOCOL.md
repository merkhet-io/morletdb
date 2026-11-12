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
- **H4 retrieval of precedents (#416):** for C's positive decisions on M2/M3, the share whose 3 cited precedents
  include a positive (VB ≥ 100) run: abnormality fingerprint against equal-weight value distance; difference
  interval above 0. Shape search, tiered ranking and abnormality weighting reported without criterion.
- **Reported without criterion:** Spearman of C's score and of A with VB per tool; context sensitivity of the
  neighbours (top-k overlap for match keys machine; machine + tool; #419).

## Not claimed

Diagnosis of anything but flank wear; other materials, tools or processes; production without a dynamometer is
covered only by the controller-only arm.
