# Decision milestone: precommitted retrieval experiment (H2)

Status: **DRAFT, not frozen.** It freezes in a later commit that sets the status to
FROZEN. Freezing happens before any Paderborn file is opened for evaluation. It requires:
- dataset access with recorded hashes (Zenodo copy; `DATASET.md`);
- the licence question answered (done: non-commercial);
- the channel units and `.mat` structure inspected on **development bearings only**.

Researcher-selected thresholds are marked **(provisional)**. Phases 3 (external users)
and 4 (operating envelope) have their own documents; this plan covers phase 2.

## Retrieval question

*Given an interval of housing vibration from a bearing we have not seen, under one
operating setting, do the top-10 intervals retrieved from **other** bearings recorded under
**other** settings share its damage state?* This is the "have we seen this before, under
other conditions?" workflow, scored with class labels as an **explicit proxy** for
usefulness. Human relevance validation is **pending**.

## Data, groups and splits

- Paderborn KAt (`DATASET.md`), housing acceleration channel only, at its native 64 kHz.
  No currents and no multichannel.
- **Independent group = bearing** (32). Every window inherits its bearing, setting,
  damage location and origin from the file name and paper Tables 4, 5 and 7. No operating
  context is inferred.
- **Fixed split** (chosen before any data access, stratified by family):

  | Role | Bearings |
  |---|---|
  | Development (8): tuning and the no-match threshold only | K001, K002, KA01, KI01, KA04, KA15, KI04, KI14 |
  | Held-out (23): primary evaluation | K003–K006; KA03, KA05–KA07, KA09, KI03, KI05, KI07, KI08; KA16, KA22, KA30, KI16, KI17, KI18, KI21; KB23, KB24, KB27 |

- KA08 (artificial outer-ring damage, drilled, extent 2) is absent from the only reachable
  copy and is excluded. This was decided before any data was opened.
- **Query/gallery:** each held-out bearing in turn is the query group, and its gallery is
  the other 22 held-out bearings (leave-one-bearing-out). Eligibility is the same channel,
  rate and unit, from a different bearing, and is applied identically for every method.
- **Windows:** 4096 samples (64 ms, the current extractor maximum), disjoint, 8 evenly
  spaced windows per file.
  - Gallery: all 80 files of every gallery bearing.
  - Queries: 2 windows from each of measurements 1–5 per setting, so 40 per bearing.
  - Query and gallery always come from different bearings, so no query material overlaps
    the gallery.

## Tasks

| Task | Gallery restriction | Role |
|---|---|---|
| **T2 cross-condition** | settings ≠ the query's setting | **primary** |
| T1 same-condition | setting = the query's setting | secondary |
| T3 cross-origin | real-damage queries vs artificial + healthy gallery, and the reverse | secondary |
| N no-match | queries whose class is removed from the gallery: KB queries with every KB bearing removed; healthy queries with every healthy bearing removed | negative-query behaviour |

**Relevance (proxy):** same damage location class (healthy, outer, inner, combined).
A secondary relevance level also requires the same origin (artificial or real).

**Difficult negatives present by construction:**
- healthy vs extent-1 damage;
- outer vs inner damage under the same setting;
- the same bearing type and rig for every specimen.

## Methods (feature vector, distance)

All four methods use the same windows and per-window normalization, and score with RMS
Euclidean distance over their feature vector. This is the existing `evaluation/retrieval.py`
contract.

| Id | Features |
|---|---|
| S | AC RMS, crest, kurtosis (log1p), 3-d |
| F | 12 log FFT band energies, fs/64–0.4 fs geometric (the existing baseline), 12-d |
| **FS** | F + S, 15-d: **content-matched** to Morlet, which also carries RMS/crest/kurtosis |
| **M** | frozen production pooled Morlet `morlet-energy-v1` via the Rust core, 15-d |

- **Frozen configuration (reported first):** z-score per window, unscaled features, exactly
  as the production and evaluator defaults.
- **Tuned variant (identical effort for every method, reported separately):**
  per-dimension standardization with mean and SD from **development** gallery windows.
  It is run with z-score and with no normalization (absolute amplitude may carry
  severity; the units are the same sensor throughout).
- **Windowing variant (secondary, every method):** zero-phase FIR decimation ×4 to 16 kHz,
  then 4096-sample windows (256 ms, bands 250 Hz–6.4 kHz).
- **Tuning budget:** exactly the configurations above. No other search over bands,
  windows, k or distances. The temporal representation is excluded: the task does not
  require temporal ordering.

## Metrics

- Per query window:
  - P@10 under the proxy relevance;
  - AP (mAP) and MRR. Recall@10 is not meaningful, because a relevant pool of hundreds
    of windows caps it.
  - nearest distance.
- **Aggregation:** mean per query bearing, then mean over the 23 query bearings.
- **Uncertainty:** paired bootstrap over query bearings (2000 resamples, seed 7). With 23
  groups on a single rig, intervals are wide and no strong statistical claim is made.
- **Breakdowns:**
  - by the query's setting and the target setting;
  - by family (healthy, artificial, real);
  - by extent level.
- **No-match behaviour:**
  - nearest-distance distributions for N queries vs positive queries;
  - threshold-free AUROC separating them;
  - the false-accept rate at one distance threshold per method, fixed on **development
    bearings only** (development healthy queries with the healthy gallery removed, vs
    development positives) at 90% development positive acceptance.
- **Cost, separately:**
  - extraction time per window per method (Rust Morlet including the adapter, NumPy
    baselines);
  - exhaustive search time per query.
- **Failure gallery:** for M and FS, the 3 worst held-out query bearings on T2. For each,
  one misleading top match and one useful match, with raw waveform, spectrum and setting.

## Decision rules for H2 (on T2, frozen configuration)

Let Δ be the bearing-level P@10 of **M minus FS**, with its 95% interval.

| Outcome | Rule |
|---|---|
| H2 supported | Δ ≥ +0.05 **(provisional)** and the interval lower bound > 0 |
| H2 not supported | the interval upper bound < +0.05: no practically relevant advantage |
| Inconclusive | otherwise |

- M vs F and M vs S are also reported.
- The tuned and windowing variants are descriptive only. They cannot rescue H2.
- **Practical usefulness (provisional):** on T2, the best method's mean P@10 ≥ 0.60,
  and the no-match false-accept rate ≤ 0.20 at the development threshold. This judges
  whether the *workflow* could rest on any of these representations, independently of H2.

## Known limitations

- One rig and one sensor, so no cross-machine generalization.
- 4 discrete settings with no transients.
- The real damage was produced on another rig.
- Fault labels are only a proxy for usefulness.
- The 64 ms window and a lowest band of 1 kHz miss shaft-rate content (the decimated
  variant reaches 250 Hz).
- 23 held-out groups (KA08 missing from the source).
- The licence is non-commercial pending clarification.
- No public checksums: we hash on first download.

## Changes after freezing

Any change after the freeze goes in a changelog here, with its reason. A change made
after held-out results have been seen requires a rerun on data that has not been seen.
For this dataset that is impossible, so such changes are reported as exploratory.
