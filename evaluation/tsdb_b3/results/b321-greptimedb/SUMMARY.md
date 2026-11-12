# B3.21: GreptimeDB 1.3.0 beside MorletDB 0.3.12-alpha.1 — results (UNPUBLISHED, local)

Run 2026-11-12 23:52 – 2026-11-13 00:11 under amendment B3.21 (frozen in `11e9483` before any load of the benchmark
set), benchmark set: 124 Paderborn acquisitions, 101,586,735 samples. Every one of the 12 loads passed the full
correctness check (counts, every channel bit for bit with exact times).

| Measure | MorletDB (`morletdb-i1-sum`) | GreptimeDB | Statement (B3's rule) |
|---|---|---|---|
| Ingestion D2, samples/s (r1, r2, r3) | 14.47 M, 14.61 M, 14.59 M | 0.78 M, 0.80 M, 0.78 M | MorletDB faster, about 18x, in every load |
| Ingestion D1, samples/s | 14.24 M, 14.09 M, 14.19 M | 0.79 M, 0.79 M, 0.77 M | MorletDB faster, about 18x, in every load |
| Stored data after settling (D2 r1) | 171.3 MB database (138.7 MB encoded chunks) | 208.5 MB Parquet | MorletDB 18 % smaller |
| Whole volume (with WAL) | 379.2 MB (pg_wal 184.5 MB) | 1,155.6 MB (WAL 944.9 MB) | MorletDB 3.0x smaller |
| R1 4,096 samples, p50 / p95 ms | 0.65 / 1.21 | 2.22 / 2.93 | MorletDB faster (both) |
| R2 128,000 samples | 6.19 / 9.04 | 14.23 / 19.17 | MorletDB faster |
| R3 192,000 samples | 8.08 / 12.40 | 13.13 / 18.27 | MorletDB faster |
| R4 recent / older (4,096) | 0.64 / 1.00 ; 0.59 / 1.00 | 1.92 / 2.51 ; 2.22 / 2.91 | MorletDB faster |
| R5, 4 threads | 0.85 / 1.68 | 3.14 / 4.56 | MorletDB faster |
| Interval statistics (R2 window) | 0.45 / 0.50 | 2.31 / 2.65 | MorletDB faster |

- Statistics agree with numpy within 4e-16 (MorletDB) and 2e-15 (GreptimeDB) relative.
- Retries (subset, development set): GreptimeDB accepts an identical retry without duplicating and **replaces a stored
  value on an altered retry** (last write wins); MorletDB refuses the altered retry.
- GreptimeDB kept 945 MB of WAL on the volume after flush and compaction (its WAL is purged later by its own
  schedule); the Parquet bytes settled 31 s after the flush.
- Telemetry was off for every GreptimeDB load (read back from `/config`).
- Reads, statistics and storage are from D2 r1 of each arm, reads from a fresh client process; one read session per
  system, so latency statements rest on that session (as in B3.18).
