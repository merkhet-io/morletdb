# Verification of the chunk codec

What is machine-checked about the raw-chunk codec (Grizzbit, `crates/grizzbit/src/lib.rs`; re-exported as `morlet_core::rawcodec`; format versions
2 and 3), how, with which bounds, and what is not. Wording elsewhere (README, website, paper) must not claim
more than this page.

## Summary

- **Proven for all inputs (Kani, bounded model checking):**
  - the integer and bit-pattern maps the codec relies on;
  - varint coding;
  - the predictor's inverse;
  - the last-place correction that restores a sample from its grid value.
  These hold for every 64-bit input, not a sample of them.
- **Tested, not proven:** the round trip `decode(encode(x)) == x`, bit for bit.
  - Unit tests and frozen golden chunks.
  - Property tests on up to 4,096 samples.
  - Structured fuzzing on chunks of up to 32,768 samples.
- **Fuzzed:** the decoder on arbitrary bytes, and on arbitrary bodies with a recomputed checksum so that
  every mutation reaches the parser. No panic, hang or memory-limit violation was found in about 870 million
  inputs.
- **Found:** one bug, an integer overflow when encoding with a subnormal quantum. It panicked in debug builds
  and had no effect in release builds. Fixed in #94 before this work was merged.

A sentence that matches this page: *"Raw chunks decode bit for bit to the appended samples. The arithmetic
that guarantees this is machine-checked for all inputs, and the encoder and decoder are fuzz- and
property-tested."* Not: "proven lossless".

## Properties

| Function(s) | Property | How checked | Status |
|---|---|---|---|
| `ordered_bits` / `float_from_ordered` | Inverse of each other for every f64 bit pattern (all 2^64, incl. NaNs, ±0, subnormals) and every i64 | Kani `ordered_bits_is_a_bijection` | **proven** |
| `ordered_bits` | `a < b ⇒ ordered_bits(a) < ordered_bits(b)` for all non-NaN f64; `-0.0 ↦ -1`, `+0.0 ↦ 0` | Kani `ordered_bits_keeps_order` | **proven** |
| `zigzag` / `unzigzag` | Inverse for every i64 and every u64 | Kani `zigzag_is_a_bijection` | **proven** |
| `put_varint` / `get_varint` | Round trip for every u64 in at most 10 bytes | Kani `varint_roundtrips` | **proven** |
| `get_varint` | On any input of up to 11 bytes from any start: an error or a value, no panic, no read past the end, at most 10 bytes consumed | Kani `varint_reader_is_total` | **proven** (≤ 11 bytes) |
| `predict` and its inverse in `decode_ints` | Decoding adds back exactly the prediction the encoder subtracted, for every order 0–4, position and i64 history (wrapping arithmetic on both sides, so no overflow panic) | Kani `prediction_residuals_invert` | **proven** (histories of 5 values, which every order needs) |
| Grid corrections | `float_from_ordered(ordered_bits(v) + (ordered_bits(x) − ordered_bits(v))) == x` bit for bit for **every** sample `x` and grid value `v`. Encoder and decoder compute `v` with the same code from the same stored scale and offset, so the float arithmetic that produced `v` does not matter | Kani `corrections_restore_any_sample` | **proven** |
| `encode` → `decode` | Bit-exact round trip for every valid chunk; rejection of empty, too long, non-finite or out-of-range samples and invalid quanta | unit tests; proptest (3 properties × 500 cases per run, up to 4,096 samples); fuzz target `roundtrip` (up to 32,768 samples) | **tested and fuzzed** |
| `decode` on arbitrary bytes | No panic; any success returns 1–32,768 finite samples within ±1e9 | fuzz `decode_raw`, `decode_sealed`; proptest | **fuzzed** |
| Allocation on decode | At most `MAX_SAMPLES` (32,768) values per stream, whatever the declared count. The count is checked before allocating | code review; fuzzing under a 64 MB malloc limit | **bounded by construction**, not by input length (see gaps) |
| `crc32c` | Matches the CRC-32C check value `123456789 → 0xE3069283` | unit test | **tested** |
| Every format version, kind and grid mode | 16 frozen encoded chunks (`testdata/rawcodec/`) decode to their frozen samples, and the current encoder still produces 16 of 16 byte for byte | `crates/grizzbit/tests/codec_golden.rs`, plus `tests/encoder_equivalence.rs` (221 frozen records in `testdata/grizzbit/`, written by the encoder before it moved into its own crate); CI rejects changes to existing files | **tested** |

Golden coverage:
- **Version 2:** raw (64 samples and a single sample), float bits, grid in division mode (counts, decimals,
  signed zeros with corrections, 32,768 samples, ±1e9 extremes) and grid in multiplication mode (dyadic,
  calibrated, with corrections).
- **Version 3 (offset grid):** multiplication mode (EDF-style, with and without added ulp noise, and an exact
  dyadic offset) and division mode (shifted decimals, quarter offset).

