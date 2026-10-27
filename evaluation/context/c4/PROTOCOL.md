# C4: do labelled earlier occurrences name the event better than a trivial rule?

Status: **FROZEN** in the commit that adds this line, before the download. Read so
far: the 3W repository README, `3W_DATASET_STRUCTURE.md`, `dataset/dataset.ini`, the
Figshare record's file list, the data article (Vargas et al., arXiv:2507.01048) and
pg_morlet's own `simple-v1` and `morlet_history_neighbors` code. The article shows
plots of seven example real instances and a map of all real instances by well and
date (its Fig. 9). **No data file has been read.** Changes after the freeze are
logged under "Changes after freeze".

## Why

C1–C3 tested comparison with history: is this segment unusual for its context?
Phase 2 added `morlet_history_neighbors`. It returns the nearest other occurrences
of a segment's context, each with its labels, and the viewer shows them. That
implies a claim: the labels of the nearest earlier occurrences say what is
happening now. C4 tests that claim against a trivial rule that knows only the well's
own history.

Paderborn cannot be used. PR #6's held-out evaluation already tested
nearest-neighbour fault retrieval on it, at close to chance. The neighbours mostly
came from the query's own bearing.

## Data

- **3W Dataset 2.0.0** (Petrobras; Vargas et al. 2025, arXiv:2507.01048).
  - Figshare doi:10.6084/m9.figshare.29205836.v1. File `3w_dataset_2.0.0.zip`,
    1,794,819,830 bytes, MD5 `2c23b87b60c5d19ed9cf9559efa6ffa7`.
  - Licence CC BY 4.0. Toolkit code Apache 2.0, not used.
- **Content:**
  - offshore oil wells;
  - 27 variables at 1 Hz (linearly interpolated by the source historian):
    pressures, temperatures, flow rates, choke openings and valve states;
  - per observation, a class label (event type) and a state label (operational
    status, such as open, shut-in or restart).
- **Instances:** one Parquet file per instance.
  - Real instances are named `WELL-<id>_<timestamp of first observation>`. This gives
    the well identity and the time order.
  - The article's Table 6 counts 1,119 real instances from 42 wells, 2011–2023:

    | Code | Event | Real |
    |---|---|---|
    | 0 | normal operation | 594 |
    | 1 | abrupt increase of BSW | 4 |
    | 2 | spurious closure of DHSV | 22 |
    | 3 | severe slugging | 32 |
    | 4 | flow instability | 343 |
    | 5 | rapid productivity loss | 11 |
    | 6 | quick restriction in PCK | 6 |
    | 7 | scaling in PCK | 36 |
    | 8 | hydrate in production line | 14 |
    | 9 | hydrate in service line | 57 |

  - Transient onsets carry the code plus 100. NaN means unknown.
- **Only real instances are used.** Simulated and hand-drawn instances are excluded.
- **Known difficulties (article, Table 7):**
  - 65.9% of variable × instance pairs are missing;
  - 9.8% are frozen (constant);
  - 5.3% of observations are unlabelled.
  - Mappings between variables and historian tags were not verified by the authors.
- **Integrity:** the archive MD5 is checked against Figshare, and SHA-256 is recorded
  for every file. Files are read with pyarrow only, in an isolated interpreter
  (`-I`) from a directory separate from the data.

## Units (fixed now)

- **Windows:** each instance is cut into non-overlapping 1-hour windows (3600
  observations) from its first observation. A trailing partial window is dropped.
- **Window label:** codes 10x are mapped to x (transient onset counts as the
  event). A window's label is the mapped class label held by at least 80% of its
  observations. Otherwise the window has no label: it is neither a query nor a
  reference, and it is counted.
- **Context:** the state label, if one value holds at least 80% of the window.
  Otherwise the context is `mixed`; a NaN state gives `unknown`. Both are reported.
  - The state label is derived from valve configuration, which is known at the time
    of operation. It is not the event label.
- **Channels:** the continuous variables, i.e. pressures, temperatures, flow rates
  and choke openings, each as one channel of the acquisition (the instance).
  - Valve-state variables are excluded: they define the context.
  - A channel is absent from a window if any of its observations there is missing
    (NaN). The product rejects non-finite samples, and nothing is imputed.
- **Independent unit:** the well.

