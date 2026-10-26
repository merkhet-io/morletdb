# Context workflow: compare a cut with the same cut in the past

Phase 1 of the [context model](context-model.md), implemented in pg_morlet 0.2.0. It
answers "is this segment different from the same context in earlier occurrences?"
for single-channel recordings.

Evidence: in evaluation C1 (NASA milling, [results](../evaluation/context/results/RESULTS.md)),
deviation from same-setting history tracked flank wear far better than deviation
from all history: Spearman ρ 0.91 against 0.60, Δ +0.31 [+0.08, +0.54].

## Steps

```sql
SELECT morlet_create_stream_collection('public', 'spindle');
SELECT morlet_enable_context('spindle');      -- spindle_ctx, _ctx_segments, _ctx_values, _labels

-- 1. Raw samples, as usual (feature extraction is optional).
SELECT morlet_start_recording('spindle', 'shift-0412', 25600, 'vib_x', 'g', '{"machine":"M7"}',
                              512, 256, 'zscore', 'none');
SELECT morlet_append_chunk('spindle', 'shift-0412', 0, :samples);

-- 2. Context from the controller, in sample coordinates of the recording.
SELECT morlet_add_context('spindle', 'ctl-0412-001', '[
  {"recording_id":"shift-0412","key":"program","value":"O1234","start_sample":0,"end_sample":2304000},
  {"recording_id":"shift-0412","key":"block","value":"N120","start_sample":25600,"end_sample":128000},
  {"recording_id":"shift-0412","key":"tool","value":7,"start_sample":0,"end_sample":2304000},
  {"recording_id":"shift-0412","key":"part","value":"SN-0042","start_sample":0,"end_sample":2304000},
  {"recording_id":"shift-0412","key":"tool_change","value":7,"start_sample":0}
]');

-- 3. One segment per maximal intersection of the context keys; part as occurrence.
SELECT morlet_build_context_segments('spindle', 'shift-0412',
                                     ARRAY['program','block','tool'], ARRAY['part']);
SELECT morlet_build_context_values('spindle', 'shift-0412');

-- 4. Compare one segment with the same context on other parts.
SELECT * FROM morlet_compare_to_history('spindle', :segment_id);
SELECT max(abs(relative_deviation)) AS score
FROM morlet_compare_to_history('spindle', :segment_id, exclude_labels => ARRAY['air_cut','crash']);

-- One row per comparison: evaluated score, its per-context quantile, top value, duration.
SELECT * FROM morlet_compare_summary('spindle', :segment_id, exclude_labels => ARRAY['air_cut']);

-- 5. Labels, used as data: exclude known-bad history, build evaluation sets.
SELECT morlet_add_label('spindle', 'shift-0412', 25600, 128000, 'air_cut', 'tool above stock');
```

## Semantics

- **Context rows:**
  - intervals `[start_sample, end_sample)` or events (`start_sample` only) with a
    key and a JSON value;
  - for one key in one recording, active intervals must not overlap; different keys
    nest freely;
  - `batch_id` makes ingest idempotent: an identical retry returns 0, and a
    different batch under the same id is an error.
- **Corrections** are appended:
  - `morlet_revise_context(collection, ctx_id, '{"end_sample": ...}')`, or
    `retract => true`;
  - segments built from the old row become `stale`;
  - `morlet_build_context_segments` replaces stale segments of that recording
    and spec.
- **Segments** come from an explicit, versioned spec (`ctx-seg-v1`: keys,
  occurrence keys, minimum length).
  - Only intervals fully inside committed samples become segments.
  - The occurrence holds the values of occurrence-key intervals that cover the
    whole segment, and is `{}` otherwise.
- **`simple-v1` values:**
  - duration_s, mean, std, rms, p75, peak_abs, crest and kurtosis
    (`morlet_simple_value_names()`);
  - segments up to 1,048,576 samples.
