# Float32 chunk coding: development spike (#326)

**DEVELOPMENT ONLY. Not a study, not a statement; local and unpublished (IU4 data, see B7).**

Run 2026-11-14 on a development set disjoint from both B7 sets: IU4 runs[5::40] sorted by (machine, tool, run),
161 runs (force 53.9 M samples, ctrl 2.16 M). B7 read runs[::20] and runs[10::20]; runs[15::20] stay unread for a
frozen benchmark set. Dump: `dump_dev.py`; sizes and decode checks: `crates/grizzbit/src/spike_f32.rs`
(`cargo test --release -p grizzbit spike_f32 -- --ignored --nocapture`, `SPIKE_DIR` set). Every prototype chunk
was decoded and compared bit for bit. Machine: M-series Mac, not quiet (load 3–5): speeds indicative only.

## Why float32 data is large today

Grizzbit widens float32 to float64. The float kind then codes order-preserving float64 bits whose low 29 mantissa
bits are always zero, so every residual carries 29 extra bits, and the grid detector judges grid fit in float64
last places, so float32-rounded grid values never fit.

## Codings tried (bytes per sample, encoded chunks, 32,768-sample chunks)

| Coding | force | ctrl |
|---|---:|---:|
| Grizzbit 1 today | 6.91 | 6.54 |
| Grizzbit 2.1 today | 6.90 | 6.27 |
| narrow float: drop the mantissa bits zero in the whole chunk | 3.79 | 3.51 |
| float32 grid: `f32(offset + k·scale)` plus corrections in float32 last places | no grid found | 2.47 (530 of 966 chunks) |
| sign / magnitude: 31-bit magnitudes predicted, sign as run lengths | 3.03 | 3.18 |
| best of the three per chunk, LPC allowed | **3.02** | **2.24** |
| pcodec 1.0.4 on the same float32 chunks (reference) | 2.83 | 1.60 (B7 set, estimate) |

- **Zero crossings** cost the narrow coding most on force: 5.5 % of samples cross zero, and each is a jump of about
  31 bits in order-preserving integers (2.80 B/sample without them, 3.45 with them, Rice estimate). The sign /
  magnitude split removes that jump.
- **pcodec's lead on force** is its entropy coder: it reaches 2.83 on the same order-preserving integers.
- **ctrl** values are decimals (6 digits, or steps of 5e-6) rounded to float32, which the float32 grid captures.
- Decode of the sign / magnitude coding: about 110 M samples/s single-threaded; encode 38–85 M/s.

## Against B7 (different sets, so indicative)

B7 table bytes per sample: MorletDB 5.20 force / 4.91 ctrl, ModelarDB lossless 3.65 / 3.27. The new codings'
chunks are about 3.0 / 2.2 B/sample before TOAST, which would put MorletDB below ModelarDB on both groups.
