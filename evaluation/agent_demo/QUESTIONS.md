# Agent demo "Why this part?": fixed questions and scoring

Status: **fixed** in the commit that adds this file, before any MorletDB database was loaded with the data and
before any tool was run on it. Later changes go into a dated "Deviations" section at the end, not into the text
above it.

This is a **demonstration on development data**, not a study. Its figures say how the scripted agent behaved on
one machine of one public dataset; they are not evidence for or against any hypothesis, and they are not
results of the planned decision-model study.

## Data

- Bosch CNC milling vibration data (Tnani, Feil and Diepold, *Procedia CIRP* 107 (2022),
  doi:10.1016/j.procir.2022.04.022), licence CC BY 4.0, the local copy at `data/bosch/repo` with its `COMMIT`
  and `SHA256SUMS`.
- **Machine M01 only.** M02 and M03 are reserved as confirmation data for a planned frozen study; no file of
  theirs is opened, loaded or counted by this demo.
- M01 is not untouched: C2 (all machines) and F2 development (M01) used it before. What was looked at for this
  file: the file names, the number of good and bad files per operation (OP00..OP14: 485 good, 34 bad), the
  HDF5 dataset name (`vibration_data`), its dtype (int64, three axes) and the run lengths (26,793 to 273,408
  samples at 2 kHz, median 89,088). Nothing else.

## Database layout

- Collection `public.bosch_m01`, one recording per run and axis (`channel` = `x`, `y`, `z`), integer counts,
  2 kHz, search envelopes of 4,096 samples (stride 2,048).
- Run id: the file name stem plus the first six hex digits of the file's SHA-256 (the same stem exists once as
  good and once as bad for some runs). Recording id: `<run id>-<axis>`.
