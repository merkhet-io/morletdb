# Precommitted timing test: does *when* frequencies occur help retrieval?

Status: **DRAFT.** It freezes, by setting this line to FROZEN in a commit, after
development data (2023) has been inspected and before any held-out (2024) waveform is
downloaded. Thresholds marked **(provisional)** are researcher-selected.

## Question

Pooled spectral features (FFT bands, pooled Morlet) discard *when* energy occurs. On
the bearing data this did not matter, and the stationary signals gave it no chance to.
This test uses real transients whose signature is an **ordering**: local earthquakes.
A P wave arrives, then an S wave. The S−P delay grows with hypocentral distance (about
1 s per 8 km), while the spectra of nearby events are similar.

*Given an earthquake recorded at one station, do the top-10 retrieved windows from
**other** earthquakes at that station come from a similar hypocentral distance?*

This measures three separate claims:
1. **Timing helps:** spectrogram with time bins vs pooled FFT bands.
2. **Morlet-specific value:** temporal Morlet vs the spectrogram with time bins.
3. **Decomposition vs time series:** temporal Morlet vs envelope cross-correlation.

## Data

- **Waveforms:** IPOC (CX) network, HHZ at 100 Hz, from GFZ GEOFON FDSN.
  - Open access; cite doi:10.14470/PK615318.
  - Raw counts. No instrument response is removed, so the primary comparisons are within
    a station.
  - Stations (11): PB01, PB02, PB03, PB07, PB08, PB09, PB20, HMBCX, PATCX, PSGCX, MNMCX.
- **Catalogue:** USGS ANSS ComCat (public domain), M ≥ 2.5, within 300 km of
  (−20.8, −69.5). A station window is kept if the hypocentral distance (catalogue
  location, epicentral distance plus depth) is ≤ 250 km, so the S wave falls inside the
  window. Catalogue location errors (often 5–15 km for small events) are a label-noise
  limit, and they are the same for every method.
- **Periods:** development = 2023 (inspection, thresholds, sanity); held-out = 2024 (read
  once).
- **Windows:** 4096 samples (40.96 s, the extractor maximum), starting 5 s before the
  IASP91 (TauP) predicted P arrival. Every method uses the same window. Using catalogue
  origins to place windows is a limitation, and it applies identically to all methods.
- **No-match windows:** 300 per period. Times and stations are drawn with a fixed seed,
  at times with no ComCat M ≥ 2.5 event within 400 km in [t − 600 s, t + 120 s] and no
  global M ≥ 6 in [t − 3600 s, t + 120 s]. Uncatalogued small events can remain; this is
  a limitation.
- **Data-quality rule (provisional; finalized from 2023 inspection only):** a window is
  excluded if it has a gap or overlap, a sample rate ≠ 100 Hz, zero variance, or any
  |count| ≥ 8,000,000 (near 24-bit clipping).

## Groups, gallery and leakage

- **Independent unit = event.** An event recorded at several stations contributes
  several correlated queries. Queries are averaged per event, and the bootstrap
  resamples events.
- **Primary gallery:** same station, other events, same period.
- **Excluded from the gallery:** any event within 24 h **and** 30 km of the query event,
  so that aftershocks and repeating sequences do not trivially match.
- **Secondary gallery:** any of the 11 stations, other events, the same exclusion.

## Methods (identical windows, z-score per window, no fitted parameters in the frozen configuration)

| Id | Representation | Distance |
|---|---|---|
| S | AC RMS / crest / kurtosis (existing baseline), 3-d | RMS Euclidean |
| FS | 12 log FFT band energies (fs/64–0.4 fs, existing layout) + S, 15-d | RMS Euclidean |
| M | pooled Morlet `morlet-energy-lowrate-v1` (Rust core), 15-d | RMS Euclidean |
| **ST** | spectrogram with time bins: the same 12 bands × 4 equal time bins (Hann-windowed power, on the pooled baseline's power scale with Hann power compensated), log1p, + S, 51-d | RMS Euclidean |
| **MT** | temporal Morlet (existing experimental adapter: the same 12 bands × 4 time bins) + S, 51-d | RMS Euclidean |
| **XC** | envelope cross-correlation: \|Hilbert(z-scored window)\|, 0.5 s moving average, decimated to 10 Hz, z-normalized | 1 − max normalized cross-correlation over lags ±3 s |

ST and MT are content-matched (the same band layout, time bins and statistics), as FS
and M are.

**Feature scaling (primary):** every vector method is standardized per dimension, with mean
and SD from development-period (2023) windows.
- Reason: unscaled, the FFT band energies are about 10–50× smaller in spread than the
  statistics and the Morlet bands. In FS and ST the distance would then be dominated by
  the 3 statistics, which is a hidden scaling advantage for Morlet (found in the bearing
  test; see `results/e6bf274/RESULTS.md`).
- The unscaled configuration is reported as secondary.
- XC is scale-free.

Nothing else is tuned.

## Metrics

- **Primary:** per query, the median |Δd| (km) between the query's hypocentral distance
  and its top-10 matches' distances (lower is better). Averaged per event, then over
  events.
- **Secondary:**
  - P@10 within the same distance band [0, 80), [80, 120), [120, 160), [160, 200),
    [200, 250] km **(provisional)**;
  - the cross-station gallery;
  - breakdowns by depth (< 60 km vs ≥ 60 km) and by magnitude tercile.
- **Chance:** the expected median |Δd| of a random ranking over the same eligible
  gallery.
- **No-match:** the AUROC of nearest distance between no-match windows and event queries,
  and the false-accept rate at a development threshold (90% of development event queries
  accepted).
- **Cost:** feature time per window and search time per query, per method.
- **Uncertainty:** paired bootstrap over held-out events (2000 resamples, seed 7).

## Decision rules (held-out 2024, same-station gallery, standardized features)

Let Δ(A, B) = mean over events of median|Δd|(B) − median|Δd|(A), so positive means A is
better.

| Claim | Supported | Rejected |
|---|---|---|
| 1. Timing helps: Δ(ST, FS) | ≥ 5 km **(provisional)** and CI lower bound > 0 | CI upper bound < 5 km |
| 2. Morlet-specific: Δ(MT, ST) | same thresholds | same |
| 3. Decomposition over time series: Δ(MT, XC) | same thresholds | same |

Anything else is inconclusive. Also reported: Δ(M, FS) (H2 replication on transients),
Δ(MT, M) (timing within Morlet) and Δ(ST, XC).

## Limits known in advance

- Earthquakes, not machines. A positive result shows that the mechanism works on real
  ordered transients, not industrial value.
- Catalogue-based window placement and labels.
- Raw counts without response removal.
- One region, and distances of 250 km or less.
- The 41 s window is the extractor maximum.

## Changelog

- 2026-10-26: draft.
