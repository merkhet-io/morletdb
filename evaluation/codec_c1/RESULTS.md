# C1 codec spike: Pcodec at MorletDB's chunk size (DEVELOPMENT, not a benchmark)

Issue #326. **Every figure here is development:** B4's four development Elf series and two waveforms that are not
B4 inputs. Nothing here is a statement, and none of B4's benchmark inputs (the other 18 Elf series, B4's six
waveforms) was read. A frozen B5 protocol measures those later.

Question: does Pcodec's (`pco`) storage gain over the chunk codec's float fallback survive at 4,096 samples per
chunk (and 32,768), and what does it cost in speed?

## Setup

- `cargo run --release -- <repo>/data results/dev.json` (`src/main.rs`); Apple M5 Max, 2026-11-10, rawcodec from
  main at `8ecb7ac` (chunk format version 3), `pco` 1.0.4 (the version B4's PyPI `pcodec` 1.0.4 arm used). The
  crate has its own `[workspace]` table and `Cargo.lock`, like `storage_b4/pqalp`: the main workspace lockfile is
  unchanged.
- **Inputs.** B4 development (PROTOCOL.md, "Development": seed 20261104): Blockchain-tr, POI-lat, Stocks-USA,
  electric_vehicle_charging, parsed by B4's rule. B4's development set has no waveform, so two waveforms outside
  B4: **XJTU-SY** Bearing1_1 (35 Hz/12 kN) horizontal, files 1–123 (4.03 M samples; values are float32 written
  at double precision, no grid) and **FEMTO** Bearing1_1 horizontal, files 1–1638 (4.19 M samples; 3-decimal grid).
- **Arms** (bits/sample): `raw` = today's chunk codec, quantum 1 (grid detected per chunk); `pco` = pco
  standalone per chunk, level 8 (default, mode and delta auto-detected), level 4, and level 8 with mode detection
  off (`Classic`); `min` = per chunk the smaller of `raw` and pco-L8, a pco chunk charged 5 extra bytes (kind tag
  + CRC32C); `pco whole` = pco on the whole series (B4's PCO arm). pco standalone's own framing is 10 bytes per
  chunk (included; a wrapped format would drop part of it): 0.02 bits/sample at 4,096.
- **Round trip:** every chunk of every arm decoded and compared with `to_bits`: **0 failures** (of 2,348
  chunks per arm). Sanity: `raw` and `pco whole` reproduce B4's development figures exactly (Blockchain-tr 13.96 /
  12.37, POI-lat 60.84 / 53.30, Stocks-USA 4.56 / 5.23, EV charging 10.03 / 10.39).
- **Speed:** single thread, best of 3 passes per chunk, M samples/s. The host was loaded (load average 5–6, a
  queued benchmark and VMs running): a first pass of the same binary measured up to 1.5× faster. Treat speeds as
  ranges, not figures to quote.

## Bits per sample

`min / pco whole` is the issue's proxy: on development inputs pco whole-series stands in for ClickHouse's best arm.

### Blockchain-tr (Elf dev, 99,999 values; raw codes every chunk on a grid)

| chunk | raw | pco L8 | pco L4 | pco L8 Classic | **min** | pco whole | min / pco whole | chunks to pco |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4,096 | 13.96 | 13.63 | 14.59 | 32.68 | **13.27** | 12.37 | 1.07 | 15 / 25 |
| 32,768 | 15.00 | 12.49 | 13.37 | 19.63 | **12.49** | 12.37 | 1.01 | 4 / 4 |

### POI-lat (Elf dev, 100,001; raw falls back to float coding)

| chunk | raw | pco L8 | pco L4 | pco L8 Classic | **min** | pco whole | min / pco whole | chunks to pco |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4,096 | 60.84 | 53.60 | 54.24 | 53.60 | **53.61** | 53.30 | 1.006 | 25 / 25 |
| 32,768 | 62.84 | 53.36 | 54.13 | 53.36 | **53.36** | 53.30 | 1.001 | 4 / 4 |

### Stocks-USA (Elf dev, 100,002; grid)

| chunk | raw | pco L8 | pco L4 | pco L8 Classic | **min** | pco whole | min / pco whole | chunks to pco |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4,096 | 4.56 | 5.27 | 5.72 | 7.98 | **4.56** | 5.23 | 0.87 | 0 / 25 |
| 32,768 | 4.46 | 5.21 | 6.35 | 8.78 | **4.46** | 5.23 | 0.85 | 0 / 4 |

### electric_vehicle_charging (Elf dev, 3,395: one chunk at either size; grid)

| chunk | raw | pco L8 | pco L4 | pco L8 Classic | **min** | pco whole | min / pco whole | chunks to pco |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4,096 / 32,768 | 10.03 | 10.39 | 10.54 | 46.06 | **10.03** | 10.39 | 0.97 | 0 / 1 |

### XJTU-SY Bearing1_1 horizontal (waveform, not a B4 input; float32 values, no grid)

| chunk | raw | pco L8 | pco L4 | pco L8 Classic | **min** | pco whole | min / pco whole | chunks to pco |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4,096 | 63.66 | 29.80 | 30.53 | 55.99 | **29.81** | 29.70 | 1.004 | 984 / 984 |
| 32,768 | 63.70 | 29.68 | 30.50 | 55.74 | **29.68** | 29.70 | 1.000 | 123 / 123 |

raw kinds at 4,096: 921 float, 63 raw (float coding saves almost nothing on these values).

### FEMTO Bearing1_1 horizontal (waveform, not a B4 input; 0.001 grid)

| chunk | raw | pco L8 | pco L4 | pco L8 Classic | **min** | pco whole | min / pco whole | chunks to pco |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4,096 | 10.65 | 11.56 | 11.76 | 55.78 | **10.65** | 11.12 | 0.96 | 0 / 1,024 |
| 32,768 | 10.63 | 11.16 | 11.53 | 15.84 | **10.63** | 11.12 | 0.96 | 0 / 128 |

## Speed at 4,096 samples (M samples/s, single thread, loaded host)

| Input | raw enc | raw dec | pco L8 enc | pco L8 dec | pco L4 enc | pco L4 dec | min enc (both) | min dec |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Blockchain-tr | 10.6 | 97 | 13.0 | 156 | 30.7 | 188 | 5.9 | 122 |
| POI-lat | 5.1 | 49 | 11.0 | 245 | 39.7 | 261 | 3.5 | 245 |
| Stocks-USA | 15.8 | 107 | 25.5 | 144 | 30.5 | 168 | 9.8 | 107 |
| EV charging | 12.8 | 101 | 15.5 | 149 | 22.4 | 150 | 7.0 | 101 |
| XJTU-SY | 6.7 | 64 | 11.8 | 297 | 34.5 | 308 | 4.3 | 297 |
| FEMTO | 17.1 | 94 | 15.6 | 218 | 42.0 | 255 | 8.1 | 94 |

At 32,768, pco decode is 176–512 and encode 23–36 (L8). `min enc` runs both encoders per chunk (raw + pco L8).

## Reading (development only)

1. **The gain survives at 4,096.** Where pco wins, per-chunk pco stays within 0.3–0.6% of pco on the whole
   series (POI-lat 53.60 vs 53.30, XJTU-SY 29.80 vs 29.70); per-chunk metadata costs little. Blockchain-tr is the
   exception: pco L8 per 4,096 chunk is 13.63 vs 12.37 whole (10% worse), and per-chunk `min` lands at 1.07×
   pco whole; at 32,768 the gap closes (1.01×).
2. **`min` never loses to raw and keeps grid chunks.** On every grid input where raw is smaller (Stocks-USA, EV
   charging, FEMTO) not one chunk went to pco: those chunks stay byte-identical. But a grid chunk is not always
   the smaller one: on Blockchain-tr 15 of 25 grid chunks went to pco. The #326 criterion "waveforms with a
   grid: chunks byte-identical" holds here only because raw happened to win every FEMTO chunk; B5 should phrase
   it as "a chunk changes only when the new coding is smaller".
3. **Which inputs benefit:** chunks raw codes as float or raw (POI-lat −12%, XJTU-SY −53%) and near-grid
   series (Blockchain-tr −5%). XJTU-SY shows the float fallback is near raw size (63.7 bits) on float32 data held
   as float64: pco's mode detection (`FloatQuant`, Classic 56.0 → Auto 29.8) recovers it.
4. **#326 pass criteria, proxy only.** On the development inputs `min` is within 1.10× of pco whole on all four
   Elf series (worst 1.07×, Blockchain-tr at 4,096). The five 2–4× losses (SSD-bench, Bird-migration,
   Basel-temp/-wind, Food-price) are **not** development inputs, so nothing here says whether `min` gets them to
   ≤ 1.25×. The one development input with a comparable structural loss (XJTU-SY, raw 2.1× pco) reaches 1.004×.
   Note: Basel/Food/SSD are short series (≤ 100 k values; SSD-bench 8,927 = 3 chunks), so per-chunk overhead
   there is the open risk; B5 decides.
5. **Speed.** pco L8 decode per 4,096 chunk: 144–297 M samples/s here, above raw (49–107) and well above the
   ≥ 50 M samples/s requirement on both waveforms (FEMTO keeps raw: 94; XJTU-SY goes pco: 297). The cost is on
   encode: keeping the smaller means running both codecs, 3.5–9.8 M samples/s vs raw's 5–17 alone (about half).
   pco L4 encodes 1.2–3.6× faster than L8 for 1–9% more bits; level 8 is the better default for a write-once
   archive unless ingest rate matters (B3 ingest is to be rechecked in B5).

## pco: licence and format stability (checked 2026-11-10)

- **Licence:** Apache-2.0 (crates.io metadata and `Cargo.toml` of `pco` 1.0.4; repository
  https://github.com/pcodec/pcodec), the same as this repository. Its dependency tree is permissive:
  `better_io`, `dtype_dispatch` (Apache-2.0), `half`, `rand_xoshiro`, `rand_core`, `cfg-if` (MIT OR Apache-2.0),
  `crunchy` (MIT), `zerocopy` (BSD-2-Clause OR Apache-2.0 OR MIT). The repository has no `deny.toml` and CI runs
  no cargo-deny step, so there is no deny policy to check against.
- **Maintenance:** 1.0.0 on 2026-01-18, then 1.0.1–1.0.4 and 1.1.0 (2026-11-03); about 2.3 M downloads;
  MSRV 1.87 (the repository pins Rust 1.99).
- **Format stability** (https://github.com/pcodec/pcodec/blob/main/docs/format.md): within a compatibility line
  (1.x.y) every version should decompress data from earlier or equal versions, and also from later versions
  unless opt-in features are used; a decoder with a higher major version, or the same major and an equal or higher
  minor, "will definitely be able to read the file". It is a stated design rule ("should"), not a promise to
  decode old bitstreams forever, and data from a new compatibility line may be rejected by old decoders.
  MorletDB's own compatibility promise therefore needs an exact pin, golden pco chunks in the test corpus and an
  upgrade check that decodes them.
- The README recommends chunks of > 10 k numbers for good compression and calls the standalone format "mainly
  recommended for quick proofs of concept and benchmarking"; embedding should use the wrapped format.

## Recommendation

**Go** for "Grizzbit 2" with a pco chunk kind chosen per chunk by size (keep the smaller), at 4,096 samples,
pco level 8, with these conditions:

- pin `pco` exactly (`=1.0.x`), store golden pco chunks in the test corpus, and use pco's wrapped format inside
  MorletDB's own chunk frame (standalone is documented as for proofs of concept);
- encode cost roughly halves ingest throughput per chunk; measure B3 ingest in B5, and consider trying pco only
  when raw does not produce a grid chunk (Blockchain-tr shows that skips some wins; the trade-off is a B5
  design decision to freeze, not tune);
- Kani proofs cannot cover pco's decoder (third-party, ~100 `unsafe` sites); fuzz the new chunk kind and keep
  the bitwise verify-on-append (#366) in front of it.

An own float32/float-quant mode would recover most of XJTU-SY (float32 → at most 32 bits) but not POI-lat or the
near-grid gains; pco covers both, so it is the first thing to freeze in B5.