## Version 5 (Grizzbit 2.1, study B6 build)

Version 5 is the draft chunk format of study B6 ([chunk-format.md](chunk-format.md), "Draft: version 5"). It is
not in any release. What is checked about it:

| Function(s) | Property | How checked | Status |
|---|---|---|---|
| `parse_flags` | Accepted exactly when bits 3–7 are zero; gives the offset bit and a correction mode 0..=3 | Kani `version5_flags_are_checked` | **proven** (all 256 bytes) |
| `kind_of`, `verified` | Version 5 has the grid kind only; every other kind byte under version 5 is refused; version 4 (the unshipped Grizzbit 2) is refused as an unknown version before its checksum is read | Kani `version5_kind_dispatch` | **proven** |
| `verified` | A version-5 chunk is accepted only if its CRC-32C matches, before any byte of the body is parsed | Kani `version5_checksum_is_checked_first` | **proven** (7-byte chunks) |
| `positions_from_gaps` | Exception positions from any gaps: no panic or overflow; when accepted, strictly increasing and below n | Kani `exception_positions_are_in_range` | **proven** (3 gaps of any i64, any n; a symbolic count exceeded 20 minutes) |
| `keep_smaller` | The version-5 candidate replaces Grizzbit 1's bytes only if strictly shorter (a tie keeps Grizzbit 1) | Kani `keep_smaller_keeps_grizzbit1_unless_strictly_smaller` | **proven** (up to 3 bytes each) |
| LPC coefficients | Every `precision`-bit two's-complement coefficient (precision 1–15) reads back as itself, and any stored bits give a value in range | Kani `coefficient_sign_extension_roundtrips` | **proven** |
| LPC predictor and its inverse | Decoding adds back exactly the prediction the encoder subtracted, for any history, coefficients of the allowed precision and shift (wrapping on both sides) | Kani `lpc_residuals_invert` | **proven** (order 2, histories of 3 values) |
| LPC arithmetic against a wide-integer reference | With values below the encoder's 2^40 limit and 15-bit coefficients, the wrapping i64 prediction equals the exact i128 sum shifted right, so no stream the encoder writes ever wraps | Kani `lpc_prediction_matches_wide_reference` | **proven** (order 2, any shift) |
| Unrolled LPC kernel | On any residuals and coefficients: no panic or overflow check fires, and the decoder restores exactly the values whose residuals the format's definition gives, through the warm-up and the first kernel position | Kani `lpc_kernel_restores_reference_residuals` | **proven** (order 2, 9 values of any i64, coefficients in [−4, 4), any shift; with 15-bit coefficients CBMC exceeded 20 minutes) |
| `encode_with(.., Grizzbit21)` → `decode` | Bit-exact round trip; the output is Grizzbit 1's bytes unless it is a strictly smaller version-5 chunk; every version-5 chunk, kept or not, round trips | unit tests; 37 edge cases (`tests/grizzbit21_edges.rs`); proptest (2 properties × 500 cases per run); fuzz target `roundtrip` | **tested and fuzzed** |
| `decode` on version-5 bodies | No panic; any success returns 1–32,768 finite samples within ±1e9 | fuzz `decode_v5` (arbitrary bodies behind a version-5 grid header, sealed); proptest (resealed mutations) | **fuzzed** |
| Unrolled kernels for all orders 1–32 | Same integers as the format's definition, including values whose sums overflow | unit test `unrolled_lpc_matches_the_definition` (400 cases) | **tested** |
| Version-5 golden chunks | 8 frozen chunks (`testdata/rawcodec/v5-*`): every correction mode, LPC and fixed predictors, an offset grid, 32,768 samples; they decode bit for bit and the encoder still writes each byte for byte, on every CI platform | `crates/grizzbit/tests/codec_golden.rs` | **tested** |

Not proven: the LPC fit (floating point, encoder only; it only chooses coefficients, and any coefficients
decode exactly) and the table's first-occurrence numbering (tested against a reference implementation on both
of its paths).

**B6 build fuzzing (gate 4):** `decode_v5` and `roundtrip` (which runs the keep-the-smaller encoder and the version-5 coder alone), at least 2 CPU-hours each with the nightly workflow's limits and `-O -a`. Figures are in the build record, [evaluation/storage_b6/PROTOCOL.md](../evaluation/storage_b6/PROTOCOL.md), section 9.

## Tools and runs

| Tool | Version | Use |
|---|---|---|
| Kani | 0.68.0 (CBMC 6.11.0, nightly-2026-08-21 as installed by `cargo kani setup`) | proofs, `#[cfg(kani)] mod proofs` in `crates/grizzbit/src/lib.rs` and `src/g21.rs` (run from `crates/grizzbit`) |
| cargo-fuzz | 0.13.2, toolchain nightly-2026-10-11, libfuzzer-sys 0.4.13, arbitrary 1.5.0 | `crates/grizzbit/fuzz/` (codec: `decode_raw`, `decode_sealed`, `decode_v5`, `roundtrip`); `crates/morlet-core/fuzz/` (transfer frames: `decode_frame`) |
| proptest | 1.x (locked in `Cargo.lock`) | `crates/morlet-core/tests/codec_props.rs` |

