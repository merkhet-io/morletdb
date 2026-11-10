# Codec comparison T1: Grizzbit 1 vs T.261 (lossless) vs SRLA on integer waveforms

**Development evidence only.** This is not a frozen study and makes no public claim. It looks for headroom in
Grizzbit 1's grid (integer) coding by comparing it with two lossless integer-waveform codecs on the same
information, and turns what it finds into concrete ideas for Grizzbit. Run on 2026-11-10 on a busy shared
host (load average 11 to 15, every process at `nice -n 10`). Speeds are indicative only.

## What was measured

- **Grizzbit 1**: the codec MorletDB stores chunks with (`crates/grizzbit`, chunk format versions 2 and 3),
  `grizzbit::encode` per chunk of 4,096 samples (MorletDB's default) and of 32,768 samples. Sizes include
  every chunk's header and CRC-32C. Every chunk was decoded and compared bit for bit: 0 failures.
- **T.261 lossless**: the ITU-T T.261 reference software (formerly "Biomedical Waveform Codec"),
  version 7.0, with each lossless preset: `combinedPreset{EEG,EMG,ECG,ACoM}_IndepChannel_lossless.cfg` for
  single channels, and `combinedPreset{...}_lossless.cfg` for the joint three-channel view. Sizes are the
  whole bitstream, including the default high-level packets (about 133 bytes per file). The best preset is
  also run with `--IntraPeriod=4096` so that its state resets every 4,096 samples, as a MorletDB chunk must.
- **SRLA**: the lossless LPC audio codec (its `srla` tool, codec version 18, format 10), on the same integers as
  a mono 16-bit WAV, at its default (`-m 4`, LPC order up to 64), its maximum preset (`-m 6`, order up to 255)
  and "max+" (`-m 6 -V 2 -L 8 -P 3`: deeper variable-block search, longer lookahead, long-term prediction).
- **Estimates** (`grizzbit_bench ideas`): code sizes, not codecs, for candidate Grizzbit predictors and coders
  on the same integers, per independent chunk (all state resets at a chunk start). See "Ideas" below.

Bits/sample is 8 x bytes / samples. Speed is single thread, best of 3: Grizzbit in process over all chunks;
T.261 as reported by its encoder and decoder (file I/O included, process start excluded); SRLA as the tool's
wall time (process start and file I/O included, which dominates on the 16,001-sample force channels).

### Inputs (development only)

- Five acquisitions of B3's development set (`B3_SET=dev`, positions 0, 25, 50, 75 and 100 of its 124 files:
  K001, KA01, KA09, KB24, KI08), every channel of the 64 kHz group (vibration_1, phase_current_1, phase_current_2;
  256,001 to 256,106 samples) and the 4 kHz group (force, speed, torque; about 16,000 samples).
- FEMTO Bearing1_1 horizontal (column 4 of `acc_*.csv`, 4,193,280 samples, 0.001 grid) and XJTU-SY Bearing1_1
  horizontal, the two non-B4 waveforms of the Grizzbit 2 spike.

### Integer conversion: same information on both sides

T.261 and SRLA take integers; Grizzbit stores float64 and detects the grid itself. `prepare.py` looks for a step
q (and an offset) such that every sample is offset + k*q for an integer k:

- **exact** (x == k*q bit for bit): none of the inputs. FEMTO is exact except for 2,282 negative zeros
  (`-0.000` in the CSV), which an integer cannot carry; Grizzbit stores them as last-place corrections.
