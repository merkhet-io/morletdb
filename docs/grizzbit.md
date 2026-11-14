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

## Codecs at a glance

Every chunk starts with its format version byte; a reader dispatches on it. Details are in the
[specification](chunk-format.md).

| Codec | Format version | Status | Lossless | Good at | Measured in |
|---|---|---|---|---|---|
| Grizzbit 1 | 2: grid, float and raw kinds | default | yes | samples on a value grid (`k · scale` or `k / scale`): ADC counts, fixed decimals, calibrated waveforms | B4 |
| Grizzbit 1 | 3: offset grid | default | yes | the same, for grids with an offset (`offset + k · scale`), such as EDF calibration | B4 |
| Grizzbit 2 (pco) | 4 | reserved, rejected | yes | meant for floats off any grid; never shipped | [B5](../evaluation/storage_b5/RESULTS.md) (failed) |
| Grizzbit 2.1 | 5: grid only | opt-in | yes | grid waveforms with structure a fixed predictor misses: vibration, motor currents, EEG | [B6](../evaluation/storage_b6/RESULTS.md) (passed) |
| float32 codings | 6: float32 grid and sign/magnitude | default for float32 samples | yes | float32 machine data without a float64 grid: forces, controller torques and positions | [B8](../evaluation/storage_b8/RESULTS.md) (passed) |

- **Version 2:** the encoder detects a value grid per chunk, codes the grid indices with a fixed predictor of
  order 0–4 and partitioned Rice coding, and stores last-place (ULP) corrections where a value is off its grid
  value by a few last places. Chunks without a grid are coded as float bit patterns or stored raw; the smallest
  of the three kinds is kept.
- **Version 3:** written only when a chunk's grid needs an offset; otherwise the chunk stays version 2, byte for
  byte.
