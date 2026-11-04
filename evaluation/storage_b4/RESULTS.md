# B4 results: MorletDB against the 2026 float codecs

Protocol frozen in [PROTOCOL.md](PROTOCOL.md) (commit `ce3084e`); one deviation (ClickHouse row order in the
harness, fixed and the ClickHouse arms rerun, both result files kept). Measured 2026-11-04 on Apple M5 Max:
pg_morlet main, `pcodec` 1.0.4, Rust `parquet` 60.0.0, ClickHouse 26.10.3.7. Every arm on every input was
decoded and compared bit for bit: **no mismatch** in `results/b4_final.json`.

## Verdict

1. **W (waveforms, no calibration declared): MorletDB is the smallest on 4 of 6 inputs, not all.** It is
   smaller than every other arm on CX counts (12% below the next), Paderborn counts (5%), Sleep-EDF (20%) and
   ECG (6%). **Pcodec is smaller on IMS decimals (8.12 vs 8.81 bits, 8%) and Paderborn physical (8.68 vs 9.17,
   5%)**, and ClickHouse's new PCO codec is also smaller on Paderborn physical (8.92 vs 9.17, 3%).
2. **C (calibrated grids): holds.** On Paderborn physical and Sleep-EDF, every ALP arm (Parquet and
   ClickHouse, with and without zstd) is **2.6–7.9× larger** than MorletDB. ALP's decimal-scale detection does
   not find a calibrated ADC grid; ALP-based storage is a poor fit for calibrated sensor data.
3. **E (Elf decimal series): MorletDB is the smallest on 12 of 22**, Pcodec on 9, ClickHouse ALP + zstd on 1.
   Of its 10 losses, five are large, on high-precision series without a grid (Basel temperature and wind,
   Bird migration, Food price, SSD bench: 2.7–4.1× Pcodec), and five are 1.1–1.6× the smallest arm.

## Waveforms: bits per sample (lower is better; smallest in bold)

| Input | M-auto | M-decl | PCO | CH-PCO | CH-ALP | CH-ALP-zstd9 | CH-zstd9 | PQ-ALP | PQ-ALP-zstd9 | PQ-BSS-zstd9 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| CX counts | **4.61** | **4.61** | 5.23 | 5.33 | 9.26 | 9.29 | 10.38 | 9.29 | 9.29 | 7.30 |
| IMS decimals | 8.81 | 8.81 | **8.12** | 9.19 | 10.42 | 10.22 | 13.91 | 10.44 | 10.13 | 49.81 |
| Paderborn counts | **7.88** | **7.88** | 8.31 | 8.47 | 10.36 | 10.36 | 13.80 | 10.39 | 10.38 | 10.00 |
| Paderborn physical | 9.17 | 9.17 | **8.68** | 8.92 | 59.61 | 31.42 | 15.35 | 72.02 | 24.02 | 35.09 |
| Sleep-EDF | **14.66** | 14.97 | 20.27 | 18.33 | 59.62 | 40.81 | 21.48 | 75.17 | 41.49 | 40.82 |
| ECG | **7.40** | **7.40** | 7.84 | 8.08 | 11.01 | 10.90 | 15.41 | 11.04 | 10.87 | 49.92 |

M-decl uses B1's declared quanta; it never beat M-auto here (the codec detects the grid itself since G1).
Decode speed on these inputs (single thread): M 65–132 M samples/s per 4,096-sample chunk, Pcodec 156–268 M
samples/s on whole series (not a statement; different granularity).

## Elf decimal series: bits per value (selected arms; all arms in `results/b4_final.json`)