- **near** (every sample within 1e-3 steps of a grid point, Grizzbit's own tolerance, but not bit for bit):
  all 20 Paderborn vibration, phase-current and force channels (vibration at 100/32768 within 2.2e-6 steps;
  currents at 0.0006887817378 within 1.9e-7 steps; force on an offset grid of about 0.30518 within 2e-9 steps),
  and FEMTO (negative zeros only). Grizzbit stores these as integers plus last-place corrections that T.261
  and SRLA cannot represent, so the like-for-like comparison is Grizzbit on the exactly-on-grid values k*q
  ("Grizzbit 1 on k*q") against T.261 and SRLA on k. Grizzbit's real cost on the original float64 ("as stored")
  is reported beside it.
- **excluded**: speed and torque of all five acquisitions (16,001 distinct values in 16,001 samples: no grid;
  they are derived quantities); temp_2_bearing_module (5 samples at 1 Hz, too short to code); XJTU-SY
  Bearing1_1 (float32-derived decimals, not on any grid: T.261 takes integers only, so not applicable).

All indices fit in 16 bits (11 to 15 bits). T.261 gets `--InputBitDepth=16`: its encoder aborts with bit
depths 13, 14 and 15 (seen on 12- and 14-bit inputs), and 16 codes no larger than the minimal 12 where both run.

## Results

### Bits per sample, per channel

| input | class | Grizzbit 1 as stored, 4,096 / 32,768 | Grizzbit 1 on k*q, 4,096 / 32,768 | SRLA default / max / max+ | T.261 EEG / EMG / ECG / ACoM | T.261 best, intra 4,096 |
|---|---|---|---|---|---|---|
| K001 vibration | near | 9.443 / 9.401 | 8.189 / 8.151 | 8.271 / 8.271 / 8.273 | 7.982 / 7.940 / 7.919 / 7.928 | 7.983 (ECG) |
| K001 current_1 | near | 10.583 / 10.517 | 9.226 / 9.163 | 7.907 / 7.910 / 8.057 | 8.021 / 7.817 / 7.856 / 7.806 | 8.033 (ACoM) |
| K001 current_2 | near | 10.771 / 10.729 | 9.411 / 9.373 | 7.529 / 7.530 / 7.753 | 7.658 / 7.518 / 7.569 / 7.502 | 7.687 (ACoM) |
| K001 force | near | 8.925 / 9.204 | 7.375 / 7.515 | 7.574 / 7.574 / 7.724 | 7.363 / 7.364 / 7.341 / 7.356 | 7.380 (ECG) |
| KA01 vibration | near | 9.593 / 9.552 | 8.231 / 8.193 | 7.710 / 7.710 / 7.734 | 7.811 / 7.625 / 7.630 / 7.612 | 7.774 (ACoM) |
| KA01 current_1 | near | 10.517 / 10.447 | 9.166 / 9.100 | 7.491 / 7.491 / 7.760 | 7.523 / 7.345 / 7.369 / 7.328 | 7.521 (ACoM) |
| KA01 current_2 | near | 10.617 / 10.547 | 9.262 / 9.195 | 7.520 / 7.520 / 7.813 | 7.564 / 7.395 / 7.424 / 7.378 | 7.567 (ACoM) |
| KA01 force | near | 8.919 / 9.185 | 7.516 / 7.513 | 7.701 / 7.701 / 7.533 | 7.422 / 7.346 / 7.386 / 7.284 | 7.432 (ACoM) |
| KA09 vibration | near | 9.371 / 9.317 | 8.045 / 7.995 | 7.779 / 7.779 / 7.791 | 7.777 / 7.768 / 7.765 / 7.750 | 7.800 (ACoM) |
| KA09 current_1 | near | 10.570 / 10.480 | 9.216 / 9.130 | 7.720 / 7.720 / 7.835 | 7.780 / 7.614 / 7.637 / 7.594 | 7.787 (ACoM) |
| KA09 current_2 | near | 10.749 / 10.677 | 9.392 / 9.323 | 7.737 / 7.737 / 7.903 | 7.803 / 7.623 / 7.654 / 7.613 | 7.811 (ACoM) |
| KA09 force | near | 8.935 / 8.960 | 7.415 / 7.455 | 7.618 / 7.622 / 7.542 | 7.307 / 7.283 / 7.291 / 7.260 | 7.326 (ACoM) |
| KB24 vibration | near | 10.999 / 10.947 | 9.590 / 9.543 | 9.297 / 9.298 / 9.298 | 9.319 / 9.220 / 9.234 / 9.204 | 9.268 (ACoM) |
| KB24 current_1 | near | 10.648 / 10.557 | 9.292 / 9.204 | 8.001 / 8.003 / 8.085 | 8.059 / 7.857 / 7.906 / 7.842 | 8.047 (ACoM) |
| KB24 current_2 | near | 10.770 / 10.732 | 9.410 / 9.376 | 7.639 / 7.641 / 7.794 | 7.705 / 7.520 / 7.583 / 7.502 | 7.701 (ACoM) |
| KB24 force | near | 10.008 / 10.458 | 8.634 / 8.671 | 8.628 / 8.563 / 8.745 | 8.628 / 8.173 / 8.171 / 8.155 | 8.444 (ACoM) |
| KI08 vibration | near | 8.850 / 8.816 | 7.592 / 7.561 | 7.399 / 7.399 / 7.408 | 7.399 / 7.269 / 7.242 / 7.261 | 7.305 (ECG) |
| KI08 current_1 | near | 10.511 / 10.445 | 9.158 / 9.095 | 7.676 / 7.679 / 7.822 | 7.833 / 7.612 / 7.638 / 7.612 | 7.864 (EMG) |
| KI08 current_2 | near | 10.766 / 10.705 | 9.408 / 9.350 | 7.809 / 7.814 / 8.016 | 7.960 / 7.748 / 7.778 / 7.745 | 7.978 (ACoM) |
| KI08 force | near | 8.415 / 8.585 | 7.085 / 7.280 | 7.296 / 7.296 / 7.106 | 6.952 / 6.940 / 6.913 / 6.932 | 6.952 (ECG) |
| FEMTO Bearing1_1 horiz | near (-0.0) | 10.654 / 10.627 | 10.505 / 10.464 | 10.040 / 10.040 / 10.047 | 9.951 / 10.026 / 10.131 / 9.951 | 10.003 (ACoM) |

### Group means (sample-weighted bits/sample, 4,096-sample chunks for Grizzbit and the estimates)

| group | samples | Grizzbit 1 on k*q | est. fixed + Rice | est. fixed + AC | est. LPC + Rice | est. LPC + AC | est. LPC/NLMS + AC | SRLA default | SRLA max | T.261 best | T.261 best, intra 4,096 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| vibration (5) | 1,280,110 | 8.330 | 8.279 | 8.167 | 8.127 | 8.017 | 7.940 | 8.091 | 8.091 | 7.945 | 8.026 |
| phase currents (10) | 2,560,220 | 9.294 | 9.181 | 9.118 | 7.694 | 7.594 | 7.589 | 7.703 | 7.704 | 7.592 | 7.800 |
| force (5) | 80,010 | 7.605 | 7.551 | 7.530 | 7.551 | 7.518 | 7.493 | 7.763 | 7.751 | 7.391 | 7.507 |
| FEMTO | 4,193,280 | 10.505 | 10.443 | 10.407 | 10.045 | 10.035 | 10.032 | 10.040 | 10.040 | 9.951 | 10.003 |
| all 21 | 8,113,620 | 9.751 | 9.675 | 9.619 | 8.976 | 8.921 | 8.906 | 8.973 | 8.973 | 8.865 | 8.971 |

"est. fixed + Rice" re-derives Grizzbit's own integer stream (it is 0.05 to 0.06 bits/sample below the real
chunks, the chunk header and CRC-32C); the estimate columns are explained under "Ideas". Splitting each 4,096
chunk into 1,024-sample blocks with their own predictor gained nothing (identical to "LPC + Rice"), so it is
left out of the table.

