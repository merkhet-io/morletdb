# Search capabilities, costs and evidence (inventory, 2026-10-31)

One page of what exists, what it costs and what is measured, as the baseline for the
search-storage work. Numbers are from committed results; "not measured" means exactly that.
Environment for all timings: Apple M5 Max, macOS, PostgreSQL 17.11 (pgrx), pgvector 0.8.0,
one client over localhost, warm caches.

## Stored structures (per 4096-sample window, stride 2048, CX seismic)

| Structure | Bytes per window (relation, incl. row overhead) | Relative to compressed raw | Required for |
|---|---|---|---|
| Raw chunks (lossless) | — | 1.0 (6.2 bits/sample on R2 data, 8.6 on BL1-C data) | everything |
| Envelope table (`bytea`, float16 + version byte; 825 B column) | 951 | 0.60 (R2), 0.43 (BL1-C) | exact search, re-rank |
| Probe table (`halfvec(410)`; 828 B column) | 1,024 | 0.65 (R2), 0.47 (BL1-C) | probe search |
| Inner-product HNSW index (m 16, ef_construction 64) | 1,171 | 0.74 (R2), 0.53 (BL1-C) | probe search |

- The envelope and probe tables hold the same envelopes twice: 98.3% of float16 values are
  bit-identical; the rest differ by one float16 step because probe rows are made from the
  decoded and re-z-scored values (measured on 3,000 R2 windows).
- Payload vs relation: envelope payload 821 B; relation bytes include tuple headers, the
  primary key index and TOAST bookkeeping (R2: payload 193 MB, relation 223 MB).

## Search paths (234,622–234,762 windows, unfiltered, 100 queries)

| Path | Recall@10 vs exact | p50 / p95 | Source |
|---|---|---|---|
| Exact, PL/pgSQL scan (`morlet_search_envelope_interval`) | 1 | 2,834 / 2,854 ms | BL1-C |
| Exact, one parallel SQL statement (3 workers; experiment harness) | 1 | 698 / 713 ms | BL1-C |
| Probe search, experiment harness (client probes) | 0.976 | 97 / 131 ms | BL1-C (held out) |
| Probe search, `morlet_search_envelope_probes` | 0.977 | 37 / 50 ms | product check on BL1-C queries |

- Exact search costs about 12 µs per eligible window; it is interactive (≤ 100 ms) up to
  roughly 8,000 windows (interpolated, R2).
- A faster exact path inside the extension is in progress (separate PR).

## Ingestion and visibility (BL1 Stage B, development data, 4 channels, one day)

- Synchronous maintenance (envelopes + probe rows in the append transaction): 919k
  samples/s; a window is searchable at the commit that completes it.
- Background worker: ~200 windows/s per worker; p95 0.09 s delay at 200× real time.
- Duplicate replay is a no-op; gaps are rejected; worker kill/restart left every window
  exactly once.

## Known limits

- **Filters:** probe search applies eligibility after probing; selective metadata filters
  lower recall (not measured yet). Exact search handles filters exactly.
- **Deletes:** probe rows cascade with their envelopes; HNSW deletion and rebuild behaviour
  not measured.
- **Access control:** all functions are `SECURITY INVOKER`; row-level security on the probe
  table is not tested.
- **Cold cache / memory pressure:** not measured. Clearing the OS page cache needs root on
  this machine, so uncontrolled restarts are not reported as cold.
- **Upgrades:** 0.1.0-alpha.3, 0.2.0 and 0.3.0 upgrade paths pass the catalog-equality checks.
- **Relevance:** all retrieval numbers are fidelity to the exact scorer; usefulness is
  supported only by a seismic catalogue-distance proxy and sleep staging.