- Metadata: `machine`, `operation`, `period`, `run`, `axis`, `label` (`good` or `bad`, the dataset's label),
  `file`, `sha256`, and a **nominal** start time: the first day of the period's month plus 10 minutes per run
  in file-name order within (operation, period). The dataset has no timestamps; the nominal time only orders
  runs so that `coverage`, `trend` and `latest` work, and is marked as nominal in the metadata.
- Context (if the server supports it): keys `machine` and `operation` over the whole recording, `run` as the
  occurrence; one segment per recording, `simple-v1` values. Label `bad` on the whole of every bad recording.
- The flag: every bad run carries a logged decision `s1:flagged` (model `dataset-label`, probability 1). In
  this demo the flag **is** the dataset label; no model was run.
- The agent connects as a read-only role.

## Which runs are investigated

**All 34 bad M01 runs**, in sorted run-id order. No sampling, so no seed is needed for the selection. The one
seeded choice is the reference pool for Q2 (below): seed **20261111**.

## The questions, per flagged run

Every answer is a list of statements. A statement carries evidence when it names collection, recording and
sample range (`public.bosch_m01 / <recording> / a..b`), or a tool result that names them.

**Q1. What is it, and what data exists?** Machine, operation, period, axes and length of the run (`latest`,
`list_recordings`); how many good and bad runs of the same operation exist (`coverage`, grouped by label).

**Q2. Which segment and which window deviate most from the good history of the same operation and axis, and by
how much?**
- Segment level: `decision_state` per axis, history = same machine and operation, same axis, bad runs excluded
  (`exclude_labels = bad`). Reported: summary score, its top value, and the largest per-value deviation.
- Window level: non-overlapping windows of 4,096 samples from sample 0. Pool = the flagged run plus 5 good runs
  of the same operation, drawn with `numpy.random.default_rng(20261111)` from the sorted list of good runs of
  that operation (all of them if fewer than 5). For every run r, axis a and window i: P = variance of the window
  (`describe_interval`, std²); deviation d(r, a, i) = |10·log10(P(r, a, i) / median of P(s, a, i) over the
  pool's **good** runs s ≠ r that have window i)|, in dB, computed only where at least 3 such runs exist.
- Answer: the flagged run's window with the largest d (its axis, window and dB), and whether the pool's largest
  d lies in the flagged run.

**Q3. Level or shape, and which frequency band?** For the flagged run's top window, against the same window of
the pool's good run whose P is the median there:
- `compare_intervals` (explicit reference): overall power ratio in dB; the band with the largest share of the
  excess power if the ratio is ≥ 0, else the band with the most negative ratio.
- `describe_interval`: kurtosis and crest factor of both windows.
- `envelope_spectrum` of both windows: strongest envelope line and its height above `noise_peak_db`, and the
  reference's level at that frequency (`check_frequencies`).
- Rule: **level** if |power ratio| ≥ 3 dB; **shape** if kurtosis differs by a factor ≥ 2, or crest factor by a
  factor ≥ 1.5, or the flagged window has an envelope line ≥ 10 dB above its noise peaks where the reference is
  < 6 dB above its own; both, one, or "neither above the thresholds".

**Q4. Where does it start?** On the top axis:
- the first window with d ≥ 3 dB (Q2's deviations);
- `find_onsets` over the whole flagged run (STA 0.25 s, LTA 5 s, threshold 4) and over the same reference run
  as Q3; flagged-run onsets with no reference onset within ±1 s are reported as new.

**Q5. How does the run sit in the operation's history?** `trend` over the operation's recordings of the top
axis (one point per recording). Reported: the flagged run's rms rank among them and any change point. Known
gap #385: `trend` measures each recording's first 4,096 samples, so this describes the lead-in, not the
deviating window; the answer says so.

**Q6. Have similar windows been seen before, and what happened then?**
- `search_similar` on the flagged top window, `metadata_filter = {"operation": <op>}`, `top_k = 50`,
  `shifts = 0`; `search_from_time` at the window's nominal start time, same filter, `top_k = 50`.
- Hits from the flagged run itself (any axis) are dropped and counted (known gap #384: search returns windows
  of the query's own recording). Of the remaining hits, the first 5 and 10 are scored.
- precision@k = share of those hits whose run is labelled bad. Chance level = the share of the other runs'
  stored search windows of that operation and axis that belong to bad runs.
- `decisions` for each distinct hit run: was it flagged?

**Q7. Evidence.** `read_interval` of the flagged top window with all 4,096 raw values. A separate check (not
part of the agent) compares them with the HDF5 file value for value.

**Answer.** Per run, in plain English: what is different, where, since when, seen before and what happened
then, a recommendation, evidence links. The recommendation follows fixed rules:
- d ≥ 6 dB or decision-state score ≥ 1: "inspect this part at <window>; the deviation is clear";
- else d ≥ 3 dB: "check this part at <window>; the deviation is moderate";
- else: "no clear deviation found by these tools; check the process record";
- plus, when precision@5 of `search_similar` ≥ 0.6: "similar windows were mostly in bad runs before".

## Metrics, over the 34 investigations

1. Share of answers whose pool-wide top window lies in the flagged run, with the chance level (the flagged
   run's share of the pool's scored windows, averaged).
2. precision@5 and precision@10 of `search_similar` and of `search_from_time` hits being bad, mean over runs,
   with the mean chance level.
3. Share of statements with an evidence link.
4. Wall time per investigation (median, max), and tool calls per investigation.
5. Tool failures: every tool call that raised, by tool, with its message.
6. Evidence check: share of top windows whose `read_interval` values equal the HDF5 values bit for bit.

No pass or fail thresholds: this is a demonstration, and every number is reported as it comes out.

## Deviations

- **2026-11-11, after the run, additions only (nothing above was changed):** `metrics.py` also reports (a) a
  control: the Q2 window rule applied leave-one-out to the good runs of each pool, to show how often the
  "inspect"/"check" thresholds fire on good runs; (b) how many bad hits in the top 5 of `search_similar` come
  from the flagged run's own period. Both were added because the first results raised the question; they are
  descriptive.
- **Before the full run:** one wording fix in `demo_agent.py` after a trial on the first run: the #385 caveat
  under Q5 now says when the deviating window *is* the lead-in (window 0) instead of always saying it is not.

## Version 2 (fixed 2026-11-11, before any v2 run on the data)

v1's rules, scripts and results stay as they are (`demo_agent.py`, `metrics.py`, `results/`), labelled v1. v2
is `demo_agent_v2.py`, `metrics_v2.py` and `results_v2/`. Same data (M01 only), same image, same 34 flagged
runs, same 5 comparison runs per operation (seed 20261111). What changes:

**V2.1 Alignment.** Per run and axis, the start of machining is found with a fixed rule on `read_interval`'s
min/max buckets of the whole recording (`max_points` = 2·ceil(n/512), so buckets of about 512 samples):
- bucket amplitude a_j = (max_j − min_j)/2 (the offset of the axis does not matter); M = median of all a_j;
- onset = start sample of the first bucket j with median(a_j..a_j+3) ≥ 0.5·M;
- the onset is **clear** if j = 0 (no lead-in: machining from the first sample) or the lead-in's median
  amplitude, median(a_0..a_j−1), is ≤ 0.25·M; otherwise it is **unclear**, and that axis falls back to onset 0
  (raw positions). Counts of each case are reported.
- Aligned window i of a run and axis = samples [onset + 4096·i, onset + 4096·(i+1)), i ≥ 0, inside the
  recording. The lead-in is not compared. d(r, a, i) is v1's deviation (|dB| of the window variance against the
  median over the comparison runs ≠ r with window i, at least 3 of them), on aligned windows.

**V2.2 Calibrated thresholds.** Run measure D(r) = max over axes and aligned windows of d(r, a, i), against
the operation's 5 comparison runs. The good runs of each operation that are not comparison runs are split by
`numpy.random.default_rng(20261112).permutation` of their sorted list: the first floor(n/2) are
**calibration** runs, the rest **held-out** runs. check = 80th and inspect = 95th percentile of D over the
calibration runs (`numpy.percentile`, linear), per operation where it has at least 40 calibration runs, else
pooled over the 13 operations that have bad runs. Headline: the share of flagged runs and of held-out good runs
reaching check and inspect.

**V2.3 Period handling.** `search_similar` hits (own run dropped, as v1) are split into hits from the flagged
run's own period and from other periods; precision@5 and @10 are computed on each list separately, each with
its own chance level (the share of bad runs' stored windows among the other runs of the operation in the same
period, or in the other periods). The answer says "similar to N other bad runs, M of them from other periods"
(N = distinct bad runs among the top 10 hits overall). The same split is reported for `search_from_time`.

**V2.4 Level vs shape.** For a run's top window (the window giving D):
- **level** if D ≥ the check threshold;
- **shape** if S ≥ the 80th percentile of S over the calibration runs, where S = the median, over the
  comparison runs (other than the run itself), of the smallest `search_similar` distance from the top window
  to that comparison run's recording of the same axis (`metadata_filter = {"run": <comparison run>}`,
  `top_k = 1`): "no good run has a window with this envelope shape";
- reported as level only, shape only, both, or neither; the distribution is reported on flagged and on
  held-out good runs. The v1 kurtosis/crest/envelope-line rule is dropped (kurtosis, crest factor and the
  envelope spectrum are still described, not classified).

**V2.5 Recommendation.** inspect if D ≥ inspect, check if D ≥ check, else "no clear deviation", with the
seen-before sentence of V2.3. The window is named in aligned and in raw samples.

**Headline table, v1 against v2:** flagged-run top-window rate (pool top in the flagged run), and how often the
flagged run's top window does not start at sample 0; inspect/check firing on flagged and on held-out good runs
(v1's fixed 6 and 3 dB applied to the same held-out runs, raw positions); precision@5/10 overall, same period
and other periods; evidence links and bit-exactness; time per investigation (calibration timed separately).
