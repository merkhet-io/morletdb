# G1 results: automatic calibration-grid detection recovers the declared-quantum size

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen with the encoder change (e7f6a5b) before any G1 run. Run once on
2026-11-02. Raw results: [`results/g1.json`](results/g1.json) (`g1.py`, B1's six inputs). Apple M5 Max, single thread for
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

## B3 benchmark set

The B3 load without declared quanta (protocol item 5) is reported with the B3 harness option in a follow-up
change, after the B3.2 harness is merged.

## What this changes

The storage advantage no longer depends on the user supplying the calibration step: floats that are
calibrated ADC counts or fixed decimals are detected chunk by chunk. Signals with no value grid at all
(full-entropy floats) still fall back to float coding, as before.
