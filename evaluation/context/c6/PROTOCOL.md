# C6: does resembling another operating condition signal degradation?

Status: **FROZEN** in the commit that adds this line, before any vibration value of the
data set has been computed. Same data as C5 (`../c5/PROTOCOL.md`, "Data"). Read so far:
the description file and paper, and, for the C5 alignment check, the operating-condition
and temperature CSVs and `.mat` variable shapes of B01, B03, B05 and B16 (counts, times,
context sets: B01 16 contexts, B03 8, B16 1). No `simple-v1` value, distance or C5 result
exists yet. Changes after the freeze are logged under "Changes after freeze".

## Why

A reader of the C4 write-up suggested that a waveform resembling a *different* operating
condition might be "a clue worth investigating rather than a match to accept
automatically". The product already treats matches as leads (PR #31), but whether this
particular kind of match carries information has not been tested.

A mechanism makes it plausible. Damage raises vibration, so a damaged bearing at low load
can look like a healthy one at higher load. If so, resembling another condition is an
early symptom, not noise. The Paderborn run-to-failure data suits the test: speed and load
are re-drawn at random for every measurement, so condition is independent of age, and a
"different condition" match cannot simply mean "later in life".

## Questions

- **Q1 (clue).** Do measurements that resemble another condition of the same bearing
  more than their own occur **more often late in life** than early?
- **Q2 (new clue).** Does that hold among measurements that the C5 candidate (H-CTX,
  same-context history score) does **not** flag?

**Gate.** In healthy early life, a measurement must usually resemble its own condition
best; otherwise conditions do not shape the waveform enough to test the idea. A bearing
that fails the gate is excluded from both questions; with too few passing bearings the
answer is "not testable here", not "no".

Each question has its own decision. Neither is combined with C5's or an earlier result.

## Units (fixed now)

As C5: unit = bearing; segment = one measurement, channel A, `simple-v1` values
(pg_morlet 0.3.1); measurement time and context bands (speed, static load, dynamic
load, excitation) exactly as C5 defines them. Values are taken from C5's
`values` output, so both evaluations read the same numbers.

## Resemblance

- **Library:** the bearing's first 150 measurements (C5's calibration window). It is
  fixed for the whole life, so the set of conditions compared and the chance rate do not
  change with age.
- **Distance** from measurement x to a library member y: `morlet_history_neighbors`'
  distance, the root mean square over the default `score_values` (`duration_s`, `std`,
  `crest`, `kurtosis`) of `(x_v − y_v) / |median_v|`, medians over the library, values
  with a zero or negligible median skipped (0.3.1).
- **Eligible condition:** a context with ≥ 5 library members.
- **D(c)** = median distance from x to the library members of eligible context c. The
  median, not the nearest member, so that conditions with more members are not favoured.
- **S(x) = log(D(own) / min over other eligible c of D(c)).** **Flag** = S > 0: x resembles
  another condition more than its own. S is defined when the own context and at least two
  other contexts are eligible (K ≥ 3); otherwise x is unscored.
- Scored measurements: those after the library with a defined S.

## Gate (per bearing)

Leave-one-out over the library: each library member is scored against the other 149
(medians and eligibility recomputed). Chance flag rate for a member = (K − 1) / K. The
bearing **passes** with ≥ 20 scored members and a flag rate ≤ **0.5 × mean chance rate**.

## Decision rules (C5's 14 primary bearings)

- **Late** = the last 25% of post-library time: t ≥ t₁₅₀ + 0.75 × (end of life − t₁₅₀);
  **early** = the rest after the library. End of life = last measurement.
- Per bearing, **enrichment e** = flag rate late − flag rate early, if both sides have
  ≥ 20 measurements in the question's set:
  - Q1: all scored measurements;
  - Q2: scored measurements whose smoothed H-CTX score (C5's causal median of 5) is
    defined and at or below C5's frozen H-CTX alarm threshold for that bearing.
- Bearings used: primary, passing the gate, with e defined. Mean e over them, 95% CI
  from a bootstrap over bearings (2000 resamples, seed 7).
- **Supported** if mean e ≥ **0.10** and the CI lower bound > 0.
- **Not supported** if the CI upper bound < 0.10.
- **Inconclusive** otherwise.
- **Not testable here** if fewer than **5** bearings are used.

Comparing late with early *within* a bearing removes the bearing's chance rate, which is
fixed by its library.

## Reported, not decided on

- Per bearing: gate flag rate and chance, scored count, early and late flag rates for
  Q1 and Q2, H-CTX threshold.
- **Direction:** for flagged measurements (late and early separately), the share whose
  resembled condition has a higher or lower load band (static + dynamic) or speed band
  than its own. "Damage looks like heavier load or speed" predicts mostly higher.
- **Wrong-condition check (synthetic):** on 10% of each bearing's early scored
  measurements (seed 7), the recorded condition is replaced by another eligible one and
  the flag rate is compared with the true one. This shows whether S could catch a wrong
  condition record; it is not a claim about errors in this data.
- Channel C; all 17 bearings.
- **Product check:** for B03 and B10 (or, if one has no scored measurement, the next
  primary bearing of the same sampling rate), three scored measurements each (first,
  middle, last). Each is loaded with the 150 library measurements into a fresh collection
  as C5 loads them, with an extra context key `bearing` so that
  `morlet_history_neighbors(..., match_keys => ARRAY['bearing'], before_only => true,
  top_k => 1000)` returns all 150 library members across conditions. The returned
  distances must match the formula to 1e-9 relative, and S computed from them (with the
  library contexts) must equal the harness's S to 1e-9. A mismatch stops the evaluation
  until fixed and logged.

## Controls and limitations

- **Not a detector comparison.** C6 asks whether the flag is informative, not whether it
  beats H-CTX; Q2 asks whether it adds anything where H-CTX is silent.
- **Late life is a proxy for degradation.** There are no per-measurement fault labels.
- **Stop rule.** Most primary bearings stop on a vibration threshold, so late life is,
  by construction, high vibration. That favours Q1 (high vibration resembles heavier
  load). Q2 and the direction analysis show how much of Q1 is just "vibration went up".
- **Run-in and thermal drift** also move measurements away from the library; a uniform
  rise in level can make a higher condition the nearest. That is the mechanism under
  test, not a confound to remove, but it means a flag says "unusual for this condition",
  not "damage".
- **Bearings with few conditions** (B14–B16 hold speed and static load constant) are
  expected to have K < 3 and be unscored; this follows from the rule, not from a choice.
- **Context bands are C5's,** not tuned. One rig, accelerated life, 61806 bearings.

## Harness

`c6.py run C5_OUT` (after `c5.py values`), synthetic checks in `test_c6.py`,
product check in `sql_check.py`. Development used synthetic data only. Before the freeze,
the synthetic product check (three queries against a 150-member library on pg_morlet
0.3.1) matched with relative error 0.0. Writing it showed that
`morlet_history_neighbors` matches on all context keys when `match_keys` is empty, so a
cross-condition search needs a unit key (here `bearing`), as `docs/context-workflow.md`
describes for machines.

## Changes after freeze

1. **Pauses: running time reported** (2026-10-28, before any resemblance or result was
   computed; C5 change 2). B07 pauses for 31.2 h at about 47% of its measurements, B01
   for 0.3 h; Table 3's durations count running time. The late/early split stays on
   measurement timestamps as frozen. Both decisions are also **reported** with running
   time (gaps over 10 minutes shortened to the bearing's median interval). If the two
   disagree, the frozen verdict stands and the disagreement is stated.
