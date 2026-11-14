# Grizzbit format specification

Grizzbit is MorletDB's chunk codec ([overview](grizzbit.md)). Grizzbit 1 is the encoded
chunk format versions 2 and 3 specified on this page. Stream collections created by
pg_morlet 0.2.0 store each raw chunk as one `bytea` value produced by Grizzbit
(`crates/grizzbit`, re-exported as `morlet_core::rawcodec`); the one-file format and
compressed transfer carry the same chunks. This page specifies that format and records
why MorletDB owns it.

## Techniques

Nothing here is novel. Grizzbit combines established lossless audio and
seismic techniques:

- fixed polynomial predictors of order 0–4 (Shorten, FLAC "fixed" subframes);
- Rice (Golomb power-of-two) coding of zigzag-mapped residuals, with up to 2^8
  partitions, each with its own parameter or a raw escape (as in FLAC);
- for samples on a known grid, integer indices plus a scale (as SEED/miniSEED and
  WAV/FLAC store counts plus a gain), and, for calibrated formats such as EDF whose
  physical range is not symmetric, an offset (as EDF stores a gain and an offset). The scale is refined per chunk, and any
  remaining differences are stored as last-place (ULP) corrections so the
  float64 values reproduce bitwise;
- a CRC-32C (Castagnoli) checksum.

It has no linear-prediction (LPC) stage. On the stage-1 benchmark data it is 2–7%
larger than FLAC.

## Layout (Grizzbit 1: format versions 2 and 3)

All integers are little-endian; `varint` is unsigned LEB128.

| Field | Size | Meaning |
|---|---|---|
| version | 1 byte | `2`, or `3` for an offset grid |
| kind | 1 byte | `1` grid, `2` float, `0` raw |
| n | varint | sample count, 1..32768 |
| body | — | per kind, below |
| crc | 4 bytes | CRC-32C of every preceding byte |

- **grid**:
  - `mode` u8: 0 means `x = k * scale`; 1 means `x = k / scale` for integer-valued `scale`.
  - `scale` f64.
  - Version 3 only: `offset` f64, finite. The value is then `offset + k * scale` or
    `offset + k / scale`.
  - `has_fix` u8.
  - `varint` byte length of the index stream, then the index stream.
  - If `has_fix`, a correction stream: corrections are differences of
    order-preserving float64 integers.
- **float**: one stream of order-preserving integers of the float64 bit patterns.
- **raw**: `n` little-endian float64 values.

Version 3 has the grid kind only. The encoder tries it only for a chunk with a
sample off the plain grid, before float-bit coding:
- **Offset:** taken from the first sample.
- **On-grid check:** every sample must lie within a thousandth of a step of
  offset + k * step.
- **Refit:** step and offset are refitted to within 1 ulp of the values needing the
  fewest corrections.
- **Size check:** the result must be smaller than plain float64.

Otherwise the chunk is coded as float bits or raw, as before. A chunk on the plain
grid (ADC counts, fixed decimals, a calibrated step through zero) keeps its version-2
encoding byte for byte.

A stream is a bit-packed (MSB-first) sequence with these fields:
- predictor order (3 bits) and partition order (4 bits);
- per partition, a Rice parameter (6 bits; 63 means a raw escape followed by a
  7-bit width);
- the residuals, padded with zero bits to a byte boundary.

## Version 5 (Grizzbit 2.1, opt-in)

Version 5 is part of this specification from 0.3.12-alpha.1, as an **opt-in** format: a
server writes it only with `morlet.chunk_format = grizzbit21`; the default, `grizzbit1`, writes versions 2 and 3
only. Every reader of that release decodes version 5 whatever the setting. It passed study B6
([protocol](../evaluation/storage_b6/PROTOCOL.md), [results](../evaluation/storage_b6/RESULTS.md)) on every
criterion. The reference text is the module documentation of `crates/grizzbit/src/g21.rs`. Version 4 was Grizzbit 2 (pco, study B5, failed). It
is never reused, and decoders refuse it as an unknown version.

Version 5 has the grid kind only. It is a version-2 or version-3 grid chunk with two additions: corrections
stored once per grid index, and a per-chunk linear predictor (LPC).

