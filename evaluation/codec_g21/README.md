# Grizzbit 2.1: development measurement

**DEVELOPMENT EVIDENCE ONLY. This is not a statement, not a frozen study and not a claim.** It checks that the
Grizzbit 2.1 development build (#377; chunk format version 5, `grizzbit::encode_with(.., Format::Grizzbit21)`)
does what the T1 codec comparison (evaluation/codec_t261, branch `dev/t261-reference`, commit 2a89c40)
estimated, on the same development inputs. Nothing here may be quoted as a result. A frozen study (B6) has to
come first.

It was first run on 2026-11-10. It was rerun on 2026-11-11 after the encoder tuning described under
"Encoder tuning", and the tables below are from that rerun. The host is a shared Apple-silicon machine;
every process ran at `nice -n 10` with a 1-minute load average of about 1.7. Speeds are indicative only.

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
| Paderborn 64 kHz (vibration + currents) | 3,840,330 | 10.317 | 9.696 | 9.207 | **8.586** | -1.731 (-16.8 %) |
| vibration (5) | 1,280,110 | 9.651 | 8.562 | 9.487 | **8.398** | -1.253 (-13.0 %) |
| phase currents (10) | 2,560,220 | 10.650 | 10.263 | 9.067 | **8.680** | -1.971 (-18.5 %) |
| force (5) | 80,010 | 9.040 | 7.901 | 9.040 | **7.901** | -1.139 (-12.6 %) |
| FEMTO Bearing1_1 | 4,193,280 | 10.654 | 10.524 | 10.222 | **10.092** | -0.562 (-5.3 %) |
| Elf, B4 development (4) | 303,397 | 26.271 | 26.271 | 26.268 | **26.268** | -0.003 (-0.0 %) |
| all 21 T1 inputs | 8,113,620 | 10.479 | 10.106 | 9.730 | **9.357** | -1.121 (-10.7 %) |

Gain from each idea alone, compared with T1's development estimates (#377):

| group | corrections, measured | T1 estimate | LPC, measured (on k\*q) | T1 estimate |
|---|---|---|---|---|
| vibration | -1.09 | -1.05 | -0.16 | -0.15 (estimate; SRLA measured -0.24) |
| force | -1.14 | -1.2 | 0.00 | 0 |
| phase currents | -0.39 | -0.4 | -1.58 | -1.49 |
| FEMTO | -0.13 (negative zeros) | -0.14 | -0.43 | -0.46 (-4.4 %) |
| all 21 | | | -0.75 | -0.70 |

### On k\*q, compared with SRLA and T.261 (from T1, not rerun)

| group | Grizzbit 1 | Grizzbit 2.1 | SRLA default (T1) | T.261 best (T1) | T.261 best, intra 4,096 (T1) |
|---|---|---|---|---|---|
| vibration | 8.330 | 8.165 | 8.091 | 7.945 | 8.026 |
| phase currents | 9.294 | 7.711 | 7.703 | 7.592 | 7.800 |
| force | 7.605 | 7.605 | 7.763 | 7.391 | 7.507 |
| FEMTO | 10.505 | 10.072 | 10.040 | 9.951 | 10.003 |
| all 21 | 9.751 | **9.002** | 8.973 | 8.865 | 8.971 |

On the same integers, Grizzbit 2.1 is within 0.03 bits/sample of SRLA overall. It is slightly behind on
vibration (+0.07) and FEMTO (+0.03), level on the phase currents (+0.01), and ahead on force (-0.16). T.261 is
0.14 lower. With its state reset every 4,096 samples, as a MorletDB chunk needs, T.261 is level with SRLA
(8.97). T1 attributes most of the rest to the entropy coder: about 0.05 to 0.1 for adaptive or arithmetic
coding.

### Share of chunks choosing each new path (Grizzbit 2.1)

| group | view | chunks | version 5 | table | exceptions only | per sample | no corrections | LPC |
|---|---|---|---|---|---|---|---|---|
| vibration | as stored | 315 | 100 % | 100 % | 0 % | 0 % | 0 % | 95 % |
| phase currents | as stored | 630 | 100 % | 100 % | 0 % | 0 % | 0 % | 100 % |
| force | as stored | 20 | 100 % | 100 % | 0 % | 0 % | 0 % | 0 % |
| FEMTO | as stored | 1,024 | 100 % | 0 % | 88 % | 0 % | 12 % | 100 % |
| Elf (B4 dev) | as stored | 76 | 1 % | 0 % | 0 % | 0 % | 1 % | 1 % |
| vibration | on k\*q | 315 | 91 % | — | — | — | 91 % | 91 % |
| phase currents | on k\*q | 630 | 100 % | — | — | — | 100 % | 100 % |
| force | on k\*q | 20 | 0 % | — | — | — | 0 % | 0 % |
| FEMTO | on k\*q | 1,024 | 100 % | — | — | — | 100 % | 100 % |

Per-input figures are in `results/dev.json` (`python3 -I evaluation/codec_g21/summarize.py` prints every table).

### Speed (M samples/s, single thread, best of 3)

Speeds are sample-weighted harmonic means per group, over every chunk of every input, with the codec in
process. The last column is the slowest single input in the group. Grizzbit 1's encoder is faster here than
on 2026-11-10 (19 to 33 on the waveforms) because of the tuning below: its bytes are unchanged.

| group | view | encode G1 | encode 2.1 | decode G1 | decode 2.1 | decode 2.1, slowest input |
|---|---|---|---|---|---|---|
| vibration | as stored | 33.1 | **17.0** | 79 | 75 | 63 (KB24) |
| phase currents | as stored | 32.9 | **15.2** | 77 | **53** | 53 (KA09 current_1) |
| force | as stored | 19.5 | **14.0** | 72 | 84 | 77 (KB24) |
| FEMTO | as stored | 42.5 | **24.6** | 94 | 108 | 108 |
| Elf (B4 dev) | as stored | 27.5 | 23.7 | 88 | 87 | 59 (POI-lat, float bits, unchanged) |
| vibration | on k\*q | 49.0 | 29.3 | 130 | 103 | 91 |
| phase currents | on k\*q | 48.2 | 27.4 | 125 | 90 | 89 |
| force | on k\*q | 47.2 | 33.6 | 120 | 119 | 116 |
| FEMTO | on k\*q | 62.1 | 34.4 | 119 | 109 | 109 |

- **Decode stays at or above 50 M samples/s on every waveform, but only just on the phase currents (53).**
  Order-32 LPC adds about 3 ns per sample. The table needs a pass that numbers indices by first occurrence,
  which adds about 2.5 ns per sample where 70 % of the indices are distinct. Decode speeds up where the table
  replaces most per-sample corrections: force, FEMTO, and vibration without LPC. T1 measured SRLA at 14–19
  and T.261 at about 5 M samples/s, process start included. The tuning did not touch the decoder.
- **Encode is 15–25 M samples/s on the waveforms, about 2× slower than Grizzbit 1** (5–7 before tuning).
  That is about 240× real time at 64 kHz. Force is the slowest at 14.0, and Grizzbit 1's own path accounts
  for most of it (19.5 alone): force sits on an offset grid with several candidate grids.

## Encoder tuning (2026-11-11)

### Where the time went (macOS `sample`, 2.1 encode of K001 phase_current_1, before tuning)

| function | share |
|---|---|
| `partition_search` (Rice cost per stream, predictor and partition order) | 38 % |
| `BitWriter::put` (stream writing) | 15 % |
| LPC fit: Welch window, autocorrelation to lag 32, Levinson-Durbin, quantisation | 12 % |
| LPC residuals (3 candidate orders, plus the winner recomputed) | 11 % |
| fixed-predictor residuals | 8 % |
| the rest (grid fit, first-occurrence numbering, corrections) | 16 % |

Much of this was repeated work. The 2.1 encoder ran the whole Grizzbit 1 encoder, then searched the index
stream's fixed predictors again, rewrote that stream, and re-encoded the per-sample corrections.

### Changes

Steps 1 to 6 change no byte of any Grizzbit 1 or version-5 chunk: the Grizzbit 1 goldens,
`encoder-equivalence.bin` and every version-5 golden still match, and new unit tests check the fast paths
against their oracles.

1. **Reuse.** Each Grizzbit 1 grid candidate keeps its index stream, that stream's cost and its correction
   stream. Version 5 reuses them unless an LPC stream is smaller.
2. **Rice-parameter search.** `best_param` runs in u64 arithmetic when the sum cannot overflow, and starts
   its walk at a proven lower bound of the first minimum. A test compares it with the exhaustive scan on
   20,000 cases.
3. **Partition sums.** Sums are taken in u64 where the partition's maximum shows they cannot overflow.
4. **Bit writer.** `BitWriter` emits 32-bit words and writes a Rice code in one step. A test compares it with
   a bit-by-bit writer.
5. **Grid fit.** `best_grid` scores each candidate grid in one streaming pass and builds only the winner's
   corrections.
6. **Fewer wasted candidates.**
   - LPC residuals are not recomputed when the last candidate won.
   - The autocorrelation runs all lags side by side, with each lag still summed in sample order.
   - Correction variants that cannot win are skipped by exact lower bounds: m exceptions cost at least 3m
     bits, and a table of d entries at least d bits.

Two changes are allowed to change version-5 bytes. Neither changed any golden.

7. **Side streams:** table, exception-gap and exception-value streams try fixed predictors 0 and 1 only,
   not 0–4. These streams are nearly white. On K001 the sizes were identical to three decimals; order 0
   alone lost 0.007 on force.
8. **LPC order selection:** the default became the estimate alone (below).

**Determinism.** The LPC fit uses IEEE basic operations only, in a fixed order:
- no fused multiply-add (Rust never contracts `a * b + c` unless `mul_add` is called);
- no libm;
- each autocorrelation lag summed in sample order.

The encoder therefore writes the same bytes on every platform. CI reproduces on Linux the version-5 goldens
written on macOS/aarch64.

### LPC order-selection strategies (as stored, both ideas on, after changes 1–7)

Each cell is bits/sample / encode / decode in M samples/s (harmonic mean). All strategies use the same
format and decoder. `results/dev_strategies.json` has every input;
`summarize.py results/dev_strategies.json` prints the per-input table.

| strategy | Paderborn 64 kHz | vibration | phase currents | force | FEMTO | Elf (B4 dev) | all 21 |
|---|---|---|---|---|---|---|---|
| Grizzbit 1 | 10.317 / 33.1 / 78 | 9.651 / 33.4 / 80 | 10.650 / 33.0 / 78 | 9.040 / 19.5 / 72 | 10.654 / 41.8 / 95 | 26.271 / 27.4 / 88 | 10.479 / 36.8 / 86 |
| around | 8.585 / 13.1 / 59 | 8.396 / 14.6 / 74 | 8.680 / 12.4 / 53 | 7.890 / 12.1 / 84 | 10.091 / 18.6 / 109 | 26.268 / 21.5 / 88 | 9.357 / 15.4 / 77 |
| **estimated** | 8.586 / **15.8** / 59 | 8.398 / **17.1** / 75 | 8.680 / **15.2** / 53 | 7.901 / **13.9** / 86 | 10.092 / **24.6** / 109 | 26.268 / 23.7 / 88 | **9.357 / 19.4** / 77 |
| est+max | 8.583 / 13.7 / 58 | 8.391 / 14.9 / 72 | 8.680 / 13.1 / 53 | 7.901 / 12.8 / 85 | 10.092 / 19.7 / 109 | 26.268 / 21.6 / 88 | 9.356 / 16.2 / 77 |
| fixed {8,16,32} | 8.589 / 14.3 / 59 | 8.398 / 15.0 / 75 | 8.685 / 14.0 / 53 | 7.901 / 12.3 / 85 | 10.093 / 19.7 / 118 | 26.268 / 20.8 / 88 | 9.360 / 16.7 / 80 |
| fixed {12,32} | 8.589 / 14.9 / 59 | 8.399 / 15.8 / 75 | 8.685 / 14.5 / 53 | 7.901 / 12.7 / 86 | 10.105 / 20.8 / 111 | 26.268 / 21.6 / 88 | 9.366 / 17.4 / 78 |
| fixed {32} | 8.589 / 15.7 / 59 | 8.398 / 16.7 / 74 | 8.685 / 15.2 / 53 | 7.901 / 13.3 / 85 | 10.124 / 23.3 / 89 | 26.269 / 22.8 / 88 | 9.376 / 18.9 / 72 |
| skip-20 | 8.586 / 15.6 / 59 | 8.398 / 16.9 / 75 | 8.680 / 15.1 / 53 | 7.901 / 14.0 / 86 | 10.092 / 24.2 / 109 | 26.268 / 24.7 / 87 | 9.357 / 19.1 / 78 |
| skip-50 | 8.586 / 15.7 / 59 | 8.398 / 16.9 / 75 | 8.680 / 15.1 / 53 | 7.901 / 14.0 / 86 | 10.092 / 24.2 / 109 | 26.268 / 24.1 / 88 | 9.357 / 19.1 / 77 |

The strategies:
- **around**: the estimated order, its half and its double (the first build).
- **estimated**: only the order with the smallest code length estimated from the Levinson errors,
  `n/2 · log2(error) + order · precision`.
- **est+max**: the estimated order and order 32.
- **fixed {…}**: those orders, each capped at n/8.
- **skip-20 / skip-50**: the estimated order, skipped when an absolute size estimate does not beat the best
  fixed predictor by 0.02 / 0.05 bits/sample.

"Reuse the previous chunk's order" was not tried. Chunks are encoded independently through a stateless API,
and a dependence on the previous chunk would make a chunk's bytes depend on how the stream was cut.

**Default: `estimated`.**
- It is the fastest strategy, or within 0.1 M samples/s of skip-20, on every group.
- Its size equals the exhaustive `around` over all 21 inputs (9.357). Its largest group loss is +0.011
  bits/sample, on force. That comes entirely from KI08 force (16,001 samples, +0.055), where an order other
  than the estimate wins.
- **est+max** saves 0.001 more for about 20 % more encode time. **The fixed sets** lose up to 0.03 on FEMTO
  and 0.11 on electric_vehicle_charging.
- **The skip variants** are equal in size and no faster: on these inputs LPC nearly always wins, so the
  skip test rarely fires.

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
2. **Speed floors on stated reference hardware**, both measured single-thread with the codec in process:
   - **Decode:** at least 50 M samples/s per waveform input, including order-32 LPC with a table, the worst
     case here (53 on this host).
   - **Encode:** proposed at least 10 M samples/s per waveform input as stored. The slowest here is 13.2
     (KB24 force); the phase currents run at 15.1–15.4. A floor of 10 leaves room for slower reference
     hardware and is still about 150× real time at 64 kHz.
3. **Cross-platform byte identity of the encoder** (x86-64 and aarch64; Linux and macOS). The LPC fit is
   floating point, using IEEE basic operations only, and decoding is integer-only. B6 should run
   `testdata/rawcodec/v5-*` and a regenerated encoder-equivalence vector for version 5 on both architectures.
4. **The write path:** pg_morlet's `encode_checked` with version 5 behind a setting, mixed-version tables,
   the transfer protocol offering version 5, and the Python reference reader (`reference/python`) reading
   version 5.
5. **Ablations to settle before freezing the format:**
   - LPC precision (11–15 bits by chunk length here). The order selection is settled for development as
     the estimate alone; B6 can freeze it as is;
   - the window (Welch), and the warm-up predictor (fixed order 2);
   - 32,768-sample chunks;
   - whether the per-index table should also be offered where Grizzbit 1 chose float-bit coding.
6. **Longer fuzzing and the Kani proofs in CI.** Nightly `decode_v5` and `roundtrip` runs, as for Grizzbit 1.

## Files and how to rerun

- `g21_bench/`: a small Rust binary outside the main workspace. It runs all four arms per input (with
  `G21_STRATEGIES=1`, Grizzbit 1 and the eight LPC strategies), checks every chunk bit for bit, measures
  speed, and writes one JSON line. `g21_bench loop FILE QUANTUM CHUNK PASSES` only encodes, for profiling.
- `run.py`: runs every input (T1's work directory and B4's development Elf series) and writes
  `results/dev.json`. With `--strategies --view "as stored"` it writes `results/dev_strategies.json`.
- `summarize.py`: prints the tables above.

```
# T1's inputs: evaluation/codec_t261/prepare.py (branch dev/t261-reference, 2a89c40) -> WORK
(cd evaluation/codec_g21/g21_bench && cargo build --release)
nice -n 10 python3 -I evaluation/codec_g21/run.py WORK data/b4-elf evaluation/codec_g21/results/dev.json --reps 3
nice -n 10 python3 -I evaluation/codec_g21/run.py WORK data/b4-elf evaluation/codec_g21/results/dev_strategies.json \
  --reps 3 --view "as stored" --strategies
python3 -I evaluation/codec_g21/summarize.py
python3 -I evaluation/codec_g21/summarize.py evaluation/codec_g21/results/dev_strategies.json
```
