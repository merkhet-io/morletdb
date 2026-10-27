# C4: does the label of the nearest occurrences identify the fault?

Status: **DRAFT**, not frozen. Nothing has been downloaded. The facts under "Data"
come from the dataset's web pages and publications; the points marked *(verify)*
are checked against the official documentation and file listing before the freeze.
The protocol is frozen in its own commit before any signal file is downloaded.
Changes after the freeze are logged under "Changes after freeze".

## Why

C1–C3 tested comparison with history: is this segment unusual for its context?
Phase 2 added two things that have only run on synthetic demo data:
- **`morlet_history_neighbors`:** the nearest other occurrences of the same context,
  each with its labels. The viewer shows them next to a segment.
- **Acquisitions:** several channels with a common time axis.

Showing a neighbour's label next to a segment implies a claim: the label of the
nearest occurrences says something about this one. C4 tests that claim, and
whether a second channel (motor current) adds to vibration. If it fails, the
neighbours remain a browsing aid and the docs say they are not a diagnosis.

## Data

- **Paderborn University bearing data set** (KAt-DataCenter; Lessmeier, Kimotho,
  Zimmer & Sextro, PHM Society European Conference 2016). Licence CC BY-NC 4.0,
  compatible with this non-commercial project; attribution as the site requests.
- **Content:**
  - 32 rolling bearings (type 6203) in an electromechanical drive test rig:
    6 healthy, 12 with artificial damage, 14 with real damage from accelerated
    lifetime tests;
  - four operating conditions varying speed, load torque and radial force;
  - per bearing and condition, 20 measurements of about 4 s *(verify)*;
  - synchronously measured housing vibration and two motor phase currents at
    64 kHz, plus low-rate speed, torque, force and temperature *(verify rates)*;
  - MATLAB files, one archive per bearing *(verify total size; stated before
    asking to download)*.
- **Labels:** the damage class and location assigned by the dataset authors
  (healthy, outer ring, inner ring, combined), per bearing.
- **Integrity:** archive and file SHA-256 recorded at download. Each measurement
  must contain the vibration and phase-1 current signals with finite values;
  violations are reported and excluded.
- **Reading:** MATLAB files are read with `scipy.io.loadmat` (no code execution),
  in an isolated interpreter (`-I`) from a directory separate from the data.

## Units and split (fixed now)

- **Primary set:** the real-damage bearings with a single damage location and the
  healthy bearings: 6 healthy, 5 outer ring, 6 inner ring *(verify the codes)*.
  Three classes. The 3 real bearings with combined damage are reported only.
- **Unit: the bearing.** Measurements of one bearing are strongly correlated, and
  splitting them between reference and query leaks the bearing's identity
  (a known problem in published results on this data set). Every comparison is
  **leave-one-bearing-out:** a query bearing's measurements are compared only
  with the other bearings of the primary set.
- **Artificial damage is not used in the primary analysis.** A known domain shift
  separates artificial from real damage. Artificial-reference → real-query is
  reported as a separate arm.
- **No development step.** Values, distance, k and the vote are fixed below,
  before any value is seen. The harness is tested on synthetic data only.

## Representation (as the product)

- One recording per channel per measurement; the measurement is one acquisition.
- Context: the operating condition code. One segment per measurement, the whole
  measurement (no blocks).
- Values: `simple-v1` per channel, the default `score_values`
  (`duration_s`, `std`, `crest`, `kurtosis`), as `morlet_history_neighbors`.
- **Distance:** as `morlet_history_neighbors`, the RMS over values of
  (x_query − x_ref) / |median_ref|, the median over the reference segments of the
  same context.

## Arms

- **V (primary):** vibration only, same operating condition.
- **VC (primary for H2):** vibration and phase-1 current, same operating
  condition. Distance: RMS over the eight values of both channels. This is not
  yet a product function; if H2 is supported, an acquisition-level neighbour
  function is a candidate.
- **Reported:**
  - **V, context-free:** all conditions as reference (a third check of the
    context claim, on bearing data);
  - **C:** current only;
  - **artificial → real:** V with the 12 artificial bearings plus the healthy ones
    as reference;
  - **combined damage** bearings as queries (their true class is not among the
    three).

## Prediction

- For each query measurement: the k = 5 nearest reference measurements; the
  predicted class is the majority label, ties broken by the smaller summed
  distance.
- No threshold, no "unknown" answer: every measurement is classified.

## Primary measurement

- **Balanced accuracy (BA)** over the 17 bearings: for each class, the mean over
  its bearings of the fraction of that bearing's measurements predicted correctly;
  then the mean over the three classes. Each bearing counts equally within its
  class; each class counts equally. Chance is 1/3.
- **CI:** bootstrap over bearings stratified by class, 2000 resamples, seed 7.
  Each resample redraws bearings with replacement within each class and recomputes
  BA from the leave-one-bearing-out predictions already made.

## Decision rules

- **H1 (the neighbour label is informative), arm V:**
  - **supported:** BA CI lower bound ≥ 0.50;
  - **not supported:** BA CI upper bound < 0.50;
  - **inconclusive:** otherwise.
  - Why 0.50: with three classes, a hint that is right less than half the time is
    not a useful hint, even when it beats chance.
- **H2 (the current channel adds), VC − V on the same bearings:**
  - **supported:** ΔBA ≥ 0.05 and its CI lower bound > 0;
  - **not supported:** the CI upper bound of ΔBA < 0.05;
  - **inconclusive:** otherwise.
- With 17 bearings the intervals may be wide. Then the result is inconclusive; it
  is reported, and not rescued by changing the analysis.

## Reported, not decided on

- Confusion matrices per arm; BA per operating condition.
- Per bearing: the fraction correct, and which bearings fail.
- Which values dominate the distances.

## Product check

- Arm V is computed in the harness with the formula above. For three folds chosen
  now (the first bearing of each class in the dataset's order), the neighbours are
  also obtained from `morlet_history_neighbors` on a collection holding the fold's
  reference bearings. Neighbour IDs must agree, distances to 1e-9 relative.

## Limitations

- One rig, one bearing type, laboratory conditions; damage classes, not severity.
- Whole measurements as segments; no machining-block context.
- 17 independent units: this test can find a large effect, not a small one.
- No time order between bearings, so `before_only` is not tested.
- The labels are the authors'. Real-damage bearings come from accelerated tests,
  not from field failures.

## Changes after freeze

None.
