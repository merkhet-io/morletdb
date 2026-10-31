# X1: lossless reference coding across repeated machining operations

Status: **feasibility gate FROZEN** in the commit that adds this file. No Bosch statistic
of the kind below has been computed yet. Only the file listing and the HDF5 dtypes and
value grid were read: one execution per file, float64 holding integer counts.
Development data only; the confirmation split stays untouched unless the gate passes.

## Question

Can operating context find earlier executions of the same operation that make a later
execution materially cheaper to store, as a reference plus a lossless residual, than
MorletDB's current codec alone?

This is different from the shaft-synchronous template experiment in the compression
study, which exploited periodicity **within** one recording. X1 targets redundancy
**between separate executions** of the same operation.

## Data

**Bosch CNC Machining** (`data/bosch/repo`; Tnani, Feil & Diepold, Procedia CIRP 107, 2022;
CC BY 4.0):
- 1,702 HDF5 files, each one execution of one operation;
- three machines (M01–M03) and 15 operations (OP00–OP14);
- 2 kHz tri-axial vibration, float64 arrays whose values are all integer counts;
- `good` or `bad` folders.

**Prior use:** evaluation C2 used it for anomaly scoring, not for compression.

**Context reliability:**
- **Machine and operation identity** are reliable.
- **Time order is known only between periods:** Feb 2019, Aug 2019, Feb 2020, Aug 2020,
  Feb 2021 and Aug 2021, each covering six months.
- **The file index within a period** is an "example no." (dataset README), not a time
  order.
- **No NC-block labels, timestamps or tool identities exist.** None are inferred.

## Split (fixed now)

- **Development:** Feb and Aug 2019 (862 files).
- **Confirmation:** 2020–2021 (840 files), used only if the gate passes and a full
  protocol is frozen after it.

## Feasibility gate (computed on development data only)

**Eligible executions:** every Aug 2019 execution. Its candidate references are the Feb
2019 executions of the same machine and operation, good or bad, since only earlier
periods are reliably earlier.
- Aug 2019 executions with no candidate stay in the denominator with zero saving.
- Feb 2019 executions, which have no earlier period, also stay in the denominator with
  zero saving.

**Per execution, per axis, against each candidate:**
- **Overlap:** the first min(len_x, len_r) samples. No resampling or stretching.
- **Correlation on raw samples:** ρ at shift 0, and the best integer shift within ±100
  samples (50 ms).
- **Correlation on predictor residuals:** the same, on the order-k difference residuals,
  with k the fixed-predictor order the codec would pick for x (0–4, minimum absolute
  residual).
- **Gaussian bound:** ½·log₂(1/(1 − ρ²)) bits per sample, using the best predictor-residual
  correlation.
- **Coded saving:** the integer residual d = x − round(a·r_s), with a fitted by least
  squares and s the best shift. Saving = cost(x) − cost(d), where cost is the codec's
  integer cost model:
  - per 4096-sample chunk, the best fixed-predictor order 0–4;
  - Rice coding in 16 partitions of 256 with the best parameter each (6 bits per
    partition);
  - 32 bits of header per chunk.
- **Reference choice:** the candidate with the largest coded saving. This is an oracle
  choice, so the result is an upper bound.

**What the bound ignores**, all of which only makes the real saving smaller:
- storing the reference;
- side information (shift, a, reference id);
- the overlap only partly covering x;
- read amplification;
- the uncovered tail, which is coded independently at cost(x).

**Model check:** for a random subset of 60 eligible executions (seed 7), cost(x) and
cost(d) are compared with the real codec's sizes (`morlet_encode_chunk`, quantum 1). The
model is used only if its savings agree with the real codec's to within 10% of cost(x).

**Gate:** aggregate upper-bound saving = Σ max(0, cost(x) − cost(d)) / Σ cost(x), over
**all** 862 development executions and all axes.
- **Below 25%: PAUSE.** Report the distributions and the cause, and build no codec.
- **25% or above:** write the full experiment protocol (reference rules, alignment,
  formats, PostgreSQL measurements, confirmation) before touching 2020–2021.

**Reported descriptively:**
- distributions of ρ (raw and residual, at shift 0 and best shift);
- the implied bound in bits, against cost(x)/n;
- the share of executions with an upper-bound saving of at least 25%;
- the same within Aug 2019 using the file index as an assumed order. This uses the
  nearest lower index of the same machine and operation, is labelled as unverified order,
  and does not decide anything.

## Not in scope

DTW, resampling, learned predictors, broad parameter searches, seismic data, and any
schema or production change.
