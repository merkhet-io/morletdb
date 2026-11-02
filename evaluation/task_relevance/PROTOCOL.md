# E5: does bounded-lag search find catalogued earthquakes better than zero-lag search? — protocol (FROZEN)

Frozen 2026-11-02 in the commit that adds this file, before the event list was downloaded. Only the station
coordinates and the number of events per catalogue in the study region (EMSC 191, USGS 19, GEOFON 5) were
looked at. Owner approval for the catalogue download for the development period was given in chat on
2026-11-02. Deviations are appended below.

## Question

The paper measures fidelity to the scorer D: recall@10 against exhaustive bounded-lag search. It has no
evidence that bounded lag finds intervals an analyst would want. E5 asks one task-level question on
development data:

**When the query window contains a catalogued earthquake, does bounded-lag search (D with L = 30 envelope
values, ±3 s) return more windows that also contain catalogued earthquakes than zero-lag search (D with
L = 0), on the same envelopes, gallery and contract?**

## Data

- **Waveforms:** R2's development gallery `morlet_r2`: CX HHZ, PB01, PB02, PB06, PSGCX, 2025-03-01 to
  2025-03-14, 100 Hz, 234,762 windows of 4,096 samples at stride 2,048. Window times come from each
  recording's `metadata.start` and the sample offset. No held-out data and no E4 days are used.
- **Catalogue:** EMSC (`seismicportal.eu` FDSN event service), every event with origin time in
  [2025-03-01T00:00Z, 2025-03-15T00:00Z) within 3° of (−21.2, −69.8), all magnitudes. Downloaded once
  after this protocol is committed, stored with its SHA-256 and query URL.
  - **Why EMSC:** it aggregates the Chilean national network's small events; USGS and GEOFON list too few
    events here.
- **Arrival times:** predicted P arrival per event and station with ObsPy's TauP and the iasp91 model, from
  the catalogue's hypocentre and the station coordinates (`data/e5-catalog/stations.txt`, GEOFON station
  service). An event without a depth gets 10 km.

## Labels (fixed now)

- **Event window:** a gallery window of station s contains event e if the predicted P arrival of e at s lies
  in [window start + 2 s, window start + 30 s] (the window lasts 40.96 s).
- **Queries:** one per (event, station) with catalogue magnitude ≥ 2.5 and epicentral distance ≤ 2°
  (events small or far are often invisible at a station), all magnitude types as given. The query window
  is the gallery window of that station whose start is the largest grid start ≤ t_P − 5 s, if that window
  is stored (data gaps skip the query). Queries are deduplicated by window.
- **Relevant hit (primary):** a returned window that is an event window for any catalogued event, at any
  station.
- **Relevant hit (secondary):** the same, excluding the query's own event, so that matches of the same
  earthquake at other stations do not count.

## Arms (all exact; the same contract)

- **BL:** bounded-lag search, L = 30 (the product default, `max_lag_fraction` 0.075).
- **ZL:** zero-lag search, L = 0, on the same envelopes, with the same exclusion, ordering and overlap
  suppression.
- **Chance:** the share of event windows among all eligible windows (the expected precision of a random
  top ten).

Both scored arms use E3's validated exact matrix implementation (`evaluation/strong_exact/e3.py`, identical
lists to the product's exact search in E3 and E6), with L set per arm. Top ten per query, source-overlapping
windows excluded.

## Metrics and decision rule

- **Per query:** P@10 for each arm, primary and secondary relevance.
- **Primary:** Δ = mean P@10(BL) − mean P@10(ZL), primary relevance. 95% bootstrap interval with resampling
  of **events**, not queries (all of an event's station queries together), 10,000 resamples, seed 20261102.
  - "Bounded lag better" if the whole interval is > 0.
  - "Zero lag better" if it is < 0.
  - Otherwise "no difference demonstrated".
- **Reported:** the same for secondary relevance; each arm against chance; the per-station breakdown; the
  number of queries and events; how often the two top tens overlap.
- **Too few queries:** with fewer than 30 queries, the result is reported as underpowered and no verdict is
  drawn.

## Known limits (stated now)

- **Catalogue relevance is a proxy.** Small catalogued events may be invisible at a station (false
  positives in the labels), and uncatalogued events are labelled irrelevant (false negatives). Both affect
  the two arms alike, but they compress the difference.
- **One region, two weeks, one representation and window size, development data.** A positive result is
  evidence for this proxy task on these data only.
- **No arm or label rule is changed after the first result.** Analyses beyond this protocol are labelled
  exploratory.

## Deviations

1. **Catalogue size changed between counting and downloading.** The count query before freezing returned
   191 events; the download about an hour later, with the same region and period, returned 181
   (`data/e5-catalog/emsc_events.txt`, SHA-256 `34105a8a…`). EMSC revises its catalogue continuously. The
   downloaded file is the frozen catalogue for E5.
