# R2 results: exact lag-tolerant search scales linearly; a zero-lag HNSW prefilter is not sufficient

Run once on 2026-10-31 as frozen in [PROTOCOL.md](PROTOCOL.md):
- protocol committed at 12cad82, before downloading;
- harness at 353f79d; one restart of the load's last step logged in the protocol (2185815).

Data: CX HHZ, PB01, PB02, PB06, PSGCX, 2025-03-01 to 03-15, from GEOFON. No earlier study had
read it. Apple M5 Max, PostgreSQL 17.11 (pgrx), pg_morlet 0.3.1 at this branch, pgvector
0.8.0, one client over localhost, warm.

**Decision: prefilter NOT supported.** At 234,762 windows the best prefilter (K′ = 1000)
reaches recall@10 0.743 against the exact search, far below the 0.95 bar, although at 0.10×
its p95 latency.

## Load and storage

| | |
|---|---|
| Samples, recordings | 481,214,308; 128 (station × day × gap-free segment) |
| Raw append | 451 s (1.07 M samples/s, one connection, 4,096-sample chunks) |
| Raw chunk table | 371 MB (6.17 bits/sample) |
| Envelopes (4,096 / stride 2,048) | 234,762 windows in 275 s (853 windows/s); table 223 MB (0.60× raw) |
| pgvector side table / HNSW index | 241 MB / 275 MB; HNSW build 37 s (m 16, ef_construction 64) |

- **Envelopes are 60% of raw here, not the 12.9% of R1b.** R1b stored one envelope per
  non-overlapping event window; the default stride of half a window stores every sample
  in two envelopes, and these counts compress to 6.2 bits/sample, so 821-byte envelopes
  per 2,048 samples weigh relatively more.
- **The ANN index costs more than the data it indexes:** side table plus HNSW take 516 MB,
  1.4× the raw samples.

## Search

| Tier | Windows | A exact p50 / p95 ms | H-100 recall / p50 ms | H-1000 recall / p50 ms | Z (zero-lag) recall |
|---|---|---|---|---|---|
| 0 | 4,217 | 54 / 54 | 0.714 / 54 | 0.977 / 232 | 0.468 |
| 1 | 16,868 | 209 / 211 | 0.638 / 61 | 0.866 / 240 | 0.447 |
| 2 | 67,472 | 858 / 866 | 0.612 / 74 | 0.755 / 255 | 0.425 |
| 3 | 234,762 | 2,832 / 2,858 | 0.605 / 96 | 0.743 / 277 | 0.451 |

Recall is against A, as sets of (recording, start), mean over 100 queries (50 random, 50
energetic). Every A result had 10 hits.

- **Exact search is linear:** 12.1–12.8 µs per stored window at every tier (p50 / windows).
  The 100 ms interactive bound is crossed at about 8,000 windows, between tiers 0 and 1.
  This is one scan in Rust per candidate with 61 lags of a 410-value envelope.
- **Zero-lag cosine agrees with lag-tolerant search on fewer than half the top ten**
  (0.43–0.47), lower than the 64.7% of P1 on event windows.
- **The prefilter degrades with gallery size.** With K′ = 1000 it keeps 98% of the exact
  top ten among 4,217 windows, where 1000 candidates are a quarter of the gallery, but
  74% among 234,762. The lag-tolerant neighbours are not concentrated near the zero-lag
  ones, so a fixed K′ captures a shrinking share of them.
- **Query type hardly matters:** random and energetic queries differ by at most 0.06 in
  recall for any arm and tier.
- **Prefilter latency** (descriptive): H-1000 takes 230–280 ms at every tier, dominated by
  `ef_search = 1000`. At tier 0 the filtered HNSW scan (2% of rows qualify) makes H-100
  as slow as the exact scan.

## What this means for the paper and the product

- An exhaustive in-database scorer is exact and predictable but linear: fine for filtered
  galleries of a few thousand windows (one station, one day), seconds at a quarter
  million.
- A standard vector index over the same representation does not rescue it: the zero-lag
  metric ranks too differently. Sublinear lag-tolerant search needs a shift-invariant
  key (for example, magnitude spectra or lag-pooled features as a first stage), which
  would need its own frozen evaluation.

## Limits

- One machine, one client, warm cache; no concurrent or cold runs.
- One HNSW configuration (frozen); larger `m` or `ef_construction` might raise recall,
  but not past the zero-lag ranking limit Z shows.
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
