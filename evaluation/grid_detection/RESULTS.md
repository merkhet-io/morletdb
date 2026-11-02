# G1 results: automatic calibration-grid detection recovers the declared-quantum size

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen with the encoder change (e7f6a5b; on `main` as ba69f6f) before any G1 run. Run once on
2026-11-02. Raw results: [`results/g1.json`](results/g1.json) (`g1.py`, B1's six inputs) and
[`../tsdb_b3/results/g1/`](../tsdb_b3/results/g1/) (the B3 benchmark set). Apple M5 Max, single thread for
the codec bench; the B3 load used the B3 harness and container limits (image `morletdb:g1-dev`, id
`sha256:4f12e8e6ad98…`).

## Verdict: all criteria met

| Input | D: declared quantum | **A: quantum 1, detection** | P: quantum 1, previous encoder | A / D | A / P |
|---|---:|---:|---:|---:|---:|
| CX seismic counts | 4.61 bits/sample | 4.61 | 4.61 | 1.00 | 1.00 |
| IMS three-decimal values | 8.81 | **8.81** | 63.02 | 1.00 | **0.14** |
| Paderborn counts | 7.88 | 7.88 | 7.88 | 1.00 | 1.00 |
| Paderborn physical floats | 9.17 | **9.17** | 63.17 | 1.00 | **0.15** |
| Sleep-EDF Fpz-Cz | 14.97 | **14.66** | 60.02 | 0.98 | **0.24** |
| PTB-XL ECG lead I | 7.40 | **7.40** | 57.58 | 1.00 | **0.13** |

1. **Exactness:** every chunk of every arm round-tripped bitwise.
2. **Recovery (A ≤ 1.05 × D):** met on all six; on Sleep-EDF detection is 2% *smaller* than the declared quantum.
3. **No loss (A ≤ 1.001 × P):** met on all six; on the four float inputs A is 4.1–7.8× smaller.
4. **Speed (M samples/s, encode / decode):** on float inputs detection is *faster* than the previous float
   coding (ECG 11.1 / 119 vs 3.3 / 63; IMS 25.2 / 165 vs 11.5 / 84), because grid coding is cheaper than
   float-bit coding; with a declared quantum it is faster still (ECG 13.9 / 119). Integer inputs: unchanged.

## B3 benchmark set, no quantum declared for any channel

| | Declared quanta (B3.2) | **No quantum (G1)** | Previous encoder, no quantum | QuestDB 10.1, Parquet zstd 9 |
|---|---:|---:|---:|---:|
| Sample data | 156.75 MiB | **151.1 MiB** | 621.6 MiB | 232.7 MiB |
| 64 kHz group (vibration, currents) | 1.277 bytes/sample | 1.277 (byte-identical) | about 6 | 2.20 |
| 4 kHz group (force, speed, torque) | 3.72 | **2.79** | 3.72 | 5.57 |

The load passed full correctness; single-connection ingestion 5.47 M samples/s (B3.2 with declared quanta:
5.56 M median). Without being told any calibration step, MorletDB stored the samples 4% smaller than with
the declared quanta (it also found grids in the 4 kHz channels, which had none declared) and **35% smaller
than QuestDB's Parquet zstd 9**.

## What this changes

The storage advantage no longer depends on the user supplying the calibration step: floats that are
calibrated ADC counts or fixed decimals are detected chunk by chunk. Signals with no value grid at all
(full-entropy floats) still fall back to float coding, as before.