- **Version 4:** Grizzbit 2 coded floats off a grid with pco. Studied in B5, failed (S2: Basel-temp 1.56×,
  Basel-wind 1.38× ClickHouse's best; S6), never shipped; version 4 reserved and rejected
  ([results](../evaluation/storage_b5/RESULTS.md)).
- **Version 5:** Grizzbit 2.1 stores the corrections once per grid index instead of per sample and adds a
  per-chunk linear predictor (LPC). The encoder keeps it only where it is strictly smaller than versions 2 and 3.

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

Grizzbit 1, measured in B4 against the 2026 float codecs (Pcodec 1.0.4, ClickHouse 26.10 with ALP and PCO, Parquet
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
- **Against FLAC**, on integer counts: Grizzbit 1 has no linear-prediction (LPC) stage and was 2–7% larger
  on the stage-1 benchmark data. Grizzbit 2.1 (below) adds one.

## Grizzbit 2.1 (opt-in)

Grizzbit 2.1 is chunk format version 5. It changes grid chunks only, with two additions to Grizzbit 1's grid
coding: last-place corrections stored once per grid index instead of per sample, and a per-chunk linear predictor
(LPC, order up to 32, exact integer arithmetic). The encoder codes every chunk with Grizzbit 1 as before, also
codes each grid chunk as version 5, and keeps the version-5 chunk only if it is strictly smaller. Chunks that stay
Grizzbit 1 are byte for byte what Grizzbit 1 writes.

It is **opt-in** from 0.3.12-alpha.1, with the setting `morlet.chunk_format`
([how to use it](#using-grizzbit-21)). The default stays `grizzbit1`.

Measured in study B6, against Grizzbit 1 on the same inputs, with criteria fixed before any benchmark input was
read ([protocol](../evaluation/storage_b6/PROTOCOL.md), [results](../evaluation/storage_b6/RESULTS.md)). B6 passed
on all six criteria (exactness, storage, no larger chunk, speed, identical bytes on every platform, B3 standing):

- **Waveforms: 12.6% smaller in total**, smaller on all six: Sleep-EDF 31%, Paderborn physical 14%, CX counts and
  IMS decimals about 4–5%, Paderborn counts 2%, ECG 0.4%.
- **Machine channels from B3:** phase currents 18–20% smaller, vibration 16%, force 14%; speed and torque
  (stored as floats) unchanged. In the B3 database the high-rate group took 17.8% less space.
- **No input and no chunk got larger** (30 benchmark inputs).
- **Floats without a grid: unchanged.** Basel temperature and wind, bird migration, food prices, Bitcoin prices,
  POI longitude and the SSD benchmark are stored byte for byte as Grizzbit 1, so the gap to Pcodec and ClickHouse
  above remains; closing it is still #326.
- **IMS decimals:** still behind Pcodec (8.39 against 8.12 bits per sample).
- **Speed, single thread on the benchmark machine:** encoding is slower than Grizzbit 1 where chunks change (about
  0.5–0.8×); decoding is between 0.63× and slightly faster on the B6 waveforms and B3 channels. The margins over B6's
  floors are **tight**: the lowest decode is 53.1 M samples/s (B3 phase currents; floor 50) and the lowest encode
  10.8 M samples/s (Sleep-EDF; floor 10). On Air-pressure, an Elf series no speed criterion covers, decoding runs
  at 0.43× Grizzbit 1 (32.8 M samples/s).
- **Edge hardware:** on a Raspberry Pi 3B+ Grizzbit 2.1 writes the same bytes (B6) and is about 12× slower than
  the benchmark machine. The speed figures are development figures ([evaluation/codec_g21](../evaluation/codec_g21/README.md)),
  not judged by B6.
- **Ingestion through pg_morlet:** about 5% slower than Grizzbit 1 (under B3's 10% threshold for a statement), with
  more server CPU; no read or interval-statistics query differed from Grizzbit 1 by 10% or more.

### Using Grizzbit 2.1

**When to use it.** On waveforms with a value grid, such as vibration, motor currents and EEG: B6 measured
16–20% smaller B3 machine channels and 31% smaller Sleep-EDF EEG. It gains little on integer counts, such as ECG
or seismic counts (ECG 0.4%, CX counts 4% in B6), and nothing on floats without a grid, which stay Grizzbit 1.
Encoding is about 40% slower than Grizzbit 1, and ingestion about 5% slower end to end.

**Turning it on.** `morlet.chunk_format` takes `grizzbit1` (the default) or `grizzbit21`, at any level PostgreSQL
allows for a setting:

```sql
-- this session
SET morlet.chunk_format = grizzbit21;

-- every new session in one database
ALTER DATABASE morlet SET morlet.chunk_format = grizzbit21;

-- every new session on the server (written to postgresql.auto.conf; needs superuser)
ALTER SYSTEM SET morlet.chunk_format = grizzbit21;
SELECT pg_reload_conf();
```

The server-wide setting can also be a line `morlet.chunk_format = 'grizzbit21'` in `postgresql.conf`, followed by a
reload, or a server option. With the release image, append it to the command in
[installation](installation-and-queries.md#fastest-start-the-release-image):

```sh
docker run -d --name morletdb -e POSTGRES_USER=morlet -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=morlet -p 127.0.0.1:54329:5432 -v morletdb-data:/var/lib/postgresql/data \
  ghcr.io/merkhet-io/morletdb:<version> -c morlet.chunk_format=grizzbit21
```

Check the value with `SHOW morlet.chunk_format;` after a `morlet_*` call in the session (`SELECT morlet_version();`
is enough). pg_morlet's library loads with a session's first `morlet_*` call; before that, `current_setting` shows
only a value set for the session, database, role or server, and nothing (NULL with `missing_ok`) when the default
applies. The same holds for `morlet.verify_appends`.

**A misspelt value never falls back quietly.** Once the library is loaded, `SET` refuses any `morlet.chunk_format`
but `grizzbit1` and `grizzbit21`. A value that reaches the session before that, through `SET`, `ALTER DATABASE` or
`ALTER ROLE ... SET`, `postgresql.conf` or `-c`, is kept as written, and every append then fails with
`invalid value for parameter "morlet.chunk_format": "grizbit21"; available values: grizzbit1, grizzbit21`, storing
nothing. `morlet.verify_appends` behaves the same way: a value other than on or off (or true/false, yes/no, 1/0),
however it was set, makes every append fail with `invalid value for parameter "morlet.verify_appends": "..."`.

**What it affects.** Only chunks written from then on, by every append path. Chunks already stored keep their
format; there is no tool yet to recode them (#374). A collection or a recording can hold both formats, and every
read, search and statistic works across them. Write-time verification (`morlet.verify_appends`, on by default)
covers version 5 as it does every format: each chunk is decoded again and compared bit for bit before it is
stored.

**How to check.** The first byte of a stored chunk is its format version. This example writes one chunk of
synthetic gridded samples with each setting, counts the chunks by version, and reads all 8,192 samples back. The
output is from a real run:

```sql
SELECT morlet_create_stream_collection('public', 'g21_demo');
SELECT morlet_start_recording('public.g21_demo', 'motor-1', 25600, 'vibration', 'g',
  '{"asset":"motor-1"}'::jsonb, 512, 256, 'zscore', 'none');

-- 4,096 samples written with the default, Grizzbit 1
SELECT morlet_append_chunk('public.g21_demo', 'motor-1', 0,
  ARRAY(SELECT round(2000 * sin(i / 8.0) * (1 + 0.4 * sin(i / 300.0)) + 300 * sin(i * 1.7)
                     + hashtext(i::text) % 40) * 0.0025
        FROM generate_series(0, 4095) AS i ORDER BY i));

-- the next 4,096 with Grizzbit 2.1
SET morlet.chunk_format = grizzbit21;
SELECT current_setting('morlet.chunk_format');
--  grizzbit21
SELECT morlet_append_chunk('public.g21_demo', 'motor-1', 4096,
  ARRAY(SELECT round(2000 * sin(i / 8.0) * (1 + 0.4 * sin(i / 300.0)) + 300 * sin(i * 1.7)
                     + hashtext(i::text) % 40) * 0.0025
        FROM generate_series(4096, 8191) AS i ORDER BY i));

SELECT recording_id, get_byte(encoded, 0) AS chunk_version,
       count(*) AS chunks, sum(sample_count) AS samples, sum(octet_length(encoded)) AS bytes
FROM g21_demo_chunks
GROUP BY 1, 2 ORDER BY 1, 2;
--  recording_id | chunk_version | chunks | samples | bytes
-- --------------+---------------+--------+---------+-------
--  motor-1      |             2 |      1 |    4096 |  6274
--  motor-1      |             5 |      1 |    4096 |  4203

-- reads decode both versions; the 8,192 samples come back bit for bit
SELECT samples = ARRAY(SELECT round(2000 * sin(i / 8.0) * (1 + 0.4 * sin(i / 300.0)) + 300 * sin(i * 1.7)
                              + hashtext(i::text) % 40) * 0.0025
                       FROM generate_series(0, 8191) AS i ORDER BY i) AS bit_for_bit
FROM morlet_read_interval('public.g21_demo', 'motor-1', 0, 8192);
--  bit_for_bit
--  t
```

The chunk table of a collection `public.c` is `public.c_chunks`. Versions 2 and 3 are Grizzbit 1, version 5 is
Grizzbit 2.1. This synthetic signal gains more (33%) than B6's real waveforms did.

**Turning it off.** `RESET morlet.chunk_format;` for the session, `ALTER DATABASE morlet RESET
morlet.chunk_format;` for a database, `ALTER SYSTEM RESET morlet.chunk_format;` and `SELECT pg_reload_conf();` for
the server (or remove the line or option you added). New chunks are Grizzbit 1 again; version-5
chunks already written stay readable.

**Compatibility.**
- **Readers of version 5:** pg_morlet, the native decoder (`morlet-ffi`, whose `morlet_ffi_max_chunk_version()`
  returns 5) and the Python SDK of 0.3.12-alpha.1 and later. Releases up to 0.3.11-alpha.1
  (pg_morlet, `morlet-ffi` 3, SDK 0.1.0a4) cannot.
- **Compressed transfer negotiates the version:** a client states the highest version it decodes, and the server
  sends samples for any newer chunk, so older clients and SDKs keep working against a server that stores version 5
  ([compressed transfer](compressed-transfer.md)).
- **Embedded edition:** file format 1 always writes Grizzbit 1 and refuses version-5 chunks
  ([one-file format](file-format.md)).
- **Downgrade:** pg_morlet below the release that ships Grizzbit 2.1 cannot read version-5 chunks. Re-append them
  with `grizzbit1` before downgrading.
- **`ALTER EXTENSION pg_morlet UPDATE`:** Grizzbit 2.1 comes with the new library (release image or package) and
  encodes without the update. The update (SQL 0.3.8 to 0.3.9) marks the encoding functions STABLE instead of
  IMMUTABLE, so PostgreSQL never reuses bytes encoded under another value of the setting: run it after installing
  the release.

**A lossless contrast.** Some time-series databases offer lossy float compression. TDengine's float
compressor tsz is documented as lossy (it truncates doubles below 10⁻¹⁶); its default and ZSTD layouts are
lossless, and in B3.6 they returned every sample exactly. Grizzbit has no lossy mode and no precision
parameter to choose.

## Float32 codings (version 6)

Samples that arrive as float32, such as PLC and CNC controller signals
(IEC 61131-3 `REAL`, OPC UA `Float`) or forces scaled from ADC counts in a DAQ, are stored today above their raw
float32 size: Grizzbit 1 codes them as float64, and its grid detection judges fit in float64 last places. Chunk
format version 6 codes a chunk whose every sample is a float32 value either on a float32 grid
(`f32(offset + k · scale)` plus corrections in float32 last places) or as 31-bit magnitudes and sign runs, with
Grizzbit's own integer streams ([specification](chunk-format.md#version-6-float32-codings)).

- **Never larger:** a version-6 chunk is kept only where it is strictly smaller; every other chunk is written as
  before, byte for byte.
- **On by default:** `morlet.float32_chunks = off` turns it off (any level: session, role,
  database, server). Reads decode every version whatever the setting.
- **Compatibility:** readers that do not know version 6 cannot read such chunks; compressed transfer gives them
  samples. Embedded-edition files never contain version 6.
- **Measured (B8):** on IU4 force and ctrl channels, 3.02 and 2.29 bytes per sample instead of 6.60 and 6.52
  with Grizzbit 1; no chunk larger on B6's 30 inputs. Encoding integer counts that fit in float32 is slower
  (about 13–15 instead of 62 M samples/s on one core) for a 1–4 % size gain ([results](../evaluation/storage_b8/RESULTS.md)).

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
| Grizzbit 2.1 | 5 (opt-in) | 0.3.12-alpha.1 | grid chunks with corrections per grid index and a per-chunk LPC, kept only where smaller ([above](#grizzbit-21-opt-in)) |

Version 4 was Grizzbit 2 (pco), which failed study B5 and was never released; the number stays reserved.

The version byte of a chunk is never reinterpreted, and every later Grizzbit decodes every earlier chunk.
Clients that receive chunks on the wire state the highest version they decode, and the server sends
decoded samples for anything newer ([compressed transfer](compressed-transfer.md)).

## Roadmap

**Floats off any grid (#326):** an additional chunk kind for floats that lie off any grid, to close the
gap above, which Grizzbit 2.1 leaves unchanged. A first attempt (Grizzbit 2, chunk format version 4) failed study
B5. The change is additive: the encoder keeps the smaller coding per chunk, chunks on a grid stay
byte-identical, and every Grizzbit 1 chunk stays readable. Its pass criteria are fixed in a protocol
before the benchmark inputs are read; no numbers are claimed until they are measured.

## Specification and readers

- [Grizzbit format specification](chunk-format.md): layout, decoding rules, chunk summary, compatibility.
- [Verification](verification.md): what is proven, tested and fuzzed.
- [`reference/python/morletfile.py`](../reference/python/morletfile.py): a second reader on Python's
  standard library; with the specification it defines the format.
- [One-file format](file-format.md) and [compressed transfer](compressed-transfer.md): Grizzbit chunks
  in a SQLite file and on the wire.
