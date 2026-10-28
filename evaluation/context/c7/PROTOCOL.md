# C7: does a pinned healthy baseline stop own history from absorbing degradation?

Status: **FROZEN** in the commit that adds this line, before the download. Read so far:
the dataset's official page (biaowang.tech) and GitHub README, and the secondary
sources cited in the run-to-failure survey (instinct,
`research/2026-10-27-run-to-failure-dataset-survey.md`). **No data file has been
read.** Changes after the freeze are logged under "Changes after freeze".

## Why

C5 (PR #35) compared each bearing with **all** its earlier recordings. On B09 the
vibration rose at 14% of life and stayed high: a plain RMS alarm kept flagging it, but
the history score fell back below its threshold in 76% of later recordings, because
the elevated recordings entered the history and moved its median. C6 (PR #34) found
the same kind of problem from the other side: a reference library taken early in life
went out of date within hours.

The fix to test: compare each recording with a **pinned baseline**, a fixed set of
early recordings, instead of with everything before it. pg_morlet 0.3.1 can already
do this. Pinned recordings carry a metadata tag, and `morlet_compare_summary` is
called with `metadata_filter` set to that tag. So C7 tests a way of using the product,
not new code. A first-class baseline option is only worth building if C7 supports it.

## Questions

- **Q1 (the fix).** Does the history score against a pinned baseline (H-PIN) raise a
  **stable alarm earlier** than the same score against all earlier recordings
  (H-ALL)?
- **Q2 (product value).** Does H-PIN raise a stable alarm earlier than a plain RMS
  alarm (B-RMS)? C5 found that the all-history score did not (Q1 of C5).

Each question has its own decision. Neither result is combined with the other or with
an earlier evaluation.

## Data

- **XJTU-SY rolling element bearing datasets** (Xi'an Jiaotong University and
  Changxing Sumyoung Technology). Official pages: biaowang.tech/xjtu-sy-bearing-datasets
  and github.com/WangBiaoXJTU/xjtu-sy-bearing-datasets.
  - **Terms:** no formal licence. The page says the datasets "are publicly available
    and anyone can use them to validate prognostics algorithms" and asks publications
    to cite Wang, Lei, Li and Li, IEEE Trans. Reliability 69(1):401–412, 2020,
    doi:10.1109/TR.2018.2882682. We use it for evaluation only, cite it, and do not
    redistribute any data.
  - **Download:** from one of the listed mirrors. The archive size is not stated on
    the pages. It is read from the mirror listing and stated in the download request,
    and SHA-256 is recorded at download.
- **Content (official page):**
  - 15 LDK UER204 bearings run to failure, 5 per operating condition: 2100 rpm and
    12 kN; 2250 rpm and 11 kN (one secondary source says 2500 rpm; not used here);
    2400 rpm and 10 kN. Speed and load are **constant within each bearing**.
  - Every minute, one snapshot of 32,768 samples (1.28 s) at 25.6 kHz from two
    housing accelerometers: CSV column 1 horizontal, column 2 vertical.
  - Faults are natural (accelerated life), not seeded.
- **End of life (secondary sources, not checked in the primary paper):** a test stops
  when the vibration amplitude in one direction exceeds 10 × the maximum amplitude
  seen in normal operation. End of life is therefore defined by amplitude.
- **Not yet read:** the per-bearing table (snapshot count, lifetime, fault element) is
  only an image on the official page. It is read when the data arrives. File counts
  that do not match it are logged before any value is computed.
- **Relation to consumed data.** XJTU-SY has not been used in any earlier evaluation.
  FEMTO (W1) and IMS (compression H2) are consumed and are used here only to test the
  harness. Paderborn run-to-failure (C5, C6) is consumed: it is not used to develop or
  tune anything, and only enters the exploratory analysis below, after the XJTU-SY run.
- **Handling:** the data stays in `data/xjtu-sy/`, separate from code, and is read in
  an isolated interpreter (`python -I`). Nothing is unpickled.

## Units (fixed now)

- **Unit:** the bearing (15). All 15 are primary: each ended on the same criterion.
- **Segment:** one snapshot, horizontal channel, as stored. The vertical channel is
  reported, not decided on.
- **Time:** snapshot number × 60 s (files are numbered in acquisition order; the page
  states one snapshot per minute). **End of life** = time of the last snapshot.
- **Context:** constant within a bearing, so every arm uses the bearing's own
  recordings only; the context rule is not tested here (C5 Q2 tested it).

## Arms (one indicator per segment)

Values are `simple-v1` as pg_morlet 0.3.1 computes them, as in C5 (`c5.simple_values`,
the negligible-median floor). The **score** is the product's summary score: the
maximum relative deviation over `duration_s`, `std`, `crest` and `kurtosis` (the
default `score_values`). `duration_s` is constant and contributes 0.

| Id | Indicator | History |
|---|---|---|
| **H-PIN** (candidate) | score | the bearing's **first 30 snapshots** (the pin, about 30 min). A pinned snapshot is scored against the other 29 |
| **H-ALL** | score | **all** earlier snapshots of the bearing; scored with ≥ 5 (`min_history` 5) |
| **B-RMS** (baseline) | RMS of the segment | none (the alarm rule calibrates it) |
| H-GATED (reported) | score | the pin plus later snapshots whose smoothed H-GATED indicator was at or below its threshold (a slow, gated refresh) |

H-ALL here is C5's H-CTX with a single context.

## Alarm rule (identical for every arm; C5's rule with the window in minutes)

Indicators are computed over the arm's **scored** segments in time order.

1. Smooth with a **causal** moving median of 5 scored segments.
2. **Calibration:** the scored segments among the first 30 snapshots (the same
   30 minutes as C5's 150 measurements). If fewer than 30 are scored, calibration
   extends to the first 30 scored segments; the extension is reported per arm and
   bearing.
   - Threshold: calibration median + 6 × (1.4826 × MAD) of the smoothed indicator.
3. **Alarm:** the first scored segment after calibration that starts 6 consecutive
   smoothed exceedances.
4. **Stable alarm:** after the alarm, the smoothed indicator exceeds the threshold in
   ≥ 50% of the remaining scored segments. Otherwise the alarm is **unstable**.
5. **Stable lead** = (end of life − alarm time) / (end of life − end of calibration)
   for a stable alarm; **0** for no alarm or an unstable alarm.

The pin is the calibration window. H-PIN's calibration scores are therefore
leave-one-out within the pin, which is what the product returns for a pinned segment
(it never compares a segment with itself).

No parameter is tunable. Harness development on FEMTO and IMS (consumed) and on
synthetic signals may only fix implementation errors, each logged.

## Decision rules (15 bearings)

For each question, Δ = mean over bearings of stable lead (H-PIN − comparator), with a
bootstrap over bearings (2000 resamples, seed 7). The comparator is H-ALL for Q1 and
B-RMS for Q2.

- **Supported** if Δ ≥ 0.05, its CI lower bound is > 0, and H-PIN has no more unstable
  alarms than the comparator + 1.
- **Not supported** if the CI upper bound of Δ is < 0.05.
- **Inconclusive** otherwise. Report it and do not tune.

## Reported, not decided on

- Per bearing and arm: alarm time (minutes after calibration and fraction of life),
  stable or not, the share of later scored segments above threshold, and the value
  behind the alarm.
- **Absorption, directly:** for each bearing with a stable B-RMS alarm, the share of
  segments after that alarm in which H-PIN and H-ALL are above their thresholds. This
  is the quantity that failed on C5's B09.
- **Without `crest`** (score over `std` and `kurtosis`). End of life is
  amplitude-defined, and crest is the score value closest to amplitude.
- **Alarms in the last 10% of life**, per arm. Those are close to the stopping
  amplitude and say little about early warning.
- H-GATED against H-ALL and against B-RMS, with the same Δ and CI.
- The vertical channel.
- **Exploratory, on consumed data (Paderborn run-to-failure, 14 primary bearings):**
  H-PIN with the pin = the first 150 measurements, restricted to pinned measurements in
  the query's context (C5's bands) and scored with ≥ 5 of them, against C5's H-CTX and
  B-RMS under C5's alarm rule. Reported per bearing, B09 first. It is run once, after
  the XJTU-SY verdicts are committed, with the parameters above, and decides nothing.
- **Product check:** for 3 bearings, at least 3 segments each, including at least one
  pinned and one after the pin, the harness's H-PIN score must match
  `morlet_compare_summary(..., metadata_filter => '{"c7_pin": true}', before_only =>
  true, min_history => 5)` on pg_morlet 0.3.1 to 1e-9 relative. Each snapshot is
  loaded as a raw-only recording in its own acquisition (origin = snapshot time), and
  pinned snapshots carry the metadata `{"c7_pin": true}`. A mismatch stops the
  evaluation until the harness is fixed and logged.

## Controls and limitations

- **Same for every arm:** segments, channel, alarm rule and end of life.
- **Circularity.** End of life is a vibration-amplitude threshold, which every
  vibration indicator approaches near the end. This inflates every arm's lead and
  favours B-RMS in Q2. The no-crest analysis and the last-10% count show how much it
  matters.
- **The pin may not be healthy.** The first 30 minutes include run-in. A pin taken
  during run-in makes H-PIN's later scores large for every segment; the alarm rule's
  calibration on the same window does not remove that. This works against H-PIN.
- **Constant conditions.** A pin taken under one condition is only meaningful for that
  condition. On machines whose conditions change, the pin needs members in each
  context, as in the exploratory Paderborn analysis. Q1 says nothing about that case.
- **No labelled onset.** Stable lead is a proxy: an earlier stable alarm is better only
  if degradation had begun.
- **15 bearings, one rig, accelerated life, UER204 bearings.** Results say nothing
  about other machines.
- **Fragile hosting.** The data come from file-sharing mirrors without a DOI; the
  mirror and checksums are recorded.

## Changes after freeze

None yet.
