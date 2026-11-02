# B2: ClickHouse and TileDB storage baselines — results

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at a3efa67, with deviations recorded before the full run:
- ClickHouse 26.5.1.1 through `chdb-core` 26.5.0, because 26.9.0 and 26.7.3 do not load on macOS 27;
- wide parts;
- one chain rejected by ClickHouse.

Measured 2026-11-02 on B1's six inputs (about 4.19 M samples each) with `chdb` 4.4.0 and TileDB 2.30.0
(TileDB-Py 0.36.1). Every arm was read back and compared bit for bit (floats) or exactly (integers).
Raw results: `results/b2.json` (`measure_b2.py`).

## Verdict

1. **Calibrated floats: pg_morlet with its quantum is smaller than every tested metadata-free float storage,
   ClickHouse and TileDB included.** On Paderborn physical values it is 0.66× the best (ClickHouse
   `ZSTD(19)`); on Sleep-EDF, 0.76× (also ClickHouse). The pre-declared claim (< 1 on both) is supported.
2. **Integer ceiling: unchanged from B1.** FLAC is still the best integer codec on every input; no ClickHouse
   or TileDB chain beat it. The 15% claim is still not supported, failing on the same two inputs (pad-p 1.19,
   Sleep-EDF 1.79).
3. **Re-measured pg_morlet equals B1** on all six inputs.

## Bits per sample (lower is better)

| Input | M (pg_morlet + quantum) | CH-f | TDB-f | best of B1 float (Z, PQ-f, ALP) | FLAC | CH-i | TDB-i | best of B1 integer (PQ-i, DDB-i) |
|---|---|---|---|---|---|---|---|---|
| CX counts | **4.61** | 7.68 | 7.03 | 6.84 (PQ-f) | 4.38 | 6.84 | 5.91 | 5.24 (PQ-i) |
| IMS decimals | **8.81** | 12.49 | 13.31 | 11.00 (ALP) | 8.41 | 8.64 | 8.87 | 9.87 (PQ-i) |
| Paderborn counts | **7.88** | 11.79 | 10.04 | 9.48 (PQ-f) | 7.70 | 9.04 | 9.42 | 9.61 (PQ-i) |
| **Paderborn physical** | **9.17** | 13.86 | 14.89 | 14.82 (Z) | 7.70 | 9.04 | 9.42 | 9.61 (PQ-i) |
| **Sleep-EDF** | **15.51** | 20.39 | 23.61 | 23.54 (Z) | 8.65 | 10.06 | 10.36 | 10.22 (PQ-i) |
| PTB-XL ECG | **7.40** | 14.22 | 15.31 | 12.75 (ALP) | 7.46 | 9.17 | 9.23 | 10.27 (PQ-i) |

- Float columns get only the float64 values; M also gets the nominal quantum. Integer columns get the exact
  digitizer counts.
- ClickHouse is credited with its best chain:
  - **CH-f:** plain `ZSTD(19)` on five inputs, `Delta(8), ZSTD(19)` on CX.
  - **CH-i:** `T64, ZSTD(19)` on four inputs, `Delta, ZSTD(19)` on CX, `DoubleDelta, ZSTD(19)` on ECG.
- TileDB is credited with its best pipeline:
  - **TDB-f:** `Zstd(19)` or `ByteShuffle, Zstd(19)`, and `BitShuffle, Zstd(19)` on CX.
  - **TDB-i:** `ByteShuffle, Zstd(19)` on five inputs, `DoubleDelta, Zstd(19)` on CX.
- All chains are in `b2.json`. `Gorilla, ZSTD(19)` on Int64 was rejected by ClickHouse on every input
  (deviation 3).
- Sizes count only the value column's data file (ClickHouse) or the attribute's data file (TileDB). Marks,
  row numbers and metadata are excluded, which favours both engines.

## Ratios against the new baselines

| Input | M / CH-f | M / TDB-f | M / best float (all) | M / CH-i | M / TDB-i | M / best integer (all) |
|---|---|---|---|---|---|---|
| CX counts | 0.60 | 0.66 | 0.67 (PQ-f) | 0.67 | 0.78 | 1.05 (FLAC) |
| IMS decimals | 0.71 | 0.66 | 0.80 (ALP) | 1.02 | 0.99 | 1.05 (FLAC) |
| Paderborn counts | 0.67 | 0.79 | 0.83 (PQ-f) | 0.87 | 0.84 | 1.02 (FLAC) |
| **Paderborn physical** | **0.66** | **0.62** | **0.66 (CH-f)** | 1.01 | 0.97 | 1.19 (FLAC) |
| **Sleep-EDF** | **0.76** | **0.66** | **0.76 (CH-f)** | 1.54 | 1.50 | 1.79 (FLAC) |
| PTB-XL ECG | 0.52 | 0.48 | 0.58 (ALP) | 0.81 | 0.80 | 0.99 (FLAC) |

## Observations

- **Column-wide blocks help ClickHouse.** Its plain `ZSTD(19)` beat B1's per-chunk zstd on the calibrated
  inputs (13.86 against 14.82; 20.39 against 23.54), because ClickHouse compresses blocks larger than one
  4,096-sample chunk. Its float codecs (`Gorilla`, `FPC`) did not help on any input.
- **pg_morlet, given only floats and a quantum, beat both engines given the exact integers** on three
  inputs (CX, Paderborn counts, ECG). It was within 3% of them on IMS and Paderborn physical.
- **Only on Sleep-EDF** did the engines' integer codecs beat M clearly (10.1 against 15.5). That input needs
  last-place corrections (B1), which cost bits that integer arms never pay.

## Limitations

- One machine, one run; sizes are deterministic, times (in `b2.json`) are descriptive only.
- Six inputs; ClickHouse 26.5 through chDB, not a server build; TileDB with one tile size (4,096).
- "Tuned" means the best of a pre-declared chain set per input, not an exhaustive search; compression levels
  and block sizes were not tuned.