### Lossless checks

- T.261: 125 bitstreams (21 channels x 4 presets, 21 at intra period 4,096, 5 joint files x 4 presets) decoded
  with DecoderApp and compared byte for byte with the input integers: **0 mismatched samples**. The encoder's
  own reconstruction (`.rec`) also matched every time.
- SRLA: 63 files (21 channels x 3 settings) decoded and compared sample for sample: **0 mismatches**.
- Grizzbit: every chunk decoded bit for bit, on the original float64 and on k*q: **0 failures**.

### Speed (M samples/s, single thread, best of 3; busy host)

| codec | encode | decode |
|---|---|---|
| Grizzbit 1, 4,096-sample chunks, on k*q (Paderborn / FEMTO) | 18 / 33 | 100 / 140 |
| Grizzbit 1 as stored (with corrections), all inputs | 15.1 | 66.4 |
| SRLA default (`-m 4`), all inputs | 10.4 | 19.0 |
| SRLA max (`-m 6`), all inputs | 6.9 | 14.3 |
| T.261 EEG preset (fastest encoder) | 1.27 | 5.3 |
| T.261 EMG / ECG preset | 0.15 / 0.14 | 5.1 / 5.0 |
| T.261 ACoM preset (usually the smallest) | 0.06 | 5.2 |

