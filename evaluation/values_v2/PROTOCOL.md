# V2: a zero rule for quantised values (`simple-v2`, #387)

Status: **FROZEN** in the commit that adds this file (2026-11-12), before any comparison under the new rule was
computed on any dataset. The rule, the factor k and the criteria below are fixed here.

## Why

A history median that is a few converter steps from zero gives meaningless relative deviations. #387 reports p75
on a vibration channel: history median −0.0383 (one converter step), segment 0.0, relative deviation ±1.0, which
dominates every score that includes it. `simple-v1` treats a median as zero only at `1e-9 × median rms`, which
catches rounding residue but not a real step. `sample_quantum` cannot help: it defaults to 1 and is rarely
declared for calibrated data.

## The rule (fixed now)

For one value (p75, std, ...) and one history (the segments a comparison uses), with the history's non-NULL
values of that value:

1. **Step.** Let `u` be the distinct values, sorted, and `g` the positive differences between neighbours in `u`.
   With at least 3 distinct values, `step = min(g)`. Otherwise there is no step.
2. **Quantised.** The history is quantised for this value if every `g` is an integer multiple of `step` within a
   relative tolerance of 1e-6 (`|g/step − round(g/step)| ≤ 1e-6 · g/step`) and no multiple exceeds 10^6.
   Otherwise there is no step: continuous values (std, rms, kurtosis of real signals) are left as in v1.
3. **Floor.** `floor_v2 = max(floor_v1, k · step)` with **k = 2**, where `floor_v1` is v1's floor (zero for
   dimensionless values, `1e-9 × median rms` for values in channel units). A history median with
   `|median| ≤ floor_v2` is treated as zero: status `zero_median`, no relative deviation, no contribution to
   scores or neighbour distances, exactly as v1 treats an exact zero.

The stored values do not change; only comparisons do. A collection carries its rule version: `simple-v2` for
collections whose context is enabled from the release that ships it on, `simple-v1` for existing ones until
switched explicitly. Every result names the version it used.

## Criteria

- **S1 (the artefact).** On a synthetic quantised channel whose p75 history alternates between 0 and −1 step,
  v1 gives a relative deviation of ±1.0 for a segment at 0, and v2 gives `zero_median`. A unit test in
  `morlet-core` and a SQL test.
- **S2 (nothing else moves).** On a synthetic continuous channel (Gaussian noise, float values), v2's results
  equal v1's bit for bit.
- **S3 (earlier evidence holds).** Re-running the summary score of C2 (Bosch CNC, AUROC 0.81 same machine and
  operation) and C3 (MillingVibes, C2 score 0.884) under v2: **each AUROC within 0.01 of its v1 value, or
  higher.** Also reported, without criterion: J1's conditioned deviation (NASA milling) and IU3's c1 development
  states (PHM 2010), and per dataset and value, the share of history medians that v2 treats as zero and v1 does
  not.
- **V2 passes if S1, S2 and S3 hold.** If S3 fails, v2 is not made the default; the finding is reported.

## Not claimed

V2 does not show that v2 detects anything better. It removes one artefact and checks that earlier results do not
get worse. The closed series-production data of #387 are not available here; S1 stands in for them.
