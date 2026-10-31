# B1 results: lossless storage against ALP, Parquet, zstd and integer codecs

Run once on 2026-10-31 as frozen in [PROTOCOL.md](PROTOCOL.md):
- protocol committed at 9fa3a57;
- harness and tests at 860c67b, before measuring;
- one change after the freeze (DuckDB block size), found by the synthetic test and logged.

Software: DuckDB 2.0.0, pyarrow 26.0.0, zstandard 0.25.0, pyflac 3.0.0 (libFLAC), `morlet-core` at this branch. Apple M5 Max, single thread. Every arm decoded and checked bitwise (floats) or exactly (integers).

**None of the three pre-registered claims holds.** The two that fail do so informatively:
- **Calibrated floats:** pg_morlet is 1.5–1.6× smaller than the best metadata-free float codec, not the 2× the claim required. It is 3.8–6.8× smaller than ALP, which falls back to ALP-RD on these inputs and saves almost nothing.
- **Integer ceiling:** within 5% of FLAC on four inputs, but 19% (Paderborn physical) and 79% (Sleep-EDF) larger. The whole gap is last-place corrections.
- **Decimals:** 20% smaller than ALP on IMS (claim needs ≤ 0.80; measured 0.8008) and 42% on ECG. The claim required both, and IMS misses by 0.0008.

## Bits per sample

| Input | Z (zstd-19) | PQ-f (BSS + zstd) | ALP (DuckDB) | **M: pg_morlet + quantum** | FLAC (counts) | PQ-i (delta + zstd) | DDB-i (bit-packing) | M-i (counts) |
|---|---|---|---|---|---|---|---|---|
| CX seismic counts | 8.33 | 6.84 | 9.50 | **4.61** | 4.38 | 5.24 | 6.38 | 4.61 |
| IMS three-decimal | 13.24 | 42.94 | 11.00 | **8.81** | 8.41 | 9.87 | 10.88 | 8.81 |
| Paderborn counts | 12.04 | 9.48 | 10.75 | **7.88** | 7.70 | 9.61 | 11.25 | 7.88 |
| Paderborn physical | 14.82 | 30.57 | 62.13 (ALP-RD) | **9.17** | 7.70 | 9.61 | 11.25 | 7.88 |
| Sleep-EDF Fpz-Cz | 23.54 | 38.15 | 59.63 (ALP-RD) | **15.51** | 8.65 | 10.22 | 12.00 | 8.80 |
| PTB-XL ECG lead I | 18.94 | 44.64 | 12.75 | **7.40** | 7.46 | 10.27 | 12.75 | 7.40 |

- Z, PQ-f and ALP receive only the float64 values. M additionally receives the quantum.
  FLAC, PQ-i, DDB-i and M-i receive the exact integer counts.
- DuckDB bytes are whole 64 KiB blocks (± 0.125 bits/sample).
- pg_morlet coded every chunk as a grid chunk on every input.

## Pre-registered comparisons

| Input | M / best metadata-free float | M / best integer codec | M / ALP |
|---|---|---|---|
| CX | 0.67 (PQ-f) | 1.05 (FLAC) | 0.49 |
| IMS | 0.80 (ALP) | 1.05 (FLAC) | **0.80** |
| Paderborn counts | 0.83 (PQ-f) | 1.02 (FLAC) | 0.73 |
| **Paderborn physical** | **0.62** (Z) | **1.19** (FLAC) | 0.15 |
| **Sleep-EDF** | **0.66** (Z) | **1.79** (FLAC) | 0.26 |
| ECG | 0.58 (ALP) | 0.99 (FLAC) | **0.58** |

| Claim | Threshold | Result |
|---|---|---|
| ≥ 2× smaller than metadata-free float codecs on calibrated floats | ≤ 0.5 on pad-p and sleep | 0.62, 0.66: **not supported** |
| Within 15% of the best integer codec | ≤ 1.15 on all six | 4 of 6; pad-p 1.19, sleep 1.79: **not supported** |
| ≥ 20% smaller than ALP on decimals | ≤ 0.8 on ims and ecg | 0.8008, 0.58: **not supported** (IMS misses by 0.0008) |

## What the numbers say (descriptive)

- **Calibrated floats defeat decimal-oriented float codecs.** ALP looks for a decimal
  exponent that turns doubles into integers. Values like (d − dmin)·417/4095 − 208 have
  none, so DuckDB chose ALP-RD and stored ~60 bits/sample, close to raw. zstd is the
  best metadata-free codec on both inputs, and pg_morlet with the nominal quantum is
  1.5–1.6× smaller than zstd.
- **The remaining gap is the correction stream, not prediction or entropy coding.** M on
  the counts (M-i) is within 2–5% of FLAC everywhere. M on the calibrated floats costs
  1.3 (Paderborn) and 6.7 (Sleep-EDF) bits/sample more than M-i. Those bits store the
  ±ulp differences between the encoder's refitted affine grid and the floats as the
  source software computed them. On Sleep-EDF the source computes
  ((d − dmin)·(pmax − pmin))/(dmax − dmin) + pmin, whose rounding a single
  multiply-add on a fitted step cannot reproduce.
- **Implication:** a grid that replays the source's calibration arithmetic exactly would
  remove the corrections and bring calibrated floats to the integer-codec cost (about
  8.8 rather than 15.5 bits/sample on Sleep-EDF). That is a codec change, not tested here.
- **Decimals:** on ECG (gain 1000) and IMS, both pg_morlet and ALP recover the integers;
  pg_morlet's predictor-plus-Rice coding beats ALP's frame-of-reference bit-packing
  because consecutive samples are correlated.
- **Parquet BYTE_STREAM_SPLIT** helps on integer-valued doubles (CX, Paderborn counts)
  but hurts on decimals and calibrated values, where the low mantissa bytes are noise.
- **Throughput** (pg_morlet, single thread): encode 5–13 M samples/s, decode 33–78 M
  samples/s. Other arms' speeds were not measured.

## Limits

- One chunk size (4,096; 1,000 for ECG records) and one machine.
- DuckDB sizes include block rounding; the arm is a DuckDB table, not a standalone ALP
  library call, so ALP's own per-vector overhead and DuckDB's segment layout are both
  included.
- FLAC is LPC-based and the strongest integer baseline here; pg_morlet has no LPC stage.
- The P2 inputs were measured before; Sleep-EDF slice and PTB-XL are new to the storage
  studies but not to the repository.

## Reproduce

```sh
cargo build --release -p morlet-core --example rawcodec_bench
.venv-compress/bin/python -I evaluation/plain_postgres/p2_dump.py data/b1-runs/p2
.venv-paper/bin/python -I evaluation/storage_baselines/prepare.py data/sleep-edfx/1.0.0/sleep-cassette \
    data/ptb-xl/1.0.3/records100 data/b1-runs/inputs
.venv-paper/bin/python -I evaluation/storage_baselines/test_b1.py
.venv-paper/bin/python -I evaluation/storage_baselines/measure.py data/b1-runs/inputs data/b1-runs/p2 \
    target/release/examples/rawcodec_bench evaluation/storage_baselines/results
```

`.venv-paper`: Python 3.14 with duckdb 2.0.0, pyarrow, pyflac, zstandard, numpy.
