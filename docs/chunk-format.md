# Encoded chunk format

Stream collections created by pg_morlet 0.2.0 store each raw chunk as one `bytea`
value produced by `morlet_core::rawcodec`. This page specifies that format and
records why MorletDB owns it.

## Techniques

Nothing here is novel. The codec combines established lossless audio and
seismic techniques:

- fixed polynomial predictors of order 0–4 (Shorten, FLAC "fixed" subframes);
- Rice (Golomb power-of-two) coding of zigzag-mapped residuals, with up to 2^8
  partitions, each with its own parameter or a raw escape (as in FLAC);
- for samples on a known grid, integer indices plus a scale (as SEED/miniSEED and
  WAV/FLAC store counts plus a gain). The scale is refined per chunk, and any
  remaining differences are stored as last-place (ULP) corrections so the
  float64 values reproduce bitwise;
- a CRC-32C (Castagnoli) checksum.

It has no linear-prediction (LPC) stage. On the stage-1 benchmark data it is 2–7%
larger than FLAC.

## Layout (format version 2)

All integers are little-endian; `varint` is unsigned LEB128.

| Field | Size | Meaning |
|---|---|---|
| version | 1 byte | `2` |
| kind | 1 byte | `1` grid, `2` float, `0` raw |
| n | varint | sample count, 1..32768 |
| body | — | per kind, below |
| crc | 4 bytes | CRC-32C of every preceding byte |

- **grid**:
  - `mode` u8: 0 means `x = k * scale`; 1 means `x = k / scale` for integer-valued `scale`.
  - `scale` f64.
  - `has_fix` u8.
  - `varint` byte length of the index stream, then the index stream.
  - If `has_fix`, a correction stream: corrections are differences of
    order-preserving float64 integers.
- **float**: one stream of order-preserving integers of the float64 bit patterns.
- **raw**: `n` little-endian float64 values.

A stream is a bit-packed (MSB-first) sequence with these fields:
- predictor order (3 bits) and partition order (4 bits);
- per partition, a Rice parameter (6 bits; 63 means a raw escape followed by a
  7-bit width);
- the residuals, padded with zero bits to a byte boundary.

The decoder rejects:
- an unknown version, or version 1;
- a checksum mismatch;
- inconsistent lengths, trailing data or bad headers;
- samples that are not finite or are outside ±1e9.

The checksum is verified before any sample is returned: by reads, replay
comparison, `morlet_decode_chunk`, and the table CHECK on insert or update. It
detects accidental corruption, including changes that would still decode to
valid samples. It is not a cryptographic authenticator; anyone who can write
the table can write a valid chunk.

## Compatibility

- The version byte is never reinterpreted. Version 1 had no checksum and existed
  only on the unreleased development branch of PR #8; it is rejected with
  "unsupported encoded chunk format version 1; re-append the samples".
- A future version 3 must keep decoding version 2. The extension would then
  decode both and encode the newest.
- `float8[]` collections from pg_morlet 0.1.0 are a separate layout, detected
  per collection, and remain supported (see durable-ingestion.md, Upgrading).

## Why own the format instead of depending on a codec library

Reasons to own it:
- **Bitwise float64 identity.** FLAC, Steim2 and zstd-on-integers store integers.
  MorletDB receives float64 samples and promises to return them bit for bit. The
  grid detection, per-chunk scale fit and ULP corrections are needed whichever
  entropy coder sits underneath, so the float64 contract is MorletDB's to own anyway.
- **Range.** FLAC is limited to 32-bit samples; libFLAC as shipped encodes at most
  24 bits per sample. Grid indices here can reach 2^53.
- **Small, auditable and dependency-free.** About 600 lines of safe Rust, with no C
  build dependency in the extension. The format is specified above and
  covered by round-trip, mutation and truncation tests.
- **Stable decoding.** A database must read its chunks for as long as it exists.
  Pinning a third-party bitstream ties that obligation to another project's
  versioning.

Costs accepted:
- We maintain and fuzz a decoder ourselves.
- We give up FLAC's 2–7% LPC gain, SIMD-optimised implementations and external
  tools that read the payload directly.

Revisit if a maintained codec offers bitwise float64 round trips, or if the
remaining gap to FLAC matters more than these costs.