## Values and distance (as the product)

- **Values per channel:** `simple-v1` `std`, `crest`, `kurtosis` and `p75`. This is the
  product default plus `p75`, which the docs recommend for level signals.
  `duration_s` is constant (one hour) and is left out.
- **Per channel:** the relative difference (x_query − x_ref) / |median_ref|, with the
  median over the reference windows of the same context and channel.
- **Values as the product computes them:** a frozen (constant) channel has `std` 0,
  `kurtosis` 0 and `crest` 1, and these are compared like any other values.
- **Distance:** the RMS over every (channel, value) pair present in both windows
  whose reference median is not 0. As in the product, a value whose median is 0 is
  left out.
- **Comparable:** a reference window needs at least 3 channels shared with the query.
  Otherwise it is not a candidate.
- This acquisition-level distance generalises `morlet_history_neighbors`, which
  works per channel. Product check below.

## Time order (as `before_only`)

- **Query:** every labelled window in a context other than `mixed` or `unknown`.
- **References for a query:** labelled windows of **other instances** that started
  before the query window started, as `before_only` does. They may come from any
  well, including the query's own. Windows of the query's own instance are never
  references.
- A query needs at least 5 comparable references. Otherwise it is excluded and
  counted.

## Arms and prediction

- **N (primary):** the k = 5 nearest comparable references in the same context.
  The predicted label is the majority, with ties broken by the smaller summed
  distance.
- **B (primary baseline), "same well, last label":** the label of the most recent
  reference window from the query's own well. If none exists, the most frequent
  reference label overall. This rule knows the well's history and nothing about the
  signal.
- **Reported:**
  - N restricted to the query's own well;
  - N context-free;
  - prior: the most frequent reference label overall.

## Primary measurement

- **Balanced accuracy (BA):**
  - for each class, take the mean over its query instances of the fraction of the
    instance's windows predicted correctly;
  - then take the mean over the eligible classes.
- **Eligible classes:** those with query windows from at least 3 instances in at
  least 2 wells after the rules above. This is determined from labels and file
  names only, before any signal value is computed.
- **ΔBA** = BA(N) − BA(B), on the same queries.
- **CI:** cluster bootstrap over wells, 2000 resamples, seed 7. Each resample redraws
  wells with replacement and recomputes both BAs from the predictions already made.
  Resamples missing an eligible class are dropped and counted.

## Decision rules

- **Supported:** ΔBA ≥ 0.05 and its CI lower bound > 0.
- **Not supported:** the CI upper bound of ΔBA < 0.05.
- **Inconclusive:** otherwise. It is reported, and not rescued by changing the
  analysis.
- **Reading a negative:** if N does not beat B, the neighbours' labels add nothing
  to knowing the well's last label. The viewer then presents neighbours as a
  browsing aid, and the docs say so.

## Reported, not decided on

- Confusion matrices for N and B, BA per class and per context.
- Excluded windows and queries, by reason.
- **The share of N's neighbours from the query's own well, and from the 30 days
  before the query.** This is the Paderborn lesson: are neighbours similar
  operation or just the same well and period?
- Which channels and values dominate the distances.

## Product check

Three queries are chosen now: for each of the three largest eligible classes, the
earliest query window that is the first window of its instance. (The first window
has no earlier windows in its own instance, so `before_only` and the harness select
the same references.)
- For each query, a collection holds exactly the query's instance and its
  references:
  - instances as acquisitions with `origin_utc`, one recording per channel;
  - the state as context;
  - the 1-hour windows as segments.
- `morlet_history_neighbors` is run per channel (`match_keys` state, `before_only`,
  `score_values` std, crest, kurtosis and p75, `top_k` 1000).
- Its single-channel distances must agree with the harness's single-channel
  distances to 1e-9 relative, and its ordering must be the same.

## Limitations

- Process data at 1 Hz, not vibration. This tests the context and neighbour logic,
  not waveform features.
- Event types cluster by well and period (article Fig. 9). Baseline B exists to
  expose that. N may also exploit it, and the "own well, last 30 days" share reports
  how much.
- The labels are Petrobras experts', validated by a committee. Their errors are not
  measurable here.
- The 3W dataset is widely used in published work. It is new to this project, but not
  unexamined by others.

## Changes after freeze

None.
