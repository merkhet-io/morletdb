# C6 results: does resembling another operating condition signal degradation?

Run once on 2026-10-28, as frozen in `PROTOCOL.md`, with the two logged changes after
freeze. Same values as C5 (`data/paderborn-rtf/run/values`); output
`data/paderborn-rtf/run/c6/c6.json` (not committed).

## Verdicts

| Question | Bearings used | Mean enrichment (late − early flag rate) | 95% CI | Verdict |
|---|---|---|---|---|
| **Q1** clue | 5 (B04, B06, B08, B10, B11) | **+0.120** | [0.039, 0.193] | **supported** |
| **Q2** new clue (where H-CTX is quiet) | 2 | − | − | **not testable here** |

**Read Q1 with its caveats (below).** It passes the frozen rule exactly at the minimum
number of bearings. The gate excluded 9 of 14 primary bearings. On the second sensor the
same question is only inconclusive.

## Product check

S computed from `morlet_history_neighbors(..., match_keys => ARRAY['bearing'],
before_only => true, top_k => 1000)` distances matched the harness exactly (relative
error 0.0) for 6 measurements of B03 and B10, each against all 150 library members.

## Gate: do recordings resemble their own condition in healthy life?

| Bearing | Conditions in library | Healthy flag rate | Chance | Gate |
|---|---|---|---|---|
| B02 | 9 | 0.50 | 0.86 | fail |
| B03 | 8 | 0.82 | 0.83 | fail |
| B04 | 16 | 0.35 | 0.92 | pass |
| B06 | 16 | 0.42 | 0.92 | pass |
| B07 | 10 | 0.49 | 0.90 | fail |
| B08 | 7 | 0.37 | 0.86 | pass |
| B09 | 16 | 0.50 | 0.90 | fail |
| B10 | 18 | 0.41 | 0.91 | pass |
| B11 | 3 | 0.26 | 0.67 | pass |
| B12 | 3 | 0.33 | 0.67 | fail |
| B13 | 3 | 0.36 | 0.67 | fail |
| B14, B15, B17 | 2 | − | − | unscored (< 3 conditions) |

- **The condition does shape the summary values on every scored bearing.** Healthy
  flag rates are below chance everywhere, except B03.
- **The effect is weaker than the frozen bar on most bearings.** Several sit just
  above half of chance (B02, B07, B09 at 0.54–0.58 × chance), so the gate's outcome
  is close for them.

## Q1 per bearing (channel A)

| Bearing | Early flag rate | Late flag rate | Enrichment |
|---|---|---|---|
| B04 | 0.85 | 0.90 | +0.06 |
| B06 | 0.64 | 0.63 | −0.02 |
| B08 | 0.57 | 0.75 | +0.19 |
| B10 | 0.76 | 0.91 | +0.15 |
| B11 | 0.62 | 0.84 | +0.22 |

Gate-failed bearings, reported for completeness:
- B02: −0.13
- B03: −0.02
- B07: +0.02
- B09: +0.03
- B12: +0.15
- B13: +0.17

## What it shows

- **Late in life, recordings resemble other conditions more often.** On 4 of the 5
  usable bearings the rise is 0.06–0.22.
- **The direction fits the mechanism.** Late flagged recordings mostly resemble a
  **higher** load or speed band than their own:
  - B04: 74% higher load and 75% higher speed;
  - B06: 67% higher load;
  - B10: 70% higher speed;
  - B11: 100% higher speed.

  Early flags in B06 and B10 mostly pointed to *lower* load. "Damage looks like a
  heavier condition" is what the data show.
- **As a clue on its own, though, the flag fires far too often.** Even before the
  last quarter of life, 57–85% of recordings on the usable bearings resemble another
  condition more than their own. In the healthy library it was 26–42%. The library
  goes out of date within hours (run-in, warming), so most recordings soon look
  "unusual for their condition". A flag that is on most of the time is not something
  an engineer can investigate one by one.
- **Q2 could not be tested.** Once H-CTX's quiet recordings are kept, too few late
  recordings remain: H-CTX alarms and stays above threshold for most of late life.
  So C6 cannot say whether the clue adds anything to the existing score.
- **The wrong-condition check was weak.** Recordings deliberately given another
  condition were flagged at 0.69–0.97, against 0.37–1.00 with the true condition. The flag's high baseline leaves little room to catch label errors.

## Reported analyses

| Analysis | Q1 | Q2 |
|---|---|---|
| Running time (change 1) | +0.120 [0.039, 0.193], supported (same) | not testable |
| All 17 bearings | +0.120 [0.039, 0.193], supported (same 5 pass) | not testable |
| Channel C | +0.081 [−0.044, 0.195], **inconclusive** (6 bearings pass, a different set) | not testable |

On channel C, a different set of bearings passes the gate (B02, B04, B06, B07, B11,
B13), and B02 goes the other way (−0.21).

## Conclusion

- The reader's idea holds directionally on this rig. A bearing's recordings resemble
  heavier operating conditions more often as it nears failure, which is the "damage
  looks like more load" mechanism.
- But the evidence is thin:
  - five bearings, exactly the frozen minimum;
  - the gate excluded most bearings;
  - the second sensor gives only an inconclusive result;
  - whether it adds anything to same-condition scoring could not be tested.
- In this form, measured against an early healthy library, the flag is on for most
  recordings well before failure. So it is **not yet a usable "investigate this"
  signal**.
- A useful version would need a baseline that stays representative, for example a
  rolling or condition-wise refreshed library. That is a new question, to be frozen
  before being tested, not a tweak to this one.

## Limitations

- One rig, accelerated life, 61806 bearings.
- Late life is a proxy for degradation.
- Ten bearings stop on a vibration threshold, which favours Q1.
- Context bands are C5's.
- The 0.5 × chance gate was fixed before the data, and several bearings fall just
  short of it.
