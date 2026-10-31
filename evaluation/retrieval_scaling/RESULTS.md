# R2 results: exact lag-tolerant search grew linearly over the measured range; a zero-lag top-K shortlist missed the recall bar (HNSW was not exercised)

Run once on 2026-10-31 as frozen in [PROTOCOL.md](PROTOCOL.md):
- protocol committed at 12cad82, before downloading;
- harness at 353f79d; one restart of the load's last step logged in the protocol (2185815).

Data: CX HHZ, PB01, PB02, PB06, PSGCX, 2025-03-01 to 03-15, from GEOFON, downloaded as continuous
station-days for R2. **Correction (found during R2-D):** the protocol called this data never
read by an earlier study. R1's 2025 evaluation had downloaded 48 short event and noise
windows (about 5,050 s, 0.1% of R2's samples) inside this slice at these stations. R2 selects
no model or parameter, so the overlap cannot bias its measurements, but the data is not
strictly unread. Apple M5 Max, PostgreSQL 17.11 (pgrx), pg_morlet 0.3.1 at this branch, pgvector
0.8.0, one client over localhost, warm.

**Decision as computed: prefilter NOT supported.** At 234,762 windows the best shortlist
(K′ = 1000) reached recall@10 0.743 against the exact search, far below the 0.95 bar.

> **Correction (found in [R2-D](diagnostic/RESULTS.md)):** the H arms never used the HNSW
> index. Their query vector came from a subquery, which pgvector cannot use as an index
> ordering key, so PostgreSQL ran a sequential scan and an exact sort. **H-100 and H-1000
> were exact zero-lag shortlists**, and Z was exact zero-lag retrieval. The recall numbers
> below therefore measure the zero-lag candidate metric, not ANN approximation, and the
> latencies are those of a sequential scan, not of HNSW. R2 says nothing about HNSW.
> R2-D measures the index separately.

## Load and storage

| | |
|---|---|
| Samples, recordings | 481,214,308; 128 (station × day × gap-free segment) |
| Raw append | 451 s (1.07 M samples/s, one connection, 4,096-sample chunks) |
| Raw chunk table | 371 MB (6.17 bits/sample) |
| Envelopes (4,096 / stride 2,048) | 234,762 windows in 275 s (853 windows/s); table 223 MB (0.60× raw) |
| pgvector side table / HNSW index | 241 MB / 275 MB; HNSW build 37 s (m 16, ef_construction 64) |

- **Envelopes are 60% of the raw table here, against 12.9% in R1b.** Measured payload and
  relation sizes account for the factor of 4.68 (0.602 / 0.129):

  | | R1b (2022 event windows) | R2 (continuous) | factor on the ratio |
  |---|---|---|---|
  | samples per stored envelope (stride) | 4,096 (one per window) | 2,048 | ×2.00 |
  | raw payload, bits/sample | 12.39 | 5.04 | ×2.46 |
  | raw table / raw payload (row, TOAST, index overhead) | 1.183 | 1.224 | ×0.97 |
  | envelope table bytes per window (821 B payload) | 965 | 951 | ×0.99 |

  Half-overlap alone doubles the envelope density; most of the rest is that the
  continuous, mostly ambient recordings compress 2.5× better than R1b's event windows, so
  the same 821-byte envelope is larger relative to them. The ratio therefore depends on
  stride, on how well the raw data compresses, and on the physical layout.
- **Which structures coexist.** The benchmark holds four: raw chunks (371 MB, required),
  pg_morlet envelopes (223 MB, used by A and by every re-rank), the halfvec side table
  (241 MB) and its HNSW index (275 MB), the last two intended for the prefilter arms (the index went unused, see the correction). The
  side table stores the same float16 values as the envelope table (P1 found identical
  sizes), so a deployment could keep one of the two if its scorer read that format;
  pg_morlet's scorer currently reads the `bytea` envelopes. The HNSW index is needed only
  if a prefilter is used. Side table plus index are 1.4× the raw samples.

## Search

| Tier | Windows | A exact p50 / p95 ms | H-100 (exact zero-lag shortlist) recall / p50 ms | H-1000 (same) recall / p50 ms | Z (exact zero-lag) recall |
|---|---|---|---|---|---|
| 0 | 4,217 | 54 / 54 | 0.714 / 54 | 0.977 / 232 | 0.468 |
| 1 | 16,868 | 209 / 211 | 0.638 / 61 | 0.866 / 240 | 0.447 |
| 2 | 67,472 | 858 / 866 | 0.612 / 74 | 0.755 / 255 | 0.425 |
| 3 | 234,762 | 2,832 / 2,858 | 0.605 / 96 | 0.743 / 277 | 0.451 |

Recall is against A, as sets of (recording, start), mean over 100 queries (50 random, 50
energetic). Every A result had 10 hits.

- **Exact search grew linearly over the measured range** (4.2k–235k windows): 12.1–12.8 µs
  per stored window at every tier (p50 / windows). Linear interpolation between tiers 0
  (54 ms) and 1 (209 ms) puts the 100 ms bound at about 8,000 windows; no gallery of that
  size was measured.
  This is one scan in Rust per candidate with 61 lags of a 410-value envelope.
- **Zero-lag cosine agrees with lag-tolerant search on fewer than half the top ten**
  (0.43–0.47), lower than the 64.7% of P1 on event windows.
- **The zero-lag shortlist degrades with gallery size.** With K′ = 1000 it keeps 98% of
  the exact top ten among 4,217 windows, where 1000 candidates are a quarter of the
  gallery, but 74% among 234,762. The lag-tolerant neighbours are not concentrated near
  the zero-lag ones, so a fixed K′ captures a shrinking share of them.
- **Query type hardly matters:** random and energetic queries differ by at most 0.06 in
  recall for any arm and tier.
- **Shortlist latency** (descriptive): H-1000 took 230–280 ms at every tier. Its path was
  a sequential scan of the whole side table, a sort and an in-database re-rank of 1000
  envelopes, not an index scan; the earlier attribution to `ef_search` was wrong.

## What this means for the paper and the product

- On this machine and data, the exhaustive in-database scorer was exact with latency
  proportional to gallery size: tens of milliseconds for filtered galleries of a few
  thousand windows, seconds at a quarter million.
- An exact zero-lag shortlist of 1000 with exact re-ranking did not reach the recall bar:
  the loss is from the zero-lag candidate metric at this budget. ANN approximation was not
  exercised by R2; [R2-D](diagnostic/RESULTS.md) measures it.
  Other first stages (shift-invariant keys, lag-pooled features, larger budgets, other
  indexes) are possible and untested.

## Limits

- One machine, one client, warm cache; no concurrent or cold runs.
- The HNSW index was built (37 s, 275 MB) but never used by the queries (see the
  correction above). The Z arm came from that exact, sorted candidate list, so it was
  in fact exact zero-lag retrieval; R2-D confirms ZE = ZH = Z.
- Fidelity only: no relevance labels on continuous noise windows.

## Reproduce

```sh
.venv-seismic/bin/python -I evaluation/retrieval_scaling/fetch.py data/cx-scale
createdb -h localhost -p 28817 morlet_r2
MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/retrieval_scaling/load.py data/cx-scale evaluation/retrieval_scaling/results
MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/retrieval_scaling/bench.py evaluation/retrieval_scaling/results
```

`results/load.json` (sizes, build times) and `results/bench.json` (per-query result lists,
recall, latency, decision) are committed. Data checksums: `data/cx-scale/SHA256SUMS` and
the per-file SHA-256 in `manifest.json` (not committed).