SRLA's figures include process start and WAV I/O (on the 16,001-sample force files they are mostly that).

### T.261 tool ablations (best preset, one run each, bits/sample)

| input | preset | base | LMS off | cross-ch. LMS off | linear model off | block matching off | LPC+DCT off | zero-LSB off | LMS order 8 | LMS order 32 |
|---|---|---|---|---|---|---|---|---|---|---|
| K001 vibration | ECG | 7.919 | 7.920 | 7.919 | 7.919 | 7.919 | 7.919 | 7.919 | 7.919 | 7.917 |
| K001 force | ECG | 7.341 | 7.343 | 7.341 | 7.341 | 7.322 | 7.341 | 7.340 | 7.343 | 7.344 |
| K001 current_1 | ACoM | 7.806 | 7.806 | 7.806 | 7.806 | 8.017 | 7.808 | 7.806 | 7.806 | 7.806 |
| FEMTO | ACoM | 9.951 | 9.951 | 9.951 | 9.951 | 9.950 | 9.951 | 9.951 | 9.951 | 9.951 |

The encoder reports no mode statistics (the decoder only prints block sizes), so tools were switched off one at
a time. Only block matching (prediction from an earlier stretch of the same signal) matters, and only on the
phase currents (0.21 bits/sample). The LMS stages, the linear model, the LPC+DCT path and the zero-LSB flag each
change sizes by less than 0.003 bits/sample here. What is left, the per-block prediction and the context-adaptive
binary arithmetic coder with adaptive probabilities, cannot be switched off and carries the rest of the gain.

### Joint view: the three 64 kHz channels of one acquisition together (bits/sample over all three)

| acquisition | T.261 joint EEG / EMG / ECG / ACoM | T.261 per-channel best, summed | Grizzbit 1 on k*q, sum | Grizzbit 1 as stored, sum |
|---|---|---|---|---|
| K001 | 7.789 / 7.694 / 7.767 / 7.686 | 7.742 | 8.942 | 10.266 |
| KA01 | 7.597 / 7.523 / 7.536 / 7.517 | 7.439 | 8.886 | 10.242 |
| KA09 | 7.763 / 7.684 / 7.683 / 7.667 | 7.652 | 8.884 | 10.230 |
| KB24 | 8.346 / 8.224 / 8.264 / 8.215 | 8.183 | 9.431 | 10.805 |
| KI08 | 7.629 / 7.521 / 7.534 / 7.531 | 7.533 | 8.719 | 10.042 |

Joint coding (inter-channel prediction) is no better than coding each channel alone, and slightly worse in 4 of
5 acquisitions: vibration and the two phase currents share too little for it to pay. Grizzbit's lack of a joint
mode costs nothing on these signals.

## Reading

**Three-way, on the same integers (all 21 channels, sample-weighted).** Grizzbit 1 (fixed polynomial
predictors of order 0 to 4, partitioned Rice) codes at **9.75** bits/sample; SRLA (per-block LPC, order chosen by
estimated code length, adaptive block division, partitioned recursive Golomb-Rice) at **8.97**; T.261 (block
prediction tools, block matching, context-adaptive binary arithmetic coding) at **8.86** with the best preset per
channel (8.97 with its state reset every 4,096 samples).

- **LPC alone is most of the gap**: 0.78 bits/sample (8 %) from Grizzbit to SRLA, and the chunk-local estimate
  "LPC + Rice" (8.98) reproduces SRLA almost exactly. It is concentrated on the phase currents (9.29 -> 7.70,
  -17 %; strongly periodic inverter currents, where order-32 LPC wins every chunk) and FEMTO (10.51 -> 10.04,
  -4.4 %); vibration gains 0.24 (-2.9 %); force gains nothing (SRLA is 0.16 worse on 16,001-sample files, where
  its per-block parameters do not amortise).
- **Adaptive segmentation adds nothing measurable here**: per-1,024-block predictor choice inside a chunk gained
  0.000 in the estimate, and SRLA's deeper block-division search (`max+`) was worse than its default on 18 of 21
  channels (its long-term prediction `-P 3` did not pay on these signals either).