**Proofs:** CBMC verification time per harness on an Apple M5 Max:

| Harness | Time |
|---|---|
| `ordered_bits_is_a_bijection` | 0.07 s |
| `ordered_bits_keeps_order` | 0.15 s |
| `zigzag_is_a_bijection` | 0.02 s |
| `varint_roundtrips` | 1.9 s |
| `varint_reader_is_total` | 0.8 s |
| `prediction_residuals_invert` | 8.7 s |
| `corrections_restore_any_sample` | 0.17 s |
| `coefficient_sign_extension_roundtrips` (version 5) | 0.19 s |
| `lpc_residuals_invert` (version 5) | 24.9 s |
| `version5_flags_are_checked` | 0.06 s |
| `exception_positions_are_in_range` | 1.8 s |
| `version5_kind_dispatch` | 0.32 s |
| `version5_checksum_is_checked_first` | 22.1 s |
| `keep_smaller_keeps_grizzbit1_unless_strictly_smaller` | 2.9 s |
| `lpc_prediction_matches_wide_reference` | 13.3 s |
| `lpc_kernel_restores_reference_residuals` | 255 s |

That is about 6 minutes in total, plus compilation (the version-5 times were taken while fuzzing ran at low
priority). All sixteen run on every pull request that touches `grizzbit` or `morlet-core`
(`.github/workflows/verification.yml`).

**Fuzzing:** the first runs (2026-11-01/02), each roughly an hour of CPU, with debug assertions and overflow
checks on. None found a crash.

| Target | Inputs | Notes |
|---|---|---|
| `decode_sealed` | 475 M | Arbitrary bodies sealed with a correct checksum. Seeded from the golden chunks. Coverage 376 edges. |
| `decode_raw` | 388 M | Arbitrary bytes including the checksum. Almost all stop at the checksum, as intended. Coverage 220 edges. |
| `roundtrip` | 5.3 M | Structured chunks, up to 32,768 samples, many quanta. Coverage 693 edges. |

Saved "timeout" and "slow unit" inputs replayed in 0–5 ms. They coincided with the machine sleeping during
the run and are not findings.

The nightly workflow fuzzes each target for 40 minutes and keeps the corpus between runs. Crashing inputs
are uploaded as artifacts, and minimized ones become regression tests.

## How to run

```sh
cargo test -p grizzbit                                      # unit, golden, equivalence and property tests
cd crates/grizzbit
cargo kani --harness corrections_restore_any_sample         # one proof (all seven: see verification.yml)
cargo +nightly-2026-10-11 fuzz run -O -a decode_sealed fuzz/corpus/decode_sealed -- -max_total_time=600
MORLET_WRITE_GOLDEN=1 cargo test -p grizzbit --test codec_golden -- --ignored      # add new golden files only
```

The fuzz targets recompute CRC-32C themselves (`fuzz_targets/common.rs`) so that mutated bodies reach the
parser. The codec's own checksum verification is what is under test.

## Gaps (not proven, or proven only within bounds)

- **The full round trip is not proven.** The encoder's grid search uses float multiplication, division and
  rounding.
  - What is proven is the part that makes the round trip exact once a grid is chosen: corrections restore
    any sample, and integer coding is lossless at the level of the predictor and zigzag.
  - The integer stream's Rice and bit coding as a whole is tested and fuzzed, not proven.
- **Kani harnesses attempted and dropped.** Each was attempted and exceeded a 20-minute limit without a
  verdict, so none was dropped for failing:
  - the bit writer and reader with symbolic widths;
  - unary codes;
  - bit-reader totality;
  - totality of integer decoding on 4-byte inputs;
  - integer coding round trip for two values;
  - grid index/value consistency for symbolic floats;
  - parser totality on 8-byte bodies.

  CBMC does not finish on the writer's growing buffer and data-dependent loops at these sizes.
- **Allocation is bounded by a constant, not by input length.** A forged chunk of a few bytes can declare
  32,768 samples and, with escape-coded zero-width partitions, legitimately decode to 32,768 zeros. The
  encoder produces such chunks for constant data. Decoding then allocates about 0.5 MB and does work linear
  in 32,768, the same as a real full chunk.
- **Non-canonical varints.** A 10-byte varint whose last byte has bits above the 64th is accepted with those
  bits dropped, so two byte strings can decode to the same count. The checksum still covers every byte.
- **No independent decoder.** `tools/reference-go` contains the envelope extractor only, so differential
  fuzzing against a second implementation is not possible.
- **Envelopes and search are out of scope here.** The bound on what the sparse lag grid can miss is
  `research/grid_bound/NOTE.md`.
