# Timing test result: earthquake retrieval by hypocentral distance

Plan: [`../../PLAN_TIMING.md`](../../PLAN_TIMING.md), frozen in `012bb3d` before any
2024 data was downloaded. Every stage ran at `012bb3d` with a clean tree. The 2024
held-out evaluation ran **once**.

- Data: IPOC CX HHZ (GFZ GEOFON, doi:10.14470/PK615318); catalogue USGS ComCat (public
  domain).
- Per-file sha256: `fetch-*.json`. Catalogue query URLs and hashes: `catalog-*.json`.

## Population (2024, held-out)

- 1,427 event windows from 281 events at 10 stations, plus 234 no-match windows.
- Exclusions:
  - 307 station gaps;
  - 66 empty responses (GEOFON has no data for those times);
  - 8 gap/overlap;
  - 2 clipped.
  All are in `prepare-2024.json`.
- The bootstrap resamples the 281 events (2000 resamples).

## Decisions (same-station gallery, standardized features)

Δ is the reduction in median hypocentral-distance error of the top-10 (km), averaged
over events. Positive means the first method is better.

| Claim | Δ (km) | 95% CI | Verdict (margin 5 km, provisional) |
|---|---|---|---|
| **1. Timing helps:** spectrogram with time bins (ST) vs pooled FFT + statistics (FS) | **+21.0** | [+19.6, +22.7] | **supported** |
| **2. Morlet-specific:** temporal Morlet (MT) vs ST | +1.6 | [+0.9, +2.3] | **rejected** (real but below the 5 km margin) |
| **3. Decomposition over time series:** MT vs envelope cross-correlation (XC) | **−6.0** | [−6.7, −5.3] | **rejected** (XC is better) |

## All methods (2024)

| Method | Median \|Δd\| same station (km) | P@10 same band | Any station (km) | No-match AUROC | False-accept at dev threshold |
|---|---|---|---|---|---|
| chance (random ranking) | 43.6 | 0.299 | 45.0 | — | — |
| S statistics | 38.0 | 0.347 | 39.9 | 0.99 | 0.02 |
| FS pooled FFT + statistics | 40.1 | 0.330 | 40.7 | 0.88 | 0.20 |
| M pooled Morlet | 37.7 | 0.349 | 38.4 | 0.95 | 0.05 |
| ST spectrogram × 4 time bins | 19.1 | 0.594 | 17.6 | 0.62 | 0.98 |
| MT temporal Morlet × 4 time bins | 17.4 | 0.599 | 15.8 | 0.94 | 0.12 |
| **XC envelope cross-correlation** | **11.4** | **0.758** | **10.1** | **0.99** | **0.01** |

Feature methods use standardized features; the unscaled ("frozen") rows are in
`evaluation-2024.json.gz`, and the comparisons in both variants agree in direction.
Pooled Morlet vs pooled FFT + statistics is +2.4 [+1.5, +3.4] standardized and
−0.7 [−1.9, +0.4] unscaled: a small, scaling-dependent difference.

The development year (2023) gave the same picture:
- ST vs FS: +21.0;
- MT vs ST: +1.4;
- MT vs XC: −6.2 km.

## Reading

- **Timing information matters a lot for ordered transients.** Pooled representations,
  Morlet or FFT, are barely better than chance at matching distance (38–40 km vs 44 km).
  Adding 4 coarse time bins halves the error.
- **Morlet adds little over an ordinary spectrogram** once both carry the same time bins
  (1.6 km), and a plain envelope beats both.
- **No-match recognition:** temporal Morlet keeps reasonable no-match separation
  (AUROC 0.94), while the standardized spectrogram collapses (0.62, 98% of noise windows
  accepted). Envelope cross-correlation is best at both retrieval and no-match
  rejection.
- **Cost:** XC search takes 8.8 ms per query, against ≤ 0.01 ms for vector methods, over
  this gallery. XC is a pairwise comparison and cannot use the existing vector or posting
  machinery. Its advantage would need a different index (for example matrix-profile
  techniques) at scale.

## Limits

- Windows start 5 s before the catalogue-predicted P arrival. That alignment helps all
  timing-aware methods, and XC most. A detector-based onset would add up to about 1–2 s of
  misalignment (the seismic example measured median 0.48 s, p90 1.49 s); XC's ±3 s lag
  window covers that.
- XC uses 0.1 s envelope resolution, while ST and MT have 4 bins of about 10 s. The
  frozen design compares the existing temporal Morlet layout, not the best possible
  time-frequency layout.
- Catalogue locations are labels (errors of several km), with one region, one network
  and raw counts.
- Earthquakes, not machines: this shows the mechanism on real ordered transients, not
  industrial value.
