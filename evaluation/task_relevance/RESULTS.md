# E5: catalogued earthquakes, bounded-lag against zero-lag search — results

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at 2daae97 (2026-11-02) before the event list was downloaded.
One deviation: EMSC returned 181 events at download, against 191 when counted before freezing. Run on
2026-11-02 on development data only (`morlet_r2`, 234,762 windows, CX HHZ, PB01, PB02, PB06, PSGCX,
2025-03-01 to 03-14).

- **Catalogue:** EMSC, 181 events within 3° (`data/`, SHA-256 in `data/SHA256SUMS`).
- **Arrivals:** predicted P arrivals from iasp91 (ObsPy TauP).
- **Raw results:** `results/e5.json` (`e5.py`).

## Verdict

**Bounded-lag search returned more windows containing catalogued earthquakes than zero-lag search.**

| | Bounded lag (L = 30, ±3 s) | Zero lag (L = 0) | Chance |
|---|---|---|---|
| Mean P@10, primary (any catalogued event) | **0.297** | 0.213 | 0.0043 |
| Mean P@10, secondary (excluding the query's own event) | **0.295** | 0.212 | |

- **Primary:** Δ = +0.084, 95% interval [0.070, 0.098], resampling 171 events with all their station queries
  (10,000 resamples). Under the frozen rule this is **"bounded lag better"**.
- **Secondary:** Δ = +0.083 [0.069, 0.097], same verdict.
- **Against chance:** both arms are far above it, 70× and 50× the base rate of event windows.

## Details

- **Queries:** 465 (event, station) queries from 171 events (magnitude ≥ 2.5, within 2° of the station):
  141 at PB01, 136 at PB02, 130 at PB06 and 58 at PSGCX. 998 of 234,762 gallery windows are event windows.
- **Per station,** mean P@10 bounded / zero lag:

  | Station | Bounded lag | Zero lag |
  |---|---|---|
  | PB01 | 0.351 | 0.240 |
  | PB02 | 0.347 | 0.264 |
  | PB06 | 0.218 | 0.175 |
  | PSGCX | 0.226 | 0.117 |

  Bounded lag is higher at every station.
- **Overlap:** the two top tens shared 46% of their windows on average. Bounded lag does not just reorder the
  same results; it finds different windows, and more of them contain earthquakes.
- **Same machinery for both arms:** E3's exact matrix search with the product's contract (source exclusion,
  ordering, overlap suppression), on the same stored envelopes, with only the lag range differing.

## Limitations

- **Catalogue relevance is a proxy.** "Contains a catalogued earthquake's predicted P arrival" is not
  "relevant to an analyst". Small catalogued events may be invisible at a station, and uncatalogued events
  count as irrelevant. Both compress the measured difference and affect the two arms alike.
- **Scope:** one region, two weeks, one envelope representation and window size, development data. The
  held-out stations and year were not used. A held-out confirmation with the same frozen rules is the
  natural next step.
- **Absolute precision is modest** (about 0.3). Most top-ten hits contain no catalogued event; the
  comparison, not the level, is the result.
- **Exact search only.** Probe search recovers 0.99+ of the bounded-lag top ten on these data (E1, E4), so
  the gap should transfer, but that was not measured here.

## What it means for the paper (not changed here)

This is the first task-level evidence for the scorer. The paper currently says recall is fidelity to D and
that no task evaluation of probe search exists. E5 adds on development data, as a proxy task: bounded lag
finds catalogued earthquakes in 30% of top-ten results against 21% for zero lag. Before it carries a
headline, a held-out replication is worth doing.
