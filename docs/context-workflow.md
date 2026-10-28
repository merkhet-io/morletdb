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

To look at recordings, context lanes and comparisons interactively, use the local,
read-only [viewer](../tools/viewer/README.md).

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
- **Zero or negligible median:** a relative deviation needs a non-zero history
  median. Since 0.3.1, a value in channel units (`mean`, `std`, `rms`, `p75`,
  `peak_abs`) whose history median is at most 1e-9 of the history's median `rms`
  counts as zero (`morlet_simple_value_floors`). Every comparison skips it: the
  per-value rows (`zero_median`), the summary and calibrated scores, the pooled
  spreads and the neighbour ranking. The reason is rounding: a constant window's
  `std` comes out about 1e-13 of its level, not 0. Before 0.3.1 a frozen sensor's
  history thus had a `std` median of about 1e-15, and any real `std` in the query
  deviated from it by about 1e11. Evaluation C4 found this on 3W.
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

## Acquisitions: several channels on one time axis (phase 2, SQL 0.3.0)

An acquisition groups channel recordings that were measured together: for example
vibration from a DAQ and spindle current from a separate logger. Each keeps its
native rate; nothing is resampled.

```sql
SELECT morlet_enable_acquisitions('spindle');                  -- spindle_acq, spindle_acq_channels
SELECT morlet_create_acquisition('spindle', 'shift-0412', 'M7', '2026-04-12 06:00Z');
SELECT morlet_add_acquisition_channel('spindle', 'shift-0412', 'shift-0412-vib', 'vib', 0, 'daq');
SELECT morlet_add_acquisition_channel('spindle', 'shift-0412', 'shift-0412-cur', 'current',
                                      2000000, 'wallclock', 1000000);   -- +2 ms, ±1 ms

-- Context once, in acquisition time (ns); mapped to every channel's samples.
SELECT morlet_add_acquisition_context('spindle', 'shift-0412', 'ctl-001', '[
  {"key":"block","value":"N120","t_start_ns":5000000000,"t_end_ns":25000000000}, ...]');
SELECT morlet_build_acquisition_segments('spindle', 'shift-0412',
                                         ARRAY['program','block','tool'], ARRAY['part']);

-- One occurrence on every channel, side by side; history only from earlier parts.
SELECT * FROM morlet_compare_acquisition('spindle', 'shift-0412',
  '{"program":"O1234","block":"N120","tool":7}', '{"part":"SN-0042"}',
  before_only => true, score_values => ARRAY['duration_s','p75','std','crest','kurtosis']);

-- One time interval from every channel.
SELECT role, start_sample, cardinality(samples) FROM morlet_read_acquisition('spindle', 'shift-0412', t0, t1);
```

- **Time:** acquisition time is integer nanoseconds from the origin.
  - A channel's sample *n* is at `offset_ns + n·1e9/rate`.
  - `morlet_time_to_samples` maps `[t0, t1)` to `[ceil((t0−offset)·rate/1e9),
    ceil((t1−offset)·rate/1e9))` in exact `numeric`; 25.6 kHz has a 39,062.5 ns
    period.
- **Clock uncertainty:** each channel declares its clock (`daq`, `wallclock`,
  `controller`) and offset uncertainty.
  - `morlet_build_acquisition_segments` trims `ceil(uncertainty·rate)` samples
    from each end of every segment, so samples that may belong to the
    neighbouring block are left out.
  - In the synthetic demo, one such sample inflated a current segment's kurtosis
    deviation to 1170%.
- **History before this point:** with `before_only => true`, `morlet_compare_to_history`,
  `morlet_compare_summary` and `morlet_compare_acquisition` use only history
  segments that started earlier in absolute time (origin + offset + sample
  time).
  - The pooled spreads respect the same cutoff.
  - Recordings without an acquisition and `origin_utc` are left out of such
    history, and as a query they raise an error.
- **Score values for level signals:** the default score set (duration, std,
  crest, kurtosis) was evaluated on vibration, where the mean is near zero.
  - For level signals such as motor current, the level *is* the signal; add `p75`
    with `score_values`.
  - In the demo, an air cut's current drop from 7 A to 3 A scored 0.025 without
    `p75` and 0.57 with it.
  - C1 found the p75 of DC spindle current informative.
- **Retries:** `morlet_create_acquisition` and `morlet_add_acquisition_channel`
  are idempotent; a conflicting retry is an error. Context batches per channel
  are `<batch>@<acquisition>/<role>`.

## Nearest occurrences

`morlet_history_neighbors(collection, segment_id, ...)` returns the history of a
comparison, with the same filters, budget, `before_only` and `score_values`.
- **Ranking:** by the root mean square over the score values of
  `(x_query − x_history) / |history median|`, the same scale as the score.