| Field | Size | Meaning |
|---|---|---|
| version | 1 byte | `5` |
| kind | 1 byte | `1` (grid; the only kind in version 5) |
| n | varint | sample count, 1..32768 |
| flags | u8 | bit 0: an offset follows the scale; bits 1–2: correction mode (below); bits 3–7: zero |
| mode | u8 | `0`: `x = k * scale`; `1`: `x = k / scale` (as version 2) |
| scale | f64 | finite, > 0 |
| offset | f64 | only with flag bit 0; finite; the value is then `offset + (k * scale or k / scale)` |
| index length | varint | byte length of the index stream |
| index stream | — | `n` grid indices, a version-5 stream (below) |
| corrections | — | per correction mode; takes every byte up to the checksum |
| crc | 4 bytes | CRC-32C of every preceding byte |

**Correction modes.** Corrections are wrapping differences of order-preserving integers, as in version 2.
Each is applied as `from_ordered(ordered(grid value) + c)`.

- `0` none: no bytes.
- `1` per sample: one stream of `n` corrections, version 2's layout.
- `2` table: an exception block, then one stream of `D` corrections.
  - `D` is the number of distinct indices in the chunk.
  - Entry `j` belongs to the `j`-th distinct index, in order of first occurrence.
  - A sample's correction is its index's entry, unless the sample is an exception.
- `3` exceptions only: an exception block with at least one exception and nothing after it. Every other
  sample's correction is 0.

**Exception block.**
- It starts with `m`, a varint in 0..=n.
- If `m > 0`, two parts follow, each a varint byte length and then a stream of `m` values:
  - the gaps: the first gap is the first position, and each further one is `position − previous position − 1`.
    All gaps are ≥ 0 and all positions are < n.
  - the `m` corrections of those samples, which replace the table entry.

**Version-5 streams.** Every stream in a version-5 chunk (indices, corrections, table, gaps) is a version-2
stream, as above, with one addition: predictor `7` means LPC. Predictors 5 and 6 are invalid.
- **Parameters,** after the partition order:
  - order − 1 (5 bits, order 1..=32);
  - precision − 1 (4 bits, precision 1..=15);
  - shift (5 bits, 0..=31);
  - `order` coefficients of `precision` bits each, in two's complement, the first multiplying `x[i−1]`.
- **Prediction,** in signed 64-bit two's-complement wrapping arithmetic, with no floating point:
  - for `i < order`, the fixed order-2 predictor of version 2, so `min(2, i)`;
  - from `i = order` on, `(Σ_{j=1..order} c_j · x[i−j]) >> shift`, an arithmetic shift.

**Encoder.** It keeps the smaller coding, per chunk:
- Every chunk is coded with Grizzbit 1 exactly as above.
- Each grid coding Grizzbit 1 computed is also coded as version 5.
- A version-5 chunk is stored only if it has strictly fewer bytes. On a tie, Grizzbit 1 is kept, so a Grizzbit 1
  chunk that stays is byte for byte what Grizzbit 1 alone writes.
- The LPC coefficients come from a Welch-windowed autocorrelation, then Levinson-Durbin, then FLAC-style
  quantisation with error feedback. Only the order with the best code-length estimate is coded exactly.
- The encoder uses IEEE basic operations only, in a fixed order: no fused multiply-add and no libm. So it writes
  the same bytes on every platform (B6, S5).

**Decoder,** with Grizzbit 1's rules:
- the checksum is checked before any byte of the body is parsed;
- lengths must match, and no trailing byte or padding bit is allowed;
- every sample must be finite and within ±1e9.

**Not in file format 1.** The one-file format's version 1 allows chunk versions 2 and 3 only. Its writer keeps
Grizzbit 1 and its reader refuses version 5 ([file-format.md](file-format.md)).

### Decoding a stream, exactly

These are the rules a decoder needs beyond the field list above. The reference reader
(`reference/python/morletfile.py`, standard library only) implements them; together with this page
it is the specification.

- **Header checks:** predictor order ≤ 4, partition order ≤ 8, and `n >> partition_order` ≥ 1.
- **Partitions:** there are `2^p` of them. Partition `j` holds the residuals of samples
  `[(j·n) >> p, ((j+1)·n) >> p)`, and the partitions follow each other in one stream.
