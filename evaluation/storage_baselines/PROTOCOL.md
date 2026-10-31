# B1: lossless storage against modern float and integer baselines on identical samples

Status: **FROZEN** in the commit that adds this file, before any B1 measurement.
- **Inspected so far:** only file headers (PTB-XL `.hea`, Sleep-EDF EDF headers) and the
  P2 dump format. No B1 size has been computed.
- **Not a held-out test:** the P2 inputs were measured before (P2, offset grid). Storage
  sizes are deterministic, so reuse risks selection of datasets, not overfitting of a
  model. Two inputs are new to the storage studies: a Sleep-EDF slice and PTB-XL.

## Question

The paper draft compares pg_morlet's raw codec only with TimescaleDB layouts. B1 asks,
on identical samples and one machine:

1. On **calibrated floats** (values = counts × step, possibly + offset, rounded to
   float64), how does pg_morlet, given the nominal quantum, compare with float codecs that
   need no metadata: ALP (via DuckDB), Parquet BYTE_STREAM_SPLIT + zstd, and zstd?
2. How far is pg_morlet from codecs given the **exact integer counts** (FLAC, Parquet
   DELTA_BINARY_PACKED + zstd, DuckDB BIGINT)?

## Inputs (fixed now)

| Id | Input | Samples | Float form | Quantum given to pg_morlet | Integer form |
|---|---|---|---|---|---|
| cx | CX seismic counts (P2 dump) | 4,194,304 | counts as float | 1 | counts |
| ims | IMS three-decimal vibration (P2 dump) | 4,194,304 | value | 0.001 | value × 1000 |
| pad-c | Paderborn ADC counts (P2 dump) | 4,194,304 | counts as float | 1 | counts |
| pad-p | Paderborn physical floats (P2 dump) | 4,194,304 | `.mat` floats | nominal 100/32768 | ADC counts |
| sleep | Sleep-EDF Fpz-Cz, new slice | 4,194,304 | EDF physical | half the EDF step (loader rule) | EDF digital |
| ecg | PTB-XL 1.0.3 `records100`, lead I, new | 4,194,000 | digital / gain (gain 1000) | 0.001 | digital |

- **P2 dumps:** regenerated with `evaluation/plain_postgres/p2_dump.py` (first 1,024
  held-out chunks of 4,096 samples).
- **Sleep slice:** the 153 Sleep-EDF cassette PSG files sorted by name; from each of the
  first 32, the 32 chunks of 4,096 samples starting at sample 360,000 (one hour in).
  Physical values with `s1.read_signal`. Symmetric and asymmetric calibration ranges both
  occur; their counts are reported.
- **ECG:** the first 4,194 records of `records100` in numeric order, lead I, one chunk per
  record (1,000 samples). Physical = digital / 1000 in float64.

## Arms (fixed now)

All bits/sample = 8 × bytes / samples, lossless, verified bitwise (floats) or exactly
(integers) after decoding. Every arm sees the same samples in the same order.

**Float input, no metadata:**
- **Z:** zstd level 19 on each chunk's float64 bytes.
- **PQ-f:** one Parquet file, float64 column, BYTE_STREAM_SPLIT + zstd level 19, no
  dictionary, one row group, pyarrow.
- **ALP:** DuckDB `DOUBLE` column, automatic compression (ALP or ALP-RD), database with
  `BLOCK_SIZE 16384`, bytes = used blocks × block size after `CHECKPOINT`, minus the same
  measure for an empty table. The chosen compression per segment is reported.

**Float input + quantum:**
- **M:** pg_morlet `rawcodec` (crate `morlet-core` at this commit) via
  `examples/rawcodec_bench.rs`, the quantum above, chunks as above. Payload only (no
  table overhead; P2 already reports table bytes for the four P2 inputs).

**Integer input:**
- **FLAC:** pyflac `StreamEncoder`, compression level 8, block size 4,096, one stream per
  input. Only if every count fits 24 bits; otherwise reported as not applicable.
- **PQ-i:** Parquet int64 column, DELTA_BINARY_PACKED + zstd level 19, one row group.
- **DDB-i:** DuckDB `BIGINT` column, automatic compression, measured as ALP.
- **M-i:** pg_morlet on the counts as floats with quantum 1 (the ceiling of what grid
  knowledge can buy).

## Primary comparisons (decided now)

1. **Calibrated floats (pad-p, sleep):** ratio M / best of {Z, PQ-f, ALP}.
   - Claim "**pg_morlet with a quantum is at least 2× smaller than metadata-free float
     codecs on calibrated floats**" only if the ratio ≤ 0.5 on **both** inputs.
2. **Integer ceiling (all six):** ratio M / best of {FLAC, PQ-i, DDB-i}.
   - Claim "**within 15% of the best integer codec**" only if ≤ 1.15 on all six.
3. **Decimal floats (ims, ecg):** M / ALP reported; ALP is designed for decimals, so no
   claim of superiority is made unless the ratio ≤ 0.8 on both.

Any ratio that misses its threshold is reported as such, and the paper states the
weaker result.

## Reported, deciding nothing

- Per-input bits/sample for every arm; DuckDB compression choices; pg_morlet chunk kinds
  (grid v2, grid v3, float, raw).
- Encode and decode throughput where the tool reports it (single thread, one machine;
  descriptive only).
- Sleep: symmetric vs asymmetric nights separately.

## Not in B1

No database-level comparison beyond what P2 measured, no lossy coding, no tuning of any
arm after the first run. If an arm fails to run, the failure is reported, not fixed by
changing other arms.
