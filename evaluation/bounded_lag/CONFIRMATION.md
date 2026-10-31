# BL1-C: independent confirmation of the shifted-probe configuration

Status: **FROZEN** in the commit that adds this file, **before any confirmation data is
downloaded**. Amendments are appended and committed before what they change is run.

## Frozen method (selected on development data in Stage A, not changed)

**P-G13-50:** 13 zero-padded shifted query probes at lags −30, −25, …, 30, each
`ORDER BY v <#> probe LIMIT 50` on an HNSW `halfvec_ip_ops` index (m 16, ef_construction 64,
`ef_search` 100, `iterative_scan = relaxed_order`), one `UNION ALL` statement, union
deduplicated, exact re-rank with `morlet_envelope_distance` in the database, exclusion and
greedy overlap suppression, top 10. The scorer contract is the one in `PROTOCOL.md`.

## Data ledger audit (2026-10-31)

CX HHZ data read by earlier studies in this repository: stations PB01, PB02, PB03, PB06,
PB08, PB09, PB15, PB16, PSGCX, MNMCX, in 2022 (R1b), 2023 (timing, compression, R1
development), 2024 (timing, compression held-out, H3, decision) and 2025 (R1 evaluation;
R2/R2-D/BL1 development). No CX data before 2022 and no other CX station has been read
(searched: all station codes and dates in `evaluation/`, `docs/`, `data/*/manifest.json`).

**Confirmation slice:** CX HHZ at **PB05, PB11, PB14, PB18**, **2021-03-01T00:00 to
2021-03-15T00:00**, from GEOFON (open). Chosen from the GEOFON availability service alone
(2026-10-31): of the never-used stations with complete extent for the period, the four with
the fewest time spans (PB14: 1; PB05, PB11, PB18: 2; ties broken alphabetically over
PB05, PB11, PB18, PB19). No waveform was inspected. About 0.4 GB miniSEED.

## Set-up (as R2)

Recordings per station, UTC day and gap-free segment; quantum 1; envelopes with window
4096, stride 2048; `halfvec` side table; the inner-product HNSW index only (no cosine index).
Gallery: every window (no metadata filter). Database `morlet_bl1c`.

**Queries:** 100 windows of day 1, chosen by R2's rule: 50 uniform random
(`default_rng(2021)`), 50 highest raw RMS without overlap.

## Arms

- **A:** exhaustive `morlet_search_envelope_interval` (reference).
- **X8:** the exact parallel SQL scan, `max_parallel_workers_per_gather = 8`,
  `plan_cache_mode = force_custom_plan`.
- **P-G13-50:** the frozen method, plan-checked (13 index scans).
- Descriptive only: PX-G13-50 (exact probes) and EZ (zero-lag, same budget).

## Confirmation criteria (all must hold for "confirmed")

1. P-G13-50 mean recall@10 against A ≥ 0.95;
2. P-G13-50 p95 ≤ A's p95 / 3;
3. P-G13-50 p95 < X8's p95;
4. side table + inner-product index ≤ 1.5× the raw chunk table.

Otherwise the method is reported as **not confirmed**, with the measured values. Each
query runs once untimed, then once timed; one machine, one client, warm.