- **Arithmetic coding on top is worth about 0.05 to 0.1 bits/sample**: the estimate "LPC + AC" is 0.055 below
  "LPC + Rice", and T.261 is 0.107 below SRLA overall (0.11 on currents, 0.15 on vibration, 0.09 on FEMTO).
  Most of T.261's remaining lead over SRLA on vibration (0.15) is matched by the NLMS estimate (7.94 vs 7.95).
- **T.261 wins everywhere, but by little over SRLA, and at a large speed cost**: its encoder runs at 0.06 to 1.3
  M samples/s (ACoM, usually the smallest, is the slowest) against Grizzbit's 15 to 33, and it decodes at about
  5 M samples/s against Grizzbit's 66 to 140. With its state reset every 4,096 samples (as a MorletDB chunk
  needs) it loses 0.05 to 0.2 bits/sample: overall it then ties SRLA (8.97), better on vibration, FEMTO and
  force, worse on the phase currents.
- **On the real Paderborn float64, the larger cost is not the integer coder at all**: Grizzbit pays 1.25 to 1.55
  bits/sample for last-place corrections (as stored vs on k*q), more than the whole LPC gap on vibration and
  force. FEMTO's 2,282 negative zeros alone cost 0.15 bits/sample. Neither codec compared here has to carry this,
  which is why the as-stored column is not part of the comparison; but it is the cheapest win for Grizzbit.

## Ideas for Grizzbit's grid coding (development estimates; each needs its own spike before any format change)

All estimates are per independent 4,096-sample chunk, on the measured inputs, sample-weighted; a format change
would be a new chunk format version and a new Grizzbit number.

1. **Corrections only at the first occurrence of each index in a chunk.** On all 20 Paderborn channels the
   float64 value is a function of its grid index (checked: distinct (k, x) pairs == distinct k), so a correction
   needs coding once per distinct k, not once per sample; the decoder sees k before it needs the correction.
   A chunk holds 16 % (vibration), 15 % (force) and 70 % (currents) distinct indices at 4,096 samples (4 %, 7 %,
   18 % at 32,768), so the estimated saving on the as-stored size is about **1.05** bits/sample on vibration,
   **1.2** on force and **0.4** on the currents, with no decoder cost worth naming. The encoder must check the
   function property per chunk and fall back to per-sample corrections otherwise. A sparse form (positions plus
   values) would likewise make FEMTO's negative zeros nearly free (0.15 -> about 0.01).
2. **Per-chunk LPC as an extra predictor** beside the fixed orders: orders 2 to 32 by Levinson-Durbin on the
   chunk, coefficients quantized to 15 bits and stored, order chosen by estimated code length (as SRLA does),
   integer arithmetic so decoding is bit-exact. Estimated **-0.70** bits/sample overall (-7 %; -1.49 on the
   phase currents, -0.40 on FEMTO, -0.15 on vibration, 0 on force), almost exactly SRLA's measured result. Decode
   cost: up to 32 multiply-adds per sample, still well above T.261's 5 M samples/s.
3. **Adaptive entropy coding of the residuals**: either an adaptive Laplacian/Golomb model driven by the running
   mean |residual| with arithmetic coding, or SRLA's cheaper partitioned recursive (two-parameter) Golomb-Rice.
   Estimated **-0.05** overall on top of idea 2 (-0.10 on vibration and currents, -0.01 on FEMTO); T.261's coder
   suggests up to about -0.1. Lower priority: it costs decode speed for a small gain.
4. **A cascaded NLMS stage (order 16 to 32, reset per chunk) after the block predictor**: estimated **-0.08** on
   vibration, about 0 elsewhere; and **long-term prediction (block matching)** for periodic signals: 0.21 on one
   phase current in T.261's ablation. Both are speculative and the last to try.

Ideas 1 and 2 together would take the Paderborn 64 kHz channels from about 10.3 to about 8.5 bits/sample as
stored (T.261 on the bare integers: about 7.7), at a fraction of T.261's encode and decode cost; the rest is
mostly the phase currents' corrections, whose indices rarely repeat within a 4,096-sample chunk.

## Build and provenance

Neither reference codec's source, binaries nor bitstreams are in this repository; they were built and run only
in a scratch directory outside it.

