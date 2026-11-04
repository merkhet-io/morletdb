# B4: MorletDB against the 2026 float codecs — protocol (FROZEN)

Frozen in the commit that adds this file, before the confirmation run. Development touched only four Elf
series (`results/dev/`) to check the harness; MorletDB's codec is not tuned in B4.

## Question

Since B1/B2, lossless float compression has moved on: Pcodec detects float multiples, ClickHouse added ALP
and PCO codecs, Parquet standardized ALP (parquet-format 2.14.0), and VIREL (arXiv 2607.22433, not peer
reviewed, no code found) reports 7.0–7.5× on the Elf suite. Is MorletDB's chunk codec still the smallest
lossless store for sensor **waveforms**, and how does it do on the low-rate **decimal** series those codecs are
published on?

## Inputs

1. **Waveforms:** B1/B2's six inputs, unchanged (`data/b1-runs`): CX counts, IMS decimals, Paderborn counts,
   Paderborn physical, Sleep-EDF, ECG; about 4.19 M samples each, as 4,096-sample chunks.
2. **Decimal series:** the Elf test data (Spatio-Temporal-Lab/elf at `64e0d60`, `fetch_elf.sh`,
   `elf-sha256.txt`): 23 files, of which `init.csv` is a byte-identical copy of `Wind-Speed.csv` and is
   dropped, so 22 series (3.4 k–100 k values). Every line that parses as a number is kept in order; other
   lines (empty, `""`) are skipped. The repository states no license; the files are used locally and not
   redistributed.

## Arms (`measure_b4.py`; all lossless)

| Arm | What | Version |
|---|---|---|
| M-auto | pg_morlet chunk codec, 4,096-sample chunks, quantum 1 (the codec detects a grid itself) | main at this commit (`rawcodec_bench`) |
| M-decl | the same with B1's declared quanta (waveform inputs only) | same |
| PCO | Pcodec, default level, whole series | `pcodec` 1.0.4 (PyPI) |
| PQ-ALP, PQ-ALP-zstd9, PQ-BSS-zstd9 | one Float64 column in Parquet (writer v2, dictionary off): ALP; ALP + zstd 9; byte-stream-split + zstd 9 | Rust `parquet` 60.0.0 (`pqalp`) |
| CH-ALP, CH-ALP-zstd9, CH-PCO, CH-zstd9 | one Float64 column in a ClickHouse MergeTree wide part (`enable_alp_codec`, `enable_pco_codec`); compressed column bytes | `clickhouse/clickhouse-server:26.10` (26.10.3.7) |

Every arm is decoded and compared bit for bit with its input; a mismatch is recorded and voids that arm on
that input. VIREL has no code; its published figures may be quoted as such, never in a statement. SZ3 is
lossy and excluded. FLAC is B1/B2's integer ceiling and is not re-run.

**Measure:** bits per sample (primary). Encode/decode throughput is reported for M, PCO and PQ (single
thread, one pass, not a statement).

## Statements (fixed now)

- **W (waveforms, metadata-free):** on each waveform input, M-auto vs every other arm (excluding M-decl).
  "MorletDB is the smallest" on an input if M-auto's bits/sample are lower than every other arm's; reported
  per input, no averaging.
- **C (calibrated grids):** the claim "decimal-scale ALP misses calibrated ADC grids" holds if PQ-ALP and
  CH-ALP (each with and without zstd) are ≥ 10% larger than M-auto on **both** Paderborn physical and
  Sleep-EDF.
- **E (decimal series):** report, per series, the smallest arm and M-auto's ratio to it, and the count of
  series where M-auto is the smallest. No claim is pre-registered; the result is reported whatever it is.

## Development (not results)

`results/dev/b4_dev.json`: 4 Elf series chosen by seed 20261104 from the 22 (Blockchain-tr, POI-lat,
Stocks-USA, electric_vehicle_charging). Every arm round-tripped exactly. Smallest arm: M-auto on Stocks-USA
(4.56 bits) and electric_vehicle_charging (10.03); PCO on Blockchain-tr (12.37) and POI-lat (53.30). What
development changed: Elf parsing (empty lines and `""` skipped), duplicate removal (`init.csv`). Statement E covers
all 22 series, the four development series included: development checked the harness only, nothing was tuned.
