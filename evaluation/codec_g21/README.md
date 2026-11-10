# Grizzbit 2.1: development measurement

**DEVELOPMENT EVIDENCE ONLY. This is not a statement, not a frozen study and not a claim.** It checks that the
Grizzbit 2.1 development build (#377; chunk format version 5, `grizzbit::encode_with(.., Format::Grizzbit21)`)
does what the T1 codec comparison (evaluation/codec_t261, branch `dev/t261-reference`, commit 2a89c40)
estimated, on the same development inputs. Nothing here may be quoted as a result. A frozen study (B6) has to
come first. It was run on 2026-11-10 on a shared Apple-silicon host, with every process at `nice -n 10`,
so the speeds are indicative only.

## What was measured

Each input is cut into chunks of 4,096 samples (MorletDB's default), and each chunk is encoded four ways. Every
chunk of every arm was decoded and compared bit for bit, with **0 mismatches**, and no 2.1 chunk was larger
than its Grizzbit 1 chunk (`g21_bench` asserts both):

| arm | encoder |
|---|---|
| Grizzbit 1 | `grizzbit::encode`, what MorletDB stores today (chunk format versions 2 and 3) |
| 2.1 corrections only | version 5 with idea 1 only: corrections once per grid index (table), sparse exceptions, or per sample, whichever is smallest |
| 2.1 LPC only | version 5 with idea 2 only: per-chunk LPC (order ≤ 32) beside the fixed predictors 0–4 |
| Grizzbit 2.1 | both ideas (`encode_with(.., Format::Grizzbit21)`) |

A version-5 chunk is written only when it is strictly smaller than the Grizzbit 1 chunk, so every arm is
Grizzbit 1 byte for byte wherever it does not help. Sizes include every chunk's header and CRC-32C.
Bits/sample = 8 × bytes / samples. Group means are weighted by sample count.

### Inputs (development only; no B3 or B4 benchmark input)

- **T1's development inputs**, written by `evaluation/codec_t261/prepare.py` (2a89c40):
  - five acquisitions of B3's *development* set (K001, KA01, KA09, KB24, KI08): vibration_1, phase_current_1
    and phase_current_2 (64 kHz, about 256,000 samples each), and force (4 kHz, about 16,000 samples);
  - FEMTO Bearing1_1 horizontal (4,193,280 samples, 0.001 grid).

  Two views of these inputs:
  - **as stored**: the original float64 with T1's quantum, as MorletDB would store it;
  - **on k\*q**: the exactly-on-grid values that T1 gave SRLA and T.261 as integers, the like-for-like view
    for the predictor.
- **B4's four development Elf series** (`evaluation/storage_b4/PROTOCOL.md`, "Development"): Blockchain-tr,
  POI-lat, Stocks-USA and electric_vehicle_charging, read as B4 reads them, at quantum 1 (B4's M-auto). They
  are low-rate decimal series, not waveforms.

## Results (4,096-sample chunks, bits/sample)

### As stored

| group | samples | Grizzbit 1 | 2.1 corrections only | 2.1 LPC only | Grizzbit 2.1 | 2.1 vs 1 |
|---|---|---|---|---|---|---|
| Paderborn 64 kHz (vibration + currents) | 3,840,330 | 10.317 | 9.696 | 9.206 | **8.585** | -1.732 (-16.8 %) |
| vibration (5) | 1,280,110 | 9.651 | 8.562 | 9.485 | **8.396** | -1.255 (-13.0 %) |
| phase currents (10) | 2,560,220 | 10.650 | 10.263 | 9.067 | **8.680** | -1.970 (-18.5 %) |
| force (5) | 80,010 | 9.040 | 7.901 | 9.030 | **7.890** | -1.150 (-12.7 %) |
| FEMTO Bearing1_1 | 4,193,280 | 10.654 | 10.524 | 10.222 | **10.091** | -0.563 (-5.3 %) |
| Elf, B4 development (4) | 303,397 | 26.271 | 26.271 | 26.268 | **26.268** | -0.003 (-0.0 %) |
| all 21 T1 inputs | 8,113,620 | 10.479 | 10.106 | 9.729 | **9.357** | -1.122 (-10.7 %) |

Gain from each idea alone, compared with T1's development estimates (#377):

| group | corrections, measured | T1 estimate | LPC, measured (on k\*q) | T1 estimate |
|---|---|---|---|---|
| vibration | -1.09 | -1.05 | -0.17 | -0.15 (estimate; SRLA measured -0.24) |
| force | -1.14 | -1.2 | -0.01 | 0 |
| phase currents | -0.39 | -0.4 | -1.58 | -1.49 |
| FEMTO | -0.13 (negative zeros) | -0.14 | -0.43 | -0.46 (-4.4 %) |
| all 21 | | | -0.75 | -0.70 |

### On k\*q, compared with SRLA and T.261 (from T1, not rerun)

| group | Grizzbit 1 | Grizzbit 2.1 | SRLA default (T1) | T.261 best (T1) | T.261 best, intra 4,096 (T1) |
|---|---|---|---|---|---|
| vibration | 8.330 | 8.164 | 8.091 | 7.945 | 8.026 |
| phase currents | 9.294 | 7.711 | 7.703 | 7.592 | 7.800 |
| force | 7.605 | 7.594 | 7.763 | 7.391 | 7.507 |
| FEMTO | 10.505 | 10.072 | 10.040 | 9.951 | 10.003 |
| all 21 | 9.751 | **9.001** | 8.973 | 8.865 | 8.971 |

On the same integers, Grizzbit 2.1 is within 0.03 bits/sample of SRLA overall. It is slightly behind on
vibration (+0.07) and FEMTO (+0.03), level on the phase currents (+0.01), and ahead on force (-0.17). T.261 is
0.14 lower. With its state reset every 4,096 samples, as a MorletDB chunk needs, T.261 is level with SRLA
(8.97). T1 attributes most of the rest to the entropy coder: about 0.05 to 0.1 for adaptive or arithmetic
coding.

### Share of chunks choosing each new path (Grizzbit 2.1)

| group | view | chunks | version 5 | table | exceptions only | per sample | no corrections | LPC |
|---|---|---|---|---|---|---|---|---|
| vibration | as stored | 315 | 100 % | 100 % | 0 % | 0 % | 0 % | 97 % |
| phase currents | as stored | 630 | 100 % | 100 % | 0 % | 0 % | 0 % | 100 % |
| force | as stored | 20 | 100 % | 100 % | 0 % | 0 % | 0 % | 20 % |
| FEMTO | as stored | 1,024 | 100 % | 0 % | 88 % | 0 % | 12 % | 100 % |
| Elf (B4 dev) | as stored | 76 | 1 % | 0 % | 0 % | 0 % | 1 % | 1 % |
| vibration | on k\*q | 315 | 93 % | — | — | — | 93 % | 93 % |
| phase currents | on k\*q | 630 | 100 % | — | — | — | 100 % | 100 % |
| force | on k\*q | 20 | 20 % | — | — | — | 20 % | 20 % |
| FEMTO | on k\*q | 1,024 | 100 % | — | — | — | 100 % | 100 % |

Per-input figures are in `results/dev.json` (`python3 -I evaluation/codec_g21/summarize.py` prints every table).

### Speed (M samples/s, single thread, best of 3; host load average about 1.5)

Speeds are sample-weighted harmonic means per group, over every chunk of every input, with the codec in
process. The last column is the slowest single input in the group.

| group | view | encode G1 | encode 2.1 | decode G1 | decode 2.1 | decode 2.1, slowest input |
|---|---|---|---|---|---|---|
| vibration | as stored | 19.2 | 5.7 | 79 | 74 | 63 (KB24) |
| phase currents | as stored | 19.4 | 5.2 | 77 | **53** | 53 (KB24 current_1) |
| force | as stored | 13.2 | 5.3 | 72 | 82 | 74 (KB24) |
| FEMTO | as stored | 24.0 | 6.6 | 95 | 109 | 109 |
| Elf (B4 dev) | as stored | 14.2 | 8.5 | 88 | 88 | 59 (POI-lat, float bits, unchanged) |
| vibration | on k\*q | 27.3 | 9.3 | 130 | 101 | 91 |
| phase currents | on k\*q | 27.8 | 8.7 | 125 | 89 | 88 |
| force | on k\*q | 26.9 | 10.3 | 119 | 119 | 115 |
| FEMTO | on k\*q | 32.0 | 9.5 | 120 | 110 | 110 |

- **Decode stays at or above 50 M samples/s on every waveform, but only just on the phase currents (53).**
  Order-32 LPC adds about 3 ns per sample. The table needs a pass that numbers indices by first occurrence,
  which adds about 2.5 ns per sample where 70 % of the indices are distinct. Decode speeds up where the table
  replaces most per-sample corrections: force, FEMTO, and vibration without LPC. T1 measured SRLA at 14–19
  and T.261 at about 5 M samples/s, process start included.
- **Encode is 3–4× slower than Grizzbit 1** (5–10 M samples/s), still about 80× real time at 64 kHz. The
  2.1 encoder runs the whole Grizzbit 1 encoder, then recodes each grid candidate. That means a second
  fixed-predictor search, the Welch-windowed autocorrelation to order 32, three LPC orders evaluated
  exactly, and the correction variants. None of this is optimised yet.

## Observations checked before building

- **The correction is a function of the grid index on all 20 Paderborn channels.** In every channel the
  number of distinct (index, float64) pairs equals the number of distinct indices. Distinct indices per
  4,096-sample chunk, as each channel's mean: vibration 9–29 %, force 11–32 %, phase currents 61–74 %.
- **It fails on FEMTO, and only at index 0:** 2,282 samples are -0.0 and the rest +0.0. The encoder handles
  the general case by keep-the-smaller. The table holds each index's most frequent correction, and samples
  that differ become exceptions; when that is not smaller, the chunk stores exceptions only or per-sample
  corrections. FEMTO's chunks use exceptions only (its negative zeros), or no corrections at all.
- **The Elf development series barely change.** POI-lat is coded as float bits, which version 5 does not
  touch. Blockchain-tr and Stocks-USA are grid chunks where neither the table nor any LPC order is smaller
  than Grizzbit 1: these low-rate series are not resonant. electric_vehicle_charging (one chunk)
  gains 2.3 % from LPC.

## Format (version 5) in short

The full layout and rules are in the crate docs (`crates/grizzbit/src/g21.rs`). A version-5 chunk is a
Grizzbit 1 grid chunk with these changes:

- A flags byte replaces `has_fix`. Bit 0 says an offset follows; bits 1–2 give the correction mode: none, per
  sample, table + exceptions, or exceptions only.
- Every stream may use predictor 7, LPC: order 1–32, coefficient precision 1–15 bits, shift 0–31. Its
  prediction is wrapping i64 arithmetic with no floating point, and the first `order` samples use fixed
  order 2.
- The table holds one correction per distinct index, in order of first occurrence. Exceptions are coded as
  position gaps plus corrections, each as a stream.

Version 4 is never reused (it was Grizzbit 2's pco kind, which failed B5). `grizzbit::FORMAT_VERSION`, and so
the transfer protocol's highest version, stays 3: a server would send samples, not version-5 chunks, to
current clients.

## What a frozen study (B6) should test next

1. **Inputs no development step has touched:** B3's benchmark acquisitions (all channels), the B4 waveform
   inputs (CX counts, IMS decimals, Paderborn counts and physical, Sleep-EDF, ECG) and all 22 Elf series.
   Pre-register per-input criteria, B4/B5 style, for example "as stored, ≥ 5 % smaller than Grizzbit 1 on
   Paderborn physical and on Sleep-EDF". By construction no chunk can grow, so also report how often version
   5 is chosen.
2. **Speed floors on stated reference hardware:** decode ≥ 50 M samples/s per waveform input, including
   order-32 LPC with a table, the worst case here (about 53 on this host). Also set an encode floor:
   2.1 encodes 3–4× slower than Grizzbit 1, because it recomputes the Grizzbit 1 candidates and searches LPC
   orders.
3. **Cross-platform byte identity of the encoder** (x86-64 and aarch64; Linux and macOS). The LPC fit is
   floating point, using IEEE basic operations only, and decoding is integer-only. B6 should run
   `testdata/rawcodec/v5-*` and a regenerated encoder-equivalence vector for version 5 on both architectures.
4. **The write path:** pg_morlet's `encode_checked` with version 5 behind a setting, mixed-version tables,
   the transfer protocol offering version 5, and the Python reference reader (`reference/python`) reading
   version 5.
5. **Ablations to settle before freezing the format:**
   - LPC precision (11–15 bits by chunk length here) and which orders are tried (the estimate, half and
     double);
   - the window (Welch), and the warm-up predictor (fixed order 2);
   - 32,768-sample chunks;
   - whether the per-index table should also be offered where Grizzbit 1 chose float-bit coding.
6. **Longer fuzzing and the Kani proofs in CI.** Nightly `decode_v5` and `roundtrip` runs, as for Grizzbit 1.

## Files and how to rerun

- `g21_bench/`: a small Rust binary outside the main workspace. It runs all four arms per input, checks
  every chunk bit for bit, measures speed, and writes one JSON line.
- `run.py`: runs every input (T1's work directory and B4's development Elf series) and writes
  `results/dev.json`.
- `summarize.py`: prints the tables above.

```
# T1's inputs: evaluation/codec_t261/prepare.py (branch dev/t261-reference, 2a89c40) -> WORK
(cd evaluation/codec_g21/g21_bench && cargo build --release)
nice -n 10 python3 -I evaluation/codec_g21/run.py WORK data/b4-elf evaluation/codec_g21/results/dev.json --reps 3
python3 -I evaluation/codec_g21/summarize.py
```