- **Residual code:**
  - With Rice parameter `k` (0..62): a unary quotient `q` (q zero bits, then a one bit), then `k`
    remainder bits `r`; the value is `(q << k) | r`.
  - With the escape (63): a 7-bit width `w` (≤ 64), then each value in `w` bits.
- **Zigzag:** a coded value `u` stands for the signed residual `(u >> 1) XOR −(u AND 1)`.
- **Prediction:** residual `e[i]` is added to a prediction `x̂[i]` from the values before it. The
  order used at sample `i` is `min(order, i)`, so the first samples use lower orders:
  - order 0: 0;
  - order 1: `x[i−1]`;
  - order 2: `2x[i−1] − x[i−2]`;
  - order 3: `3(x[i−1] − x[i−2]) + x[i−3]`;
  - order 4: `4(x[i−1] + x[i−3]) − 6x[i−2] − x[i−4]`.

  All integer arithmetic wraps as signed 64-bit two's complement.
- **End of stream:** after the last residual only zero padding bits up to the end of the stream's
  bytes are allowed.
- **Order-preserving integers** (float kind and corrections): the float64 bit pattern `b` maps
  to `b` when the sign bit is clear and to `−(b AND 0x7FFF…F) − 1` when it is set. So +0 is 0,
  −0 is −1, and numeric order is kept. The inverse maps `o ≥ 0` to the bit pattern `o`, and
  `o < 0` to `(−(o + 1)) OR 0x8000…0`.
- **Grid values:**
  - `k` converted to float64, then `k · scale` (mode 0) or `k / scale` (mode 1), both in float64.
  - Version 3 then adds `offset`, as `offset + that`.
  - With corrections, each value is `from_ordered(ordered(grid value) + c)`, with wrapping addition.
- **Grid stream lengths:** the index stream has the byte length given before it, and the
  correction stream (if `has_fix`) takes the remaining bytes before the checksum. Without
  corrections no bytes may remain.
- **Kinds by version:** kind 0 (raw) and kind 2 (float) appear only with version 2; kind 1
  (grid) with versions 2 and 3.
- **Checksum:** CRC-32C (Castagnoli; reflected polynomial `0x82F63B78`, initial value and final
  XOR `0xFFFFFFFF`) over every byte before it, stored little-endian.

## Version 6 (float32 codings)

**Default for float32 samples since the release after 0.3.13-alpha.1** (study
[B8](../evaluation/storage_b8/RESULTS.md), passed). This layout is now fixed: a change needs a new version.

Version 6 holds samples that are each exactly a float32 value widened to float64. Grizzbit 1 codes such samples as
float64: the float kind's order-preserving integers then carry 29 low mantissa bits that are always zero, and grid
detection judges fit in float64 last places, which float32-rounded grid values never meet. Version 6 codes them in
float32 terms, with the same integer streams.

| Field | Size | Meaning |
|---|---|---|
| version | 1 byte | `6` |
| kind | 1 byte | `1` (float32 grid) or `3` (float32 sign/magnitude) |
| n | varint | sample count, 1..=32768 |
| payload | | per kind, below |
| checksum | 4 bytes | CRC-32C of every byte before it, little-endian |

**Float32 grid (kind 1):**
- `offset` (float64 LE), `scale` (float64 LE): both finite, `scale` > 0;
- a varint length `L`, then `L` bytes: the index stream, `n` integers `k`;
- `has_fix` (1 byte, 0 or 1); with 1, the remaining bytes are the correction stream, `n` integers `c`; with 0,
  no byte may remain;
- sample `i` is `from_ord32(ord32(g) + c[i])`, where `g` is `offset + k[i] · scale` computed in float64 (one
  multiply, one add, no fused multiply-add) and rounded to the nearest float32, and `c[i]` is 0 without
  corrections. The sum must lie in `ord32`'s range.

**Float32 sign/magnitude (kind 3):**
- the first sign (1 byte: 0 positive, 1 negative);
- a varint `R`, the number of sign runs, 1..=n;
- a varint length `L`, then `L` bytes: the magnitude stream, `n` integers each in 0..=0x7FFFFFFF;
- with `R` > 1, the remaining bytes are the run stream: `R` integers, each at least 1, summing to `n`; with
  `R` = 1, no byte may remain;