| Series | n | M-auto | PCO | CH-PCO | CH-ALP-zstd9 | PQ-ALP | CH-zstd9 | smallest |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Air-pressure | 95,928 | 11.45 | **8.73** | 10.77 | 16.75 | 16.77 | 26.77 | PCO |
| Air-sensor | 8,664 | **46.10** | 46.57 | 46.63 | 54.26 | 73.32 | 59.85 | M-auto |
| Basel-temp | 100,001 | 52.38 | **15.27** | 27.85 | 28.85 | 29.48 | 21.07 | PCO |
| Basel-wind | 100,000 | 53.45 | **14.29** | 21.79 | 29.08 | 31.06 | 23.40 | PCO |
| Bird-migration | 89,867 | 45.90 | **15.05** | 16.99 | 19.69 | 21.38 | 23.82 | PCO |
| Bitcoin-price | 7,116 | 46.11 | 46.20 | 46.23 | **28.80** | 29.51 | 38.17 | CH-ALP-zstd9 |
| Blockchain-tr | 99,999 | 13.96 | **12.37** | 13.16 | 15.61 | 20.35 | 18.63 | PCO |
| City-lat | 41,001 | **20.46** | 20.54 | 20.63 | 21.62 | 21.61 | 31.58 | M-auto |
| City-lon | 41,001 | **21.65** | 22.14 | 22.26 | 22.64 | 22.64 | 33.25 | M-auto |
| City-temp | 100,001 | **7.50** | 7.72 | 7.73 | 9.71 | 10.76 | 12.19 | M-auto |
| Dew-point-temp | 100,001 | **8.19** | 8.98 | 9.09 | 12.58 | 12.59 | 19.81 | M-auto |
| Food-price | 100,001 | 32.14 | **11.84** | 19.14 | 14.75 | 19.43 | 15.55 | PCO |
| IR-bio-temp | 99,602 | **3.60** | 4.29 | 4.41 | 9.10 | 9.46 | 11.60 | M-auto |
| PM10-dust | 93,662 | **3.99** | 4.45 | 4.28 | 6.22 | 9.35 | 7.11 | M-auto |
| POI-lat | 100,001 | 60.84 | **53.30** | 53.51 | 59.28 | 95.46 | 56.04 | PCO |
| POI-lon | 100,001 | 64.02 | **54.55** | 54.81 | 59.22 | 88.34 | 57.96 | PCO |
| SSD-bench | 8,927 | 31.40 | **7.59** | 7.88 | 9.74 | 16.91 | 8.60 | PCO |
| Stocks-DE | 100,002 | **5.52** | 6.58 | 6.14 | 11.03 | 11.34 | 13.02 | M-auto |
| Stocks-UK | 100,002 | **5.30** | 8.58 | 5.96 | 10.90 | 11.09 | 12.27 | M-auto |
| Stocks-USA | 100,002 | **4.56** | 5.23 | 5.20 | 8.90 | 9.13 | 11.73 | M-auto |
| Wind-Speed | 99,132 | **5.08** | 5.40 | 5.85 | 6.55 | 6.86 | 10.15 | M-auto |
| electric_vehicle_charging | 3,395 | **10.03** | 10.39 | 10.46 | 11.32 | 11.72 | 17.39 | M-auto |

## Reading

- **The storage lead on waveforms is real but narrower than against 2025's codecs.** Against the databases
  B1–B3 measured (QuestDB, TimescaleDB, ClickHouse 26.5, TileDB) MorletDB stayed smallest; against
  ClickHouse 26.10 with PCO it is smaller on 5 of 6 waveforms, and Pcodec, a standalone codec, is smaller on 2.
- **Where Pcodec wins, the data are almost on a grid.** IMS decimals and Paderborn physical are not exact
  multiples of one step everywhere; Pcodec's float-multiple mode with per-chunk adaptation captures what
  MorletDB's single grid per chunk misses. This is the clearest lead for improving the codec.
- **ALP is not a waveform codec.** On calibrated values it falls back to near-raw storage.
- **VIREL** (7.0–7.5× on its Elf subset in its paper) could not be measured: no code was found.

## Scope

One machine; six waveform inputs of about 4.19 M samples and 22 short decimal series; codecs at their default
settings (no per-input tuning of any arm). Elf series are parsed by B4's rule, not Elf's own reader.
