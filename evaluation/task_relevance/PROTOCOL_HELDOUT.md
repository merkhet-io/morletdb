# E5-H: held-out replication of E5 — protocol (FROZEN)

Frozen 2026-11-02 in the commit that adds this file, before the held-out event list was downloaded. Only
the held-out stations' coordinates were looked at. E5's development result is known (bounded lag 0.297
against zero lag 0.213 at P@10). Owner approval for the catalogue download was given in chat on 2026-11-02.
Deviations are appended below.

## Question

E5's question, once, on data no tuning has touched: on held-out stations and year, does bounded-lag search
return more windows containing catalogued earthquakes than zero-lag search?

## Identical to E5 (`PROTOCOL.md`)

Unchanged:
- **Code:** `e5.py`, unmodified.
- **Label rule:** predicted P 2–30 s into the window.
- **Query rule:** M ≥ 2.5, within 2° of the station, P about 5 s into the query window.
- **Arms:** exact search with L = 30 against L = 0, same contract.
- **Metric:** P@10, primary and secondary relevance.
- **Bootstrap:** by event, 10,000 resamples, seed 20261102.
- **Decision rule:** whole interval > 0 ⇒ "bounded lag better", < 0 ⇒ "zero lag better", else "no difference
  demonstrated"; fewer than 30 queries ⇒ underpowered.

## Different from E5 (data only)

- **Waveforms:** the held-out gallery `morlet_bl1c` (CX HHZ, PB05, PB11, PB14, PB18, 2021-03-01 to
  2021-03-14, 234,622 windows). This is the database BL1-C and E1 used. Its waveforms have been searched
  before; its earthquake labels have not.
- **Catalogue:** EMSC, origin times in [2021-03-01T00:00Z, 2021-03-15T00:00Z), within 5.6° of the
  held-out stations' centroid (−21.21, −69.94). The radius is the farthest station's distance from the
  centroid (3.6°) plus E5's 2° query limit, so every event that can produce a query is included.
  - **Why the radius differs from E5's 3°:** the held-out stations span 7° of latitude. Downloaded once,
    stored with its SHA-256 and query URL.

## Use of the held-out data

One run. No arm, label rule or parameter is changed after it; anything further is labelled exploratory.

## Deviations

(none yet)