**T.261 reference software**: the owner's download (`bwc-main.zip`), "Reference Software for T.261, formerly
Biomedical Waveform Codec (BWC)", `BWC_VERSION "7.0"` in `source/Lib/CommonLib/version.h`. Copyright (c)
2022-2026 Fraunhofer-Gesellschaft zur Förderung der angewandten Forschung e.V. (version.h), and (c) 2022-2024
Fraunhofer-Gesellschaft and (c) 2024 Dolby International AB (LICENSE.txt). Licensed under a BSD licence that
states: "This software may be subject to other third party and contributor rights, including patent rights, and
no such rights are granted under this license", and "NO EXPRESS OR IMPLIED LICENSES TO ANY PARTY'S PATENT RIGHTS
ARE GRANTED BY THIS LICENSE." Nothing here copies its code; ideas taken from T.261 specifically (rather than
LPC, Rice and LMS, which long predate it) would need a patent check before going into Grizzbit.

Built without CMake by compiling the sources of `CommonLib`, `EncLib`, `DecLib`, `App/Encoder` and
`App/Decoder` directly (as their CMakeLists.txt list them, each library's directory on the include path):
`clang++ -O3 -std=c++20 -DENABLE_SIMD=0 -DNDEBUG -w` (Apple clang 21, arm64; the project requires C++20 and its
SIMD path is x86-only), linking EncoderApp from App/Encoder + EncLib + CommonLib and DecoderApp from
App/Decoder + DecLib + CommonLib. Run only as `EncoderApp -c <preset> --InputFile=<k file> --FileFormat=Raw
--InputNumChannels=<1|3> --InputBitDepth=16 --BitstreamFile=<out> [--Key=value]` and `DecoderApp
--BitstreamFile=<in> --OutputFile=<out> --FileFormat=Raw`.

**SRLA**: the owner's clone (commit 73cbb9a; `SRLA_CODEC_VERSION 18`, `SRLA_FORMAT_VERSION 10`); paper "SRLA: A
lossless audio codec focused on compression ratio and decoding speed", Multimedia Tools and Applications, 2026.
MIT licence (COPYING, Copyright (c) 2023 aikiriao): reimplementing its techniques in Grizzbit is fine; copying
its code would require keeping its copyright and permission notice. Built without CMake: every `libs/*/src/*.c`
plus `tools/srla_codec/srla_codec.c`, `clang -O3 -DNDEBUG -std=c90 -w`, every `libs/*/include` and `include` on
the include path, linked with `-lm`. Run only as `srla -e [options] <in.wav> <out.srl>` and `srla -d <in.srl>
<out.wav>`. Its WAV reader fails to open files shorter than its 32 KiB read buffer (the 16,001-sample force
channels); `srla.py` appends a 4 KiB `junk` chunk to such files, which the reader skips (the decoded samples
were compared as usual).

## Files and how to rerun

- `prepare.py`: reads the inputs, classifies each channel's grid, writes float64, k*q and integer files.
- `grizzbit_bench/`: a small Rust binary outside the main workspace: Grizzbit sizes, checks and speed
  (`grizzbit`), and the estimates (`ideas`).
- `run.py`: Grizzbit, T.261 (all presets, intra 4,096, ablations, joint) -> `results/dev.json`.
- `srla.py`: the SRLA arm -> `results/dev_srla.json`.
- `results/dev_femto_grid.json`: Grizzbit on FEMTO's k*q. `dev.json` was written before `prepare.py` counted
  negative zeros as off-grid (it then classed FEMTO as exact and ran Grizzbit on the original values only);
  this file adds the missing like-for-like arm, and `summarize.py` merges it.
- `summarize.py`: the tables above.

```
.venv-b3/bin/python -I evaluation/codec_t261/prepare.py BUILD/work
(cd evaluation/codec_t261/grizzbit_bench && cargo build --release)
.venv-b3/bin/python -I evaluation/codec_t261/run.py BUILD evaluation/codec_t261/results/dev.json
.venv-b3/bin/python -I evaluation/codec_t261/srla.py SRLA_BUILD/bin/srla BUILD/work evaluation/codec_t261/results/dev_srla.json
python3 -I evaluation/codec_t261/summarize.py
```

`BUILD` holds `bin/EncoderApp`, `bin/DecoderApp`, `cfg/` (the reference software's preset files) and `work/`.
The full T.261 run took 29 minutes on the busy host (the ACoM preset on FEMTO's 4.2 million samples, about
70 s per encode, dominates); SRLA took about a minute.