- sample `i` is the float32 with bit pattern `m[i] | s(i) << 31`, the signs alternating run by run from the
  first sign.

**Streams.** Every stream is the integer stream above (predictor header, partitions, Rice codes), and may also
use version 5's LPC predictor (predictor field 7), except the run stream, which uses fixed predictors only.

**`ord32`** maps a float32 bit pattern `b` to `b` when the sign bit is clear and to `−(b AND 0x7FFFFFFF) − 1`
when it is set, so +0 is 0 and −0 is −1; its inverse refuses integers outside −2³¹..2³¹−1.

**Encoder.** Only for a chunk whose every sample equals its float32 rounding. It codes the sign/magnitude kind,
and the grid kind when at most three quarters of the samples are distinct values, and keeps the smaller. The
version-6 chunk is stored only if it has strictly fewer bytes than the chunk the configured format writes without
it (Grizzbit 1, or Grizzbit 2.1); on a tie that chunk is kept, byte for byte. The grid fit uses IEEE basic
operations in a fixed order, and its `offset` and `scale` are stored, so decoding does not depend on it.

**Decoder,** with Grizzbit's rules: the checksum first; every length, count and range above; samples finite and
within ±1e9.

**Not in file format 1.** Its writer keeps Grizzbit 1 and its reader refuses version 6.

## Chunk summary (version 1, 66 bytes)

Stored next to a chunk to answer interval statistics without decoding it. Readers may ignore it;
it is derived from the samples, and writers of the file format may leave it NULL.

| Bytes | Field |
|---|---|
| 0 | `1` (summary version) |
| 1–8 | `u64` sample count |
| 9 | `1` if any sample is NaN, else `0` |
| 10–65 | `f64` min, max, sum, sum compensation, scale, scaled sum of squares, its compensation |

The sums are compensated (Neumaier) sums in sample order. Squares are summed as `(x / scale)²`,
where `scale` is the largest `|x|` so far and the sum is rescaled whenever `scale` grows
(`morlet_core::intervalstats`).

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
- Version 3 (SQL 0.3.1) adds the offset grid. Decoders accept versions 2 and 3.
  Chunks that do not need an offset are still written as version 2, byte for byte
  as before.
- **Downgrade:** a version-3 chunk is reported as "unknown format version" by
  pg_morlet 0.3.0 and earlier, so downgrading after appending such chunks needs
  them re-appended.
- **Version 4** was the planned Grizzbit 2 (#326, study B5). It failed B5, was never released, and stays
  reserved: decoders refuse it, and no later format reuses the number.
- **Version 5** (Grizzbit 2.1, opt-in, above) keeps decoding versions 2 and 3. A decoder that reads version 5
  reports it as its highest version on the wire (below), so older clients receive samples instead of version-5
  chunks.
- **Downgrade from version 5:** pg_morlet 0.3.11-alpha.1 and earlier, and decoders of those releases, report a
  version-5 chunk as an unknown format version. A collection that stored version-5 chunks needs them re-appended
  with `grizzbit1` before such a downgrade.
- **Version 6** (float32 codings, above) keeps decoding every earlier version. Readers that do not know it
  report it as an unknown format version, so a database with version-6 chunks needs a build that reads version 6;
  pg_morlet 0.3.13-alpha.1 and earlier do not. Before such a downgrade, re-append those chunks with
  `morlet.float32_chunks = off`;
  on the wire, older clients receive samples (below). `morlet.float32_chunks = off` writes no version-6 chunk.
- `float8[]` collections from pg_morlet 0.1.0 are a separate layout, detected
  per collection, and remain supported (see durable-ingestion.md, Upgrading).
- **Chunks on the wire (SQL 0.3.5):** `morlet_read_interval_encoded` sends stored
  chunks unchanged to clients that decode them ([compressed-transfer.md](compressed-transfer.md)),
  in a frame (`MTX1`: interval, then start sample, length and bytes per chunk,
  CRC-32C over the frame; `morlet_core::transfer`). A client passes the highest
  version it decodes, and the server sends decoded samples (`MTR1` frame) instead
  of any chunk of a newer version, so a client never has to guess a format. A
  client decoder must verify each chunk's own checksum, as the server does.

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