- **History in compare-to-history:**
  - other non-stale segments whose context contains the query's values for
    `match_keys` (all of its keys by default);
  - on recordings with the same rate, channel and unit, and metadata matching
    `metadata_filter`;
  - not the same occurrence, and not overlapping an active label in
    `exclude_labels`;
  - more than `max_candidates` (default 10,000) is an error.
- **Per value** the function returns:
  - `hist_n`, the history median and a quantile;
  - `relative_deviation` = (x − median)/|median|, the **recommended score**;
  - `robust_z` = (x − median)/(1.4826 · MAD);
  - with fewer than `min_history` (default 5) history segments, the numbers are
    NULL and `status` is `insufficient_history`. Other statuses are described
    under "Calibrated comparison" below.
- **Compare without context:** add a key every segment shares (for example
  `machine`) and pass `match_keys => ARRAY['machine']`.

## Summary and calibration (phase 2)

**`morlet_compare_summary` returns one row per comparison:**
- **`score`:** the maximum relative deviation over `duration_s`, `std`, `crest` and
  `kurtosis`, with `top_value` naming the value behind it. This is the evaluated
  score: AUROC 0.81 on the Bosch CNC anomalies (C2) and 0.88 on the MillingVibes
  surface-quality labels (C3).
- **`score_quantile`:** the score's rank among leave-one-out scores of the same
  history, so 0.99 means "more unusual than 99% of normal history segments are in
  their own context".
  - This is a per-context calibration of the alarm. It is NULL above 2000 history
    segments.
  - It has not been evaluated with several contexts yet.
- **`duration_relative_deviation`,** separately, because duration was the
  strongest single value in C2.
- **`calibrated_score`:** experimental, see below.

**Calibration by spread (`calibrated-v1`) is experimental, and ranked worse in
C3.**
- **How it works:**
  - `morlet_value_spreads` gives each value's typical relative spread: the median
    over contexts, floored at 1%;
  - `morlet_compare_to_history` reports `spread` and
    `calibrated_deviation = |relative_deviation| / spread`;
  - `calibrated_score` is their maximum over all values.
- **Why it is not the default:**
  - In C2's M03 case it would have suppressed a drifting noisy value.
  - In C3 it up-weighted low-spread but uninformative values (crest, kurtosis,
    mean) and lost AUROC (0.845 against 0.884).
  - A spread says how noisy a value is, not whether it matters. Weighting values
    by relevance needs labelled history per context.
- **Status semantics:** `status` reports whether the calibrated deviation could be
  computed (`insufficient_history`, `zero_median`, `no_pooled_spread`, `ok`).
  `robust_z` is NULL when the history's MAD is 0.
- **Cost:** spreads are computed per call over all comparable segments. On the
  milling check this doubled compare latency (p50 23 ms → 46 ms).

**Read per-value deviations first.** C2's M03 case and C3 fail in opposite
directions under any one fixed combination; the per-value rows show which value
moved.

## Read with care

- **Prefer `relative_deviation` with small histories.** With 3–6 history
  segments the MAD is too noisy to put segments on one scale. In C1, robust z
  reached ρ 0.51 against 0.91 for relative deviation.
- **Report tool age next to any score.** Time in use, or part count since the
  last tool change, tracked gradual wear almost as well as the sensor score in C1.
  - A sensor score earns its keep for sudden events, mixed settings and unknown
    tool age.
  - Put the age in the occurrence or the context so it is returned with each
    comparison (`query_occurrence`).
- **Input validation catches corrupt acquisitions.** Samples must be finite and
  within ±1e9. In the milling data this rejected one run whose values reached 1e34.

## Not yet (phase 2 and later)

- **Several channels on one time base, and nanosecond timestamps.** Context here
  is in sample coordinates per recording, so the adapter converts controller
  time with the recording's rate and start.
- **"History before this point only".** Restrict history with
  `metadata_filter` until acquisition time exists.
- **Representations beyond `simple-v1`** (bands, tooth-passing frequency,
  context-segment envelopes), nearest-neighbour segments, and a viewer.