- **Output:** each neighbour comes with its occurrence, raw interval,
  `simple-v1` values and overlapping labels.
- **Size:** `top_k` goes up to 1000, so a caller can also draw the whole history
  distribution, as the viewer does.
- **Use:** a browsing aid, not a classifier. Evaluation
  [C4](../evaluation/context/c4/results/RESULTS.md) (Petrobras 3W, held out) tested whether the majority
  label of the 5 nearest earlier occurrences names the current event. It did worse
  than the machine's most recent label: balanced accuracy 0.39 against 0.61, Δ −0.21,
  95% CI [−0.28, −0.05]. Most correct matches came from the same well. Paderborn
  bearings (PR #6) showed the same pattern: `simple-v1` values mostly identify the
  machine and period, not the fault. Show neighbours as occurrences to look at; do
  not present their labels as a prediction for the query.

## Level track (SQL 0.3.1, opt-in)

`morlet_level_track(collection, segment_id, process_variance_per_hour,
observation_variance, value_name => 'std', transform => 'log', ...)` asks: under the
same operating conditions, is this machine's level changing, or is it short-lived
variation?
- **Episodes:** the segments a comparison would use (same context for `match_keys`,
  rate, channel, unit, `metadata_filter`, `exclude_labels`, other occurrences). They
  are restricted to the query's machine and to segments that start before the query
  (and at or after `since_utc`), in start-time order, followed by the query itself.
  Ties are ordered by segment id.
- **Model `local-level-v1`:** a scalar local-level Kalman filter on y = ln(value), or
  on the value itself with `transform => 'none'`.
  - The level may move by a variance of `process_variance_per_hour` × hours between
    valid episodes.
  - Each observation scatters around it with `observation_variance`.
  - Both are in squared units of y.
  - The first valid episode sets the level, with variance `observation_variance`.
- **No defaults for the variances.** Calibrate them on development history by maximum
  likelihood (`evaluation/level/kalman.py`, `calibrate`). Do not reuse them across
  machines or values without checking.
- **Output per episode:**
  - the observed value and y;
  - the predicted level before the episode and its variance;
  - the innovation and `innovation_var`, the model-based variance of the observation
    predicted before it was seen;
  - the standardized innovation;
  - the filtered `level` and `level_var`, the uncertainty of the level itself;
  - `status` (`initialized`, `ok`, `invalid_value`) and `model`.
- **Invalid values:** with `transform => 'log'`, a value ≤ 0 is reported as
  `invalid_value` and skipped. There is no epsilon.
- **Nothing is stored.** The track is recomputed from the stored values on every call,
  as the caller, so privileges and row-level security apply. Late episodes,
  corrections and new parameters take effect on the next call.
  - A maintenance reset is `since_utc`.
  - A change of operating context is a different track.
- **Read the residuals, not just the level.** A short disturbance stays visible as a
  large standardized innovation even when the level smooths it. A persistent change
  shows up as a run of innovations with the same sign while the level catches up.
  The function does not alarm, classify or estimate wear.
- **Assumptions:** Gaussian, independent errors. Overlapping or correlated episodes
  make both variances too small. In evaluation L1, correlated noise brought 95% level
  intervals down to 85% coverage.
- **Evidence, synthetic only** ([L1](../evaluation/level/RESULTS.md)):
  - On evenly spaced episodes the filter equals an exponential moving average with the
    same gain.
  - It adds uncertainty, and a faster catch-up after long gaps: 39% lower level error
    than that moving average with 48 h pauses.
  - Real-world value is not established.

```sql
-- Track ln(std) of this segment's channel in its context, since the last bearing change.
SELECT episode, occurrence, round(observed::numeric, 4) AS std,
       round(exp(level)::numeric, 4) AS level_std,
       round(exp(level - 1.96 * sqrt(level_var))::numeric, 4) AS level_low,
       round(exp(level + 1.96 * sqrt(level_var))::numeric, 4) AS level_high,
       round(standardized_innovation::numeric, 1) AS z, status
FROM morlet_level_track('cell', 4711, 0.001, 0.01, since_utc => '2026-03-01 00:00Z')
ORDER BY episode;
```

`exp(level)` is a geometric level of `std`; the interval is model-based.

## Not yet

- **Clock drift and sync points.** One fixed offset per channel.
- **Cross-channel values,** for example coherence between current and vibration.
- **Representations beyond `simple-v1`:** bands, tooth-passing frequency,
  context-segment envelopes.
- **Score sets per channel type chosen automatically.** Today they are explicit,
  through `score_values`.
