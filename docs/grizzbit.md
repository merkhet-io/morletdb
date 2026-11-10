# Grizzbit

**Grizzbit: lossless compression for sampled signals, bit for bit.**

Grizzbit is the name of MorletDB's chunk codec: the format in which every stored chunk of samples is
written, in the PostgreSQL extension, in the one-file format and on the wire. It lives in
[`crates/grizzbit`](../crates/grizzbit) (`morlet_core::rawcodec` re-exports it) and is specified in the
[Grizzbit format specification](chunk-format.md).

Grizzbit is a named, specified format, not a new algorithm. Every technique in it is established (see
[Lineage and credits](#lineage-and-credits)); what it adds is a fixed combination of them with a written
specification, a second independent reader and a promise that stored chunks stay readable.

## What it does

Grizzbit takes up to 32,768 float64 samples of one channel and returns a byte string that decodes to the
same samples, every bit: the same values, the same signs of zero, the same last place. It has no precision
or tolerance parameter. Samples it cannot store exactly are refused when they are written, never altered:
values must be finite and within ±1e9.

## How it works

**In plain words.** Most sampled signals come from a digitizer: every value is a whole number of steps of
some size, perhaps shifted by an offset, even after calibration turns counts into volts or g. Grizzbit
finds that step in each chunk, stores the step numbers instead of the floats, predicts each step number
from the ones before it and stores only the small prediction errors in as few bits as they need. Where a
calibrated value does not land exactly on the grid in its last bit, it stores that last-place difference
too. Data without a grid is stored as the float bit patterns, coded the same way, or plainly.

**Technically.** Each chunk is one of three kinds, chosen by coded size:

- **grid:** integer indices `k` on a value grid, `x = k · scale` or `x = k / scale` (format version 2), or
  `offset + k · scale` / `offset + k / scale` (version 3). The grid is declared or detected from the chunk,
  and the scale (and offset) refitted per chunk. Indices are coded with a fixed polynomial predictor of
  order 0–4 and partitioned Rice coding of the zigzag-mapped residuals, as FLAC's fixed subframes do.
  Samples that differ from their grid value in the last place get a correction stream (differences of
  order-preserving integers), so the float64 values reproduce bitwise;
- **float:** the float64 bit patterns mapped to order-preserving integers, with the same predictor and
  Rice coder;
- **raw:** little-endian float64, when nothing else is smaller.

Every chunk ends with a CRC-32C (Castagnoli) of all preceding bytes, and a decoder verifies it before
returning any sample. The [specification](chunk-format.md) gives the layout and every decoding rule.

## Why it is lossless

Four independent layers, each checkable in the repository:

1. **By design.** Every kind stores the exact float64 bit pattern: grid indices plus last-place
   corrections, order-preserving integers of the bits, or the bits themselves. There is no quantization
   step and no parameter that could introduce one.
2. **Verified at write time.** With `morlet.verify_appends` on (the default), every append path decodes
   each chunk again after encoding and compares it bit for bit with its input before storing it (#366). A
   chunk that does not decode to its input is refused.
3. **Checked on every read and on demand.** Each chunk's CRC-32C is verified by every read, by replay
   comparison and by the table's CHECK constraint on insert and update. `morlet_verify` scrubs a whole
   collection: it decodes every chunk, checks its checksum, sample count, chunk summary and coverage, and
   reports problems without changing anything ([operations](operations.md#integrity-scrub-sql-035)).
4. **Tested, fuzzed and partly proven.** Round-trip, mutation and truncation tests; property tests;
   nightly structured fuzzing of decoding and round trips; 16 frozen golden chunks that a change may not
   alter; and Kani proofs, for every 64-bit input, of the integer and bit-pattern maps, varint coding, the
   predictor's inverse and the last-place correction. The round trip itself is tested and fuzzed, not
   proven ([verification](verification.md) states exactly what is checked). Benchmarks read their data
   back too: every B3 load compares every channel with the source by the SHA-256 of its float64 samples
   (868 channels, 101.6 million samples), and B4 decoded every arm on every input and compared it bit for
   bit.

## Where it is strong, and where not

Measured in B4 against the 2026 float codecs (Pcodec 1.0.4, ClickHouse 26.10 with ALP and PCO, Parquet
with ALP), all at default settings, every arm decoded and compared bit for bit
([results](../evaluation/storage_b4/RESULTS.md)):

- **Waveforms: smallest on 4 of 6 inputs** (5 of 6 against ClickHouse). Smaller than every other arm on
  CX counts (12% below the next), Paderborn counts (5%), Sleep-EDF (20%) and ECG (6%).
- **Data almost, but not exactly, on a grid: not the smallest.** Pcodec is 8% smaller on IMS decimals and
  5% smaller on Paderborn physical values; ClickHouse's PCO codec is 3% smaller on Paderborn physical.
- **Calibrated sensor values: strong.** Every ALP arm (Parquet and ClickHouse, with and without zstd) is
  2.6–7.9× larger on Paderborn physical and Sleep-EDF.
- **Elf decimal series: smallest on 12 of 22.**
- **Floats without a clean grid: weak.** On high-precision series such as weather (Basel temperature and
  wind), bird migration, food prices and an SSD benchmark, Grizzbit falls back to float-bit coding and is
  2.7–4.1× Pcodec's size and 2.2–4.0× ClickHouse's best arm; on Bitcoin prices 1.6× ClickHouse ALP with
  zstd. Slow process data with full-precision decimals behaves like this.
- **Against FLAC**, on integer counts: Grizzbit has no linear-prediction (LPC) stage and was 2–7% larger
  on the stage-1 benchmark data.

**A lossless contrast.** Some time-series databases offer lossy float compression. TDengine's float
compressor tsz is documented as lossy (it truncates doubles below 10⁻¹⁶); its default and ZSTD layouts are
lossless, and in B3.6 they returned every sample exactly. Grizzbit has no lossy mode and no precision
parameter to choose.

## Lineage and credits

Nothing here is novel. Grizzbit combines:

- fixed polynomial predictors of order 0–4, from Shorten and FLAC's "fixed" subframes;
- Rice (Golomb power-of-two) coding of zigzag-mapped residuals in up to 2^8 partitions with a raw escape,
  as in FLAC;
- integer counts plus a gain, as SEED/miniSEED and WAV/FLAC store them, and a gain plus an offset, as EDF
  stores its calibration;
- order-preserving integer maps of IEEE 754 bit patterns, a standard technique;
- the CRC-32C (Castagnoli) checksum.

Its own parts are the per-chunk grid detection and refit, the last-place corrections that make float64
reproduce bitwise, and the specification and compatibility promise around them. Why MorletDB owns the
format instead of depending on a codec library is set out in the
[specification](chunk-format.md#why-own-the-format-instead-of-depending-on-a-codec-library).

## Format versions

| Grizzbit | Chunk format versions | Since | What |
|---|---|---|---|
| Grizzbit 1 | 2 and 3 | 2 in pg_morlet 0.2.0; 3 in SQL 0.3.1 | grid, float and raw kinds (2); offset grid (3) |

The version byte of a chunk is never reinterpreted, and every later Grizzbit decodes every earlier chunk.
Clients that receive chunks on the wire state the highest version they decode, and the server sends
decoded samples for anything newer ([compressed transfer](compressed-transfer.md)).

## Roadmap

**Grizzbit 2 (planned, #326):** an additional chunk kind for floats that lie off any grid, to close the
gap above. The change is additive: the encoder keeps the smaller coding per chunk, chunks on a grid stay
byte-identical, and every Grizzbit 1 chunk stays readable. Its pass criteria are fixed in a protocol
before the benchmark inputs are read; no numbers are claimed until they are measured.

## Specification and readers

- [Grizzbit format specification](chunk-format.md): layout, decoding rules, chunk summary, compatibility.
- [Verification](verification.md): what is proven, tested and fuzzed.
- [`reference/python/morletfile.py`](../reference/python/morletfile.py): a second reader on Python's
  standard library; with the specification it defines the format.
- [One-file format](file-format.md) and [compressed transfer](compressed-transfer.md): Grizzbit chunks
  in a SQLite file and on the wire.
