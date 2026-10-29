# C5 results: own, like-for-like history on Paderborn run-to-failure bearings

Run once on 2026-10-28, as frozen in `PROTOCOL.md`, with the three logged changes
after freeze. Data: all 48 ZIPs (152.0 GB) verified against the record's MD5s, all 17
experiments matching Table 3's file counts. Output: `data/paderborn-rtf/run/c5.json`
(not committed).

## Verdicts

| Question | Δ stable lead (mean over 14 bearings) | 95% CI | Unstable alarms | Verdict |
|---|---|---|---|---|
| **Q1** H-CTX vs B-RMS (plain RMS) | **−0.047** | [−0.195, 0.050] | 2 vs 0 | **not supported** |
| **Q2** H-CTX vs H-ALL (context rule) | **+0.074** | [0.001, 0.181] | 2 vs 2 | **supported** |

Mean stable lead: H-CTX 0.21, B-RMS 0.26, H-ALL 0.14.

Q1's CI upper bound (0.0495) is just under the 0.05 threshold, so the frozen rule
returns "not supported" rather than "inconclusive". Either way, the product's
own-history score did not warn earlier than a plain RMS alarm. Q2's CI lower bound is
barely above 0: restricting history to the same condition beat using all history, but
narrowly.

## Product check

The H-CTX score through pg_morlet 0.3.1 (`morlet_compare_summary(..., before_only =>
true, min_history => 5)`) matched the harness exactly (relative error 0.0) for 9
segments of B02, B03 and B10, with the same scored and unscored segments.

## Per bearing (channel A, stable lead; u = unstable alarm, lead 0; − = no alarm)

| Bearing | H-CTX | H-ALL | B-RMS | Ends on |
|---|---|---|---|---|
| B02 | 0.27 | 0.27 | 0.27 | vibration |
| B03 | 0.12 | 0.13 | 0.13 | temperature |
| B04 | 0.70 | 0.00u | 0.67 | vibration |
| B06 | 0.24 | 0.24 | 0.01 | vibration |
| B07 | 0.01 | − | 0.00 | temperature |
| B08 | 0.37 | 0.14 | 0.37 | temperature |
| B09 | 0.00u | − | 0.92 | vibration |
| B10 | 0.18 | 0.06 | 0.17 | vibration |
| B11 | 0.69 | 0.69 | 0.69 | vibration |
| B12 | − | − | − | vibration |
| B13 | 0.09 | 0.09 | 0.09 | vibration |
| B14 | 0.00u | 0.00u | 0.01 | vibration |
| B15 | 0.07 | 0.07 | 0.06 | temperature |
| B17 | 0.21 | 0.21 | 0.21 | vibration |
| B01* | 0.74 | 0.73 | 0.73 | none |
| B05* | 0.08 | 0.03 | 0.03 | interrupted |
| B16* | 0.09 | 0.09 | 0.09 | none |

\* not primary.

## What the bearings show

- **In most bearings the three arms alarm within minutes of each other.** Degradation
  on this rig shows up as a rise in vibration level that every indicator sees.
- **Q1 is decided by two bearings.**
  - **B06:** H-CTX alarmed at 76% of life on `kurtosis`, while RMS only crossed its
    threshold at 99% (+0.23).
  - **B09:** RMS rose at 14% of life and stayed above threshold (96% of later
    measurements). H-CTX alarmed at 12% but fell back below its threshold in 76% of
    later measurements, so the alarm counts as unstable (−0.92). The likely mechanism:
    the history includes the elevated measurements, so its median climbs and absorbs a
    persistent rise. A fixed baseline (the RMS alarm's calibration) keeps flagging it.
    This is a property of comparing with all earlier history, and it matters for the
    product.
- **Q2 is decided by three primary bearings.**
  - **B04:** H-ALL's alarm was unstable, +0.70.
  - **B08:** H-ALL alarmed later, at 87% of life against 66% for H-CTX, +0.23.
  - **B10:** +0.12.
  - Elsewhere the two arms are within 0.01.
- **`std` drove most H-CTX alarms.** `kurtosis` drove B06's, and `crest` drove those of
  B11, B13, B14 and B16.

## Reported analyses

| Analysis | Q1 Δ [CI], verdict | Q2 Δ [CI], verdict |
|---|---|---|
| Running time (pauses > 10 min shortened; change 2) | −0.050 [−0.196, 0.045], not supported | +0.074 [0.002, 0.181], supported |
| Temperature-ended (B03, B07, B08, B15) | +0.001 [−0.003, 0.005], not supported | +0.055 [−0.007, 0.167], inconclusive |
| All 17 bearings | −0.036 [−0.160, 0.045], not supported | +0.063 [0.003, 0.156], supported |
| Channel C | −0.028 [−0.115, 0.029], not supported | +0.013 [0.000, 0.038], **not supported** |
| H-CTX without `crest` vs B-RMS | −0.038 [−0.187, 0.080], inconclusive | − |
| H-RMS vs B-RMS | +0.009 [−0.034, 0.054], inconclusive | − |

- **Running time agrees with the frozen verdicts.** B07's 31 h and B14's 99 h pauses
  do not change them.
- **Q2 does not replicate on channel C**, the other accelerometer on the same bearings.
  Treat the Q2 result as weak.
- Without `crest`, H-CTX has 4 unstable alarms instead of 2.

## Conclusion

- On a rig where speed and load change for every recording, comparing a bearing with
  its own earlier recordings **did not warn earlier than a plain RMS alarm** (Q1).
- Comparing within the same condition did better than comparing with all history (Q2),
  but only narrowly, and not on the second sensor.
- C5 therefore does **not** support the claim "compare a unit with its own like-for-like
  history and you see degradation coming earlier" over the simplest baseline. It weakly
  supports the context rule *within* history-based scoring.
- The B09 case points to a concrete product limitation: an all-earlier-history baseline
  absorbs a sustained change.

## Limitations

- 14 bearings from one rig, accelerated life.
- Ten bearings stop on a vibration threshold, which favours RMS-like indicators.
- Stable lead is a proxy; there is no labelled onset.
- Q2's significance rests on three bearings.
- Context bands were fixed, not tuned.
