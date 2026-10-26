# C2: does same-operation history flag anomalous CNC machining runs?

**Result: SUPPORTED.** AUROC 0.81 (same machine + operation) vs 0.64 (context-free),
Δ +0.17 [+0.07, +0.28]. See [results/RESULTS.md](results/RESULTS.md).

Status: **FROZEN** in the commit that adds this line, before the data was downloaded.
Only the README and the repository's file listing (`file-listing.txt`: paths and
byte sizes) have been read. No vibration value has been seen.

## Why

C1 (NASA milling, lab data, gradual wear) supported context-conditioned
comparison. It left two questions open:
- does it work on **real production data**?
- does it detect **sudden anomalies**, where counting cutting time cannot help?

The Bosch CNC Machining dataset has both: industrial machines, a natural context
(the operation), and labelled anomalous runs.

## Data

- **Bosch CNC Machining** (Tnani, Feil & Diepold, Procedia CIRP 107, 2022,
  doi:10.1016/j.procir.2022.04.022):
  - `github.com/boschresearch/CNC_Machining`, archived read-only on 2026-02-19;
  - data licence CC BY 4.0, code BSD-3-Clause.
- **Content:**
  - three brownfield CNC milling machines (M01–M03), 15 operations (OP00–OP14);
  - tri-axial accelerometer (Bosch CISS) inside the machine, 2 kHz;
  - one HDF5 file per execution of an operation, an array of (samples, 3)
    holding X, Y, Z.
- **Labels:** the folder `good` (normal) or `bad` (anomalous), assigned by the
  dataset authors.
- **Listing:**
  - 1,702 files, 952 MB, of which 70 are bad;
  - six periods: Feb 2019, Aug 2019, Feb 2020, Aug 2020, Feb 2021, Aug 2021;
  - bad files: 39 / 14 / 0 / 0 / 8 / 9 by period.
- **Integrity:** the commit hash and every file's SHA-256 are recorded at download.
  Each file must have 3 columns and finite values; violations are reported and
  excluded.

## Split (time-ordered, fixed now)

- **History:** the *good* files from Feb 2019 to Aug 2020. Known-bad history is
  excluded, as `exclude_labels` does in the product.
- **Evaluation:** every file from Feb 2021 and Aug 2021, good (452) and bad (17).
  Their labels are used only for scoring.
- **Rationale:** this mirrors deployment, where earlier normal runs judge later
  runs. It also includes the rig's real drift over about 1.5 years.

## Values per file (fixed)

The `simple-v1` definitions of pg_morlet, per axis (X, Y, Z):
- `std`;
- `kurtosis`;
- `crest` (peak |x| / rms including DC).

Plus `duration_s` once: 10 values in all.
- **Why these:** all are strictly positive, so relative deviation is defined.
- **Excluded:** the mean and the 75th percentile. They sit near zero on axes
  without gravity offset, where relative deviation is unstable. This is decided
  before any value was seen.

## Compare-to-history (as `morlet_compare_to_history`)

- **Deviation:** relative deviation per value, |x − median_hist| / |median_hist|.
- **Score:** the maximum over the 10 values.
- **Arms:**
  - **machine+operation** (primary conditioned): history files with the same
    machine and operation;
  - **operation** (reported): the same operation, any machine;
  - **context-free** (primary baseline): all history files.
- **Minimum history:** 3 files. An evaluation file whose machine+operation
  history has fewer than 3 files is excluded from the primary comparison and
  counted; it is scored in the other arms for reporting.

## Primary measurement

- **AUROC** of the score for bad vs good, over the evaluation files scored in both
  primary arms.
- **ΔAUROC** = AUROC(machine+operation) − AUROC(context-free), on the same files.
- **CI:** cluster bootstrap over (machine, operation) groups of the evaluation
  files, 2000 resamples, seed 7. Each resample draws groups with replacement and
  recomputes both AUROCs. Resamples without both classes are dropped and counted.

## Decision rules

- **Supported:** ΔAUROC ≥ 0.05 and its CI lower bound is > 0.
- **Not supported:** the CI upper bound of ΔAUROC is < 0.05.
- **Inconclusive:** anything else. With 17 bad files the CI may be wide; report
  and stop.
- **Usefulness, reported and not part of the decision:**
  - the conditioned arm's absolute AUROC;
  - its true positives at the false-alarm rate of a threshold set on history.
    The threshold is the 99th percentile of history files' leave-one-out scores
    within their own context.

## Reported, not decided on

- The **operation** arm, i.e. history transfer across machines.
- **Per machine and per period** AUROC (descriptive).
- **Per value:** which axes and values carry the anomalies.
- **History size** per context, and excluded files.

## Controls and limitations

- **Identical across arms:** files, values and score. The arms differ only in
  which history counts as the same context.
- **Labels are the authors'.** What "anomalous" means operationally is not
  specified in the README. Some bad files may be detectable by duration alone.
  Per-value results show that.
- **Not tested:**
  - NC-block context (operations are whole files);
  - the multi-axis time base, since each file is already synchronized;
  - one site and one sensor type.
- **No tuning:** values, score, split and thresholds are fixed. There is no
  development step on evaluation files. The harness is checked on synthetic data
  only.

## Changes after freeze

1. **Serialization fix in `c2.py`,** after the first execution. That execution
   computed the results but could not write a NumPy scalar to JSON. The identical
   computation was run again. No change to data, values, scores or rules.
