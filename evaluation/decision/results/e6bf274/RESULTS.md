# H2 result: Paderborn cross-condition bearing retrieval

Plan: [`../../PLAN.md`](../../PLAN.md), frozen in `3c1f211`; the logged implementation fix
is in `e6bf274`. The run used `e6bf274` with a clean tree. The held-out evaluation ran
**once**, after the development run.

- Data: Zenodo copy of Paderborn KAt (CC BY-NC 4.0, Lessmeier et al. 2016). All 31
  archives match the record's MD5s (`verify.json`, `SHA256SUMS.archives`,
  `SHA256SUMS.mat`).
- Rust adapter sha256 `674528d3…` (`features.json`).

## Population

- 2,480 files were read. **729 (29%) were excluded by the frozen rate rule:** they hold
  extra samples within the 4 s span. Every bearing lost some files, from 3 to 51 of 80
  (`prepare.json`).
  - This is far more than the 6/80 observed on K002 before freezing. It shrinks the
    population and may correlate with acquisition sessions.
  - Two held-out bearing × setting cells have no queries left: KA16 at N15_M01_F10 and
    KB24 at N15_M07_F04.
- 14,008 windows in total. Held-out: 630 query windows from 23 bearings against 10,560
  gallery windows. Development: 212 query windows from 8 bearings.
- The vibration unit fields are empty in every file.

## Primary decision (T2: other bearings under other settings, z-score, unscaled, P@10, location)

| | Δ P@10 (M − FS) | 95% CI (bootstrap over 23 bearings) | Rule |
|---|---|---|---|
| **Morlet vs FFT bands + statistics** | **−0.032** | **[−0.098, +0.036]** | upper bound < +0.05 → **H2 not supported** |
| Morlet vs FFT bands | −0.003 | [−0.062, +0.058] | reported |
| Morlet vs statistics | +0.011 | [−0.076, +0.102] | reported |

No configuration shows a Morlet advantage. The tuned and windowing variants are
descriptive only:

| Configuration | S | F | FS | M |
|---|---|---|---|---|
| native, z-score, raw (**frozen**) | 0.283 | 0.296 | 0.326 | 0.294 |
| native, z-score, standardized (development fit) | 0.285 | 0.323 | 0.342 | 0.340 |
| native, no normalization, raw | 0.334 | 0.361 | 0.339 | 0.369 |
| native, no normalization, standardized | 0.336 | 0.406 | 0.418 | 0.409 |
| decimated ×4, z-score, raw | 0.236 | 0.303 | 0.279 | 0.251 |
| decimated ×4, no normalization, raw | 0.355 | 0.345 | 0.355 | 0.351 |

All comparisons, mAP, MRR and the T1/T3 tasks are in `evaluation-heldout.json`.

## Is retrieval useful at all here?

- **Barely above chance.** A random ranking scores P@10 = 0.267 on T2 (label prior,
  `descriptive-heldout.json`). The best frozen method reaches 0.326, and the best variant
  0.418.
  - Same-condition retrieval (T1) is 0.33–0.44 frozen, against chance 0.266.
  - Cross-origin retrieval (T3; real damage vs artificial damage plus healthy) is
    0.21–0.31, against chance 0.262.
- **No-match queries cannot be recognized.** The AUROC of nearest distance between
  no-match and class-present queries is 0.44–0.61 across configurations. At the
  development threshold, 92–100% of no-match queries are accepted.
- **The provisional practical criteria are not met:** best T2 P@10 ≥ 0.60 and
  false-accept ≤ 0.20 both fail, for every method.
- **What the features do capture is the individual bearing.** In an exploratory check
  (not pre-registered) that excludes only the query's own file, the share of top-10
  matches from the query's own bearing is:

  | Statistics | FFT bands | FFT + statistics | Morlet |
  |---|---|---|---|
  | 0.15 | 0.85 | 0.58 | 0.91 |

  Spectral features identify the specimen and its mounting far more than its damage
  class.
- **Cost:** feature extraction takes 0.2–0.3 ms per 4096-sample window for the NumPy
  baselines and 7–8 ms for Rust Morlet, including the JSON adapter. Exhaustive search
  takes ≤ 0.05 ms per query over 10,560 windows. Search cost is irrelevant at this scale.

## Failure gallery (`gallery/`)

The 3 worst held-out query bearings on T2 for M (KA03, KA16, KB27: P@10 = 0) and for FS
(KB27, KA16, KB23). Each figure shows the query, its rank-1 match and its best-ranked
relevant match, as waveforms and spectra.
- Relevant matches sit at ranks 46–1,182.
- Several misleading matches are plausibly related despite the label:
  - KA16 (outer) retrieves KB24 (combined inner+outer) with very similar impulse trains
    and spectra.
  - KB27 (combined) retrieves outer-ring bearings.
  This illustrates that fault labels are only a proxy, and that **human relevance
  review is pending**.

## Limits

- One rig and one sensor.
- 4 settings.
- Labels are a proxy.
- 23 held-out groups.
- 29% of files excluded.
- A third-party copy, not yet compared with the official host, which was unreachable.

None of these favours one method over another.
