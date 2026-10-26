# R1b confirmation (2022): compact materialized envelopes

**Decision under the frozen rules: CONTINUE.** Half-precision envelopes (`f16`),
stored as `bytea` next to lossless raw chunks:
- return the same top-10 as decoding raw (recall@10 0.9999);
- run at 0.012× its p95 latency;
- take 12.9% of the raw chunk table.

Compact materialized envelopes become a product feature to build.

- **Protocol:** [../../PROTOCOL.md](../../PROTOCOL.md), frozen at `bfdf6fe` before
  any 2022 data was downloaded.
- **Selection:** `f16` was selected on 2023 (development) by the frozen rule.
- **Run:** one run of commit `bfdf6fe` (clean tree) on 2026-10-26. 2022 had not
  been used by any earlier experiment in this repository.
- **Environment:** Apple M5 Max, 128 GB, macOS 27.0.1; PostgreSQL 17.11 (pgrx),
  pg_morlet 0.2.0 release build; Python 3.12 with psycopg over localhost TCP.
  - Load average was 5.8 before the timed pass and 4.6 after.
  - The W1 indicator job ran at nice 19 during part of this run.

## Data

- 1,826 windows: 1,557 event windows from 284 events, and 269 no-match windows.
  CX HHZ, 10 stations.
- 1,557 queries.

## Decision rules applied to the selected candidate (`f16`)

| Rule (frozen) | f16 | Pass |
|---|---|---|
| recall@10 vs E ≥ 0.99 | 0.9999 (identical set 0.999, identical order 0.987) | yes |
| p95 latency ≤ 0.25 × E | 16 ms vs 1324 ms (0.012×) | yes |
| table ≤ 25% of the raw chunk table | 12.9% | yes |
| relevance: upper 95% CI of S − E ≤ +1 km | +0.001 [−0.002, +0.004] | yes |

## All candidates (reported against the same thresholds; they do not change the decision)

| Candidate | Table vs raw | recall@10 | Identical order | p50 / p95 ms | Bytes per query | Δ km [CI] |
|---|---|---|---|---|---|---|
| E (raw decode) | — | — | — | 941 / 1324 | 5.72 MB | — |
| f4 | 0.281 | 1.000 | 1.000 | 12 / 14 | 0.32 MB | 0.000 |
| **f16** | **0.129** | **0.9999** | **0.987** | **13 / 16** | **0.17 MB** | **+0.001 [−0.002, +0.004]** |
| u8 | 0.072 | 0.998 | 0.845 | 13 / 17 | 0.11 MB | +0.012 [−0.006, +0.037] |
| u8-d20 | 0.042 | 0.952 | 0.067 | 12 / 16 | 0.07 MB | −0.021 [−0.096, +0.056] |

Bytes include the query's own raw window (32 kB), which every method fetches.

- `u8` would also pass all four rules on 2022. It was not selected: on 2023 it
  failed the identical-order bar, and it reorders results in 15% of queries.
- `u8-d20` changes the matcher, not just its precision, and agrees with E on
  only 57% of top-10 sets.

## Storage and generation (`f16`)

| | Value |
|---|---|
| Payload per window | 820 B |
| Table (heap + TOAST + primary key) | 1,761,280 B for 1,826 windows (965 B per window) |
| Raw chunk table | 13,697,024 B (7,501 B per window) |
| Envelope CPU per window | 0.19 ms |
| Encode | < 0.01 ms |
| Insert | 0.33 ms |

This envelope CPU time (0.19 ms) is lower than R1's 2025 figure (0.98 ms) for the
same computation. R1 ran under heavy background load; the difference was not
investigated, so neither figure is a precise cost.

## Findings beyond the decision

- **The client path dominated R1's M latency.** R1 returned envelopes as `real[]`
  (161 ms p95 in 2023). This run returns them as `bytea` and reads them straight
  into NumPy (16 ms p95), with the same database work. A product API should return
  compact binary representations.
- **Page packing, not schema, set R1's storage.** `f4` as `bytea` without the
  representation column is still 28%. Halving the payload to `f16` more than halves
  the table, because rows then pack eight to an 8 kB page instead of four.
- **The decision is stable across periods.** On 2023 (development), `f16` had
  recall 0.9998, identical order 0.993 and 12.8% storage, close to 2022.

## Caveats

- One window per recording, catalogue-placed windows, one machine, no concurrency
  and no true disk-cold measurement. All data fits in memory.
- Agreement is measured against envelope cross-correlation on decoded raw (E),
  the reference matcher. Relevance uses the catalogue's hypocentral distance,
  with its location errors.
- This is seismic event retrieval. For machine data the same mechanism applies
  (a small derived representation next to raw), but the representation and its
  storage bound would need their own check.

## Reproduce

```sh
python evaluation/materialized/fetch_windows.py 2022 data/r1b-runs/conf data/seismic-r1
python evaluation/materialized/r1.py load --year 2022 --windows data/r1b-runs/conf --out data/r1b-runs/conf
python evaluation/materialized/compact/compact.py derive --year 2022 --out data/r1b-runs/conf
python evaluation/materialized/compact/compact.py run    --year 2022 --out data/r1b-runs/conf
```
