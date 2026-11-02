# E5-H: held-out replication of E5 — results

Protocol: [PROTOCOL_HELDOUT.md](PROTOCOL_HELDOUT.md), frozen at 8c0c4ba (2026-11-02) before the held-out
event list was downloaded. Run once on 2026-11-02 with `e5.py` unchanged, on `morlet_bl1c` (CX HHZ, PB05,
PB11, PB14, PB18, 2021-03-01 to 03-14, 234,622 windows).

- **Catalogue:** EMSC, 150 events within 5.6° of the stations' centroid (`data_heldout/`, SHA-256 in
  `data_heldout/SHA256SUMS`).
- **Raw results:** `results/e5_heldout.json`.

## Verdict

**Replicated: bounded-lag search returned more windows containing catalogued earthquakes than zero-lag
search on held-out stations and year.**

| | Bounded lag (L = 30) | Zero lag (L = 0) | Chance |
|---|---|---|---|
| Mean P@10, primary | **0.139** | 0.060 | 0.0034 |
| Mean P@10, excluding the query's own event | **0.137** | 0.059 | |

- **Primary:** Δ = +0.078, 95% interval [0.054, 0.105], from 10,000 resamples of the 92 query events.
  **"Bounded lag better"**.
- **Secondary:** Δ = +0.078 [0.054, 0.104], same verdict.

## Details

- **Queries:** 116 from 92 events. Per station, mean P@10 bounded / zero lag:

  | Station | Queries | Bounded lag | Zero lag |
  |---|---|---|---|
  | PB05 | 30 | 0.080 | 0.027 |
  | PB11 | 51 | 0.182 | 0.078 |
  | PB14 | 14 | 0.221 | 0.121 |
  | PB18 | 21 | 0.062 | 0.024 |

  Bounded lag is higher at every station. 798 of 234,622 windows are event windows.
- **Overlap:** the two top tens shared 49% of their windows on average.

## Compared with development data (E5)

| | Development (E5) | Held-out (E5-H) |
|---|---|---|
| Queries / events | 465 / 171 | 116 / 92 |
| P@10 bounded / zero lag | 0.297 / 0.213 | 0.139 / 0.060 |
| Difference [95% interval] | +0.084 [0.070, 0.098] | +0.078 [0.054, 0.105] |
| Ratio bounded / zero lag | 1.4 | 2.3 |

The absolute difference is almost the same. Precision is lower in both arms on the held-out data, which has
fewer catalogued events near its stations and some stations farther from them.

## Limitations

E5's limitations apply unchanged:
- catalogue relevance is a proxy;
- one region and two weeks per data set;
- one representation;
- exact search only.

The held-out waveforms had been searched before (BL1-C, E1), but their earthquake labels had not; no rule
was changed after E5's result.
