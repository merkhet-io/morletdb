# V2 results: the zero rule for quantised values (`simple-v2`, #387)

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen in f90ba6d before any comparison under the new rule was computed.
Run on 2026-11-12. **V2 passes: S1, S2 and S3 hold.**

| Criterion | Result | |
|---|---|---|
| **S1** the artefact | p75 history of 0, −1 and −2 steps (0.0383), segment at 0: under `simple-v1` relative deviation exactly 1; under `simple-v2` status `zero_median`, no deviation | holds (`morlet-core` test `quantised_p75_near_zero_is_zero_under_v2`; SQL test `value_rules_v2_treat_a_quantised_near_zero_median_as_zero`, PostgreSQL 17) |
| **S2** nothing else moves | continuous values have no step (`continuous_values_have_no_step`); in the SQL test, std, rms, kurtosis and crest compare identically under both rules | holds |
| **S3** earlier evidence | C2 (Bosch CNC) AUROC same machine and operation 0.8101 under both rules, context-free 0.6370 under both; C3 (MillingVibes) C2 score 0.8844, calibrated 0.8449 and 0.8532 under both. Differences 0 | holds (`s3.py`, `results/s3.json`) |

**How often v2 treats a median as zero** (reported without criterion): C2, 0 of 37,670 history medians; C3, 0 of
24. Their values (duration, std, kurtosis, crest) are not quantised near zero, so the rule does not apply to them,
which is what S3 asks.

**Not run** (named in the protocol as reported without criterion): J1's conditioned deviation (NASA milling) and
IU3's c1 development states (PHM 2010). Both need a database load. The share of zero medians there remains open.

**Reading.** simple-v2 removes the #387 artefact and changes nothing that C2 and C3 measured. It shows no
detection gain; none was claimed.

Environment: `.venv-compress` (numpy, scipy, h5py) on the M-series Mac for S3; pgrx 0.19.3 with PostgreSQL 17.11
for the SQL test.
