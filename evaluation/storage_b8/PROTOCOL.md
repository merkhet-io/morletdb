# Study B8: float32 chunk codings (chunk format version 6) — protocol (FROZEN)

**FROZEN 2026-11-14** in the commit that adds this file, on branch `bench/b8-float32` on top of the development
spike `9cbea2e` (`evaluation/storage_f32_dev`). The owner approved B8 on 2026-11-14 and decided: the thresholds in
section 5 as proposed; the new codings become the **default** if B8 passes; IU4 is the only float32 source. No
benchmark input (section 3) has been read by any code of this study or of B7.

**Local and unpublished:** B8 uses IU4 data. Nothing of B8 is pushed or cited before the owner approves.

After the freeze, development decisions go into the **build record** (section 7), written in its own commit
before any benchmark input is read. It can fix implementation details. It can never change a criterion, an
input list or a rule.

## 1. Question and hypotheses

**Question.** Grizzbit widens float32 samples to float64 and codes them as float64. B7 found MorletDB storing
float32 machine data above float32 size (5.20 B/sample force, 4.91 ctrl, table bytes), larger than ModelarDB's
lossless mode (3.65, 3.27). Do two float32 codings in Grizzbit's own style, used only where they are smaller,
close that gap without changing any other chunk, any sample bit, or read and write speed?

**Hypotheses** (from the development spike, IU4 runs[5::40], 4,096-sample chunks):
- **H1:** encoded chunk bytes about 3.0 B/sample force and 2.2 ctrl (spike: 3.01, 2.24; today 6.6, 6.5).
- **H2:** smaller than ModelarDB lossless's B7 table bytes on both groups.
- **H3:** no chunk of any other input gets larger; chunks that are not float32-exact are byte for byte as today.
- **H4:** decode at least 50 M samples/s and encode at least 10 M samples/s single-threaded (spike, not quiet:
  decode about 110 M, encode 38–85 M).
- **H5:** the encoder writes the same bytes on macOS/arm64, Linux/arm64 and Linux/x86_64.

## 2. The build (fixed now)

- **Chunk format version 6**, additive. Versions 2, 3 and 5 are never reinterpreted; version 4 stays rejected.
  Every version-6 chunk decodes to float64 samples, each exactly a float32 value widened. Two kinds:
  - **float32 grid:** a sample is `from_ord32(ord32(f32(offset + k · scale)) + c)`, with `offset` and `scale`
    float64 in the chunk header, `k` the grid index stream and `c` the correction stream in float32
    order-preserving units (`ord32`: +0 is 0, -0 is -1, order kept). `offset + k · scale` is one IEEE multiply
    and one add in float64, then rounded to nearest float32. The grid is detected from the chunk with
    tolerances in float32 last places;
  - **float32 sign/magnitude:** the 31-bit magnitude bit patterns as one integer stream, and the signs as the
    first sign plus run lengths, a second integer stream (absent when the chunk has one sign).
  - Every integer stream is Grizzbit's existing stream: fixed predictors 0–4 or the version-5 LPC, partitioned
    Rice coding. The frame (version byte, kind, sample count, payload, CRC-32C) follows Grizzbit's rules.
- **When it is used:** only for a chunk whose every sample equals its float32 rounding (`x == (x as f32) as f64`),
  and only when the version-6 chunk is **strictly smaller** than the chunk the configured format writes without it
  (Grizzbit 1, or Grizzbit 2.1 when `morlet.chunk_format = grizzbit21`). On a tie the existing chunk is kept, so
  any chunk that stays is byte for byte today's.
- **Default:** with B8 passed, version 6 is used by default (owner's decision). The build adds a setting to turn
  it off (name fixed in the build record); B8's arms use the default.
- **Decoder rules, as Grizzbit's:** the checksum is verified before any sample is returned; length mismatches,
  trailing bytes, run lengths that do not sum to the sample count, and samples outside the accepted range are
  rejected. Decoding uses integer arithmetic plus the one float64 multiply-add and float32 rounding above.
- **Determinism:** the grid fit uses IEEE basic operations in a fixed order (no fused multiply-add, no libm).
  The stored `offset` and `scale` make decoding independent of the fit; the encoder's bytes are tested across
  platforms (S6).
- **Write-time verification (#366) applies** to version-6 chunks as to every other.
- **Decoders:** pg_morlet, the SDK's native decoder (`crates/morlet-ffi`) and `morlet_core::transfer`
  (`MAX_CHUNK_VERSION` becomes 6; the SQL default `max_chunk_version` stays 3). File format 1 never contains a
  version-6 chunk; its reader refuses it (test).
- **Compatibility, stated:** a database with version-6 chunks can be read only by builds that know version 6.
  This is documented in the changelog and the upgrade notes.

## 3. Inputs

- **Development (read before the freeze, and the only real data the build may read):** IU4 runs[5::40], sorted by
  (machine, tool, run), force and ctrl channels; B6's development inputs; B3's development set for the harness
  smoke check. Synthetic data freely.
- **Benchmark, S2–S4 (never read before the build record):** IU4 runs[15::20] sorted by (machine, tool, run), 321
  runs, disjoint from B7's two sets and from the development set:
  - **force:** force_sensor_x/y/z, 25 kHz;
  - **ctrl:** torque_spindle, torque_axis_x/y/z, position_control_deviation_axis_x/y, 500 Hz.

  Every channel must be float32-exact (as B7 checks); a manifest of source SHA-256s and per-channel float32
  SHA-256s is written before any measurement.
- **No-regression inputs, S5:** B6's 30 benchmark inputs, as B6 builds them (their Grizzbit 1 and 2.1 sizes are
  published).

## 4. Measurements

**A. Storage, codec level.** 4,096-sample chunks, quantum 1, each run's channel a series starting a new chunk.
Arms: **G1** (Grizzbit 1 at the build commit, version 6 off) and **G1+6** (the default build). Per group and per
input: bytes per sample (encoded chunk bytes, frame and checksum included), chunk counts per version and kind, a
bit-exact round trip of every chunk by `to_bits`. For S5 also arm **G21+6** against B6's G21.

**B. Storage, table level.** One load of each benchmark group through B7's MorletDB path (supported importer core,
chunk 4,096, defaults) into a fresh PostgreSQL with the default build; size as B7 measures it (chunk table with
TOAST and indexes plus the recordings table, server stopped, after B7's settle step). Full read-back compared
with the source float32 bits.

**C. Speed, single thread.** Encode and decode of G1 and G1+6, M samples/s over all chunks of an input, best of
3 passes, codec in process. Inputs: force, ctrl and B6's six waveforms and six B3 machine channels. Start
condition before every pass: one-minute load average below 3, no other benchmark or build running.

**D. Platforms.** Golden version-6 chunks and an encoder-equivalence file reproduced byte for byte in CI on
macOS/arm64, Linux/x86_64 and Linux/arm64.

**E. Reported, no criterion:** pcodec 1.0.4 on the same benchmark chunks; decode speed on the Raspberry Pi 3B+.

## 5. Criteria (all must hold)

- **S1, exact:** every chunk of every benchmark and no-regression input round-trips bit for bit, in every arm.
- **S2, size:** on the benchmark set, encoded chunk bytes at most **3.3 B/sample force** and **2.6 ctrl** (G1+6).
- **S3, against ModelarDB:** table bytes per sample (4.B) below ModelarDB lossless's B7 table bytes: **3.65
  force, 3.27 ctrl**. (B7's ModelarDB figures are from B7's runs, not re-run; stated as such.)
- **S4, speed:** decode at least **50 M samples/s** and encode at least **10 M samples/s** for G1+6 on every
  input of 4.C.
- **S5, no regression:** on B6's 30 inputs, no chunk is larger with version 6 than without it, and every chunk
  that is not version 6 is byte for byte the same; totals reported.
- **S6, determinism:** 4.D passes on all three platforms.

If any criterion fails, version 6 does not ship as the default; the result is recorded as it is.

## 6. Implementation gates (all before any benchmark input is read)

1. Round-trip and property tests through the keep-the-smaller encoder: n = 1 and 32,768; constants; ±0 and runs
   of signed zeros; float32 subnormals, the largest float32 values within ±1e9; alternating signs; grids with and
   without offset; random float32 bit patterns in range; float64 chunks with one non-float32 sample (must not be
   version 6).
2. Golden version-6 chunks (both kinds, LPC and fixed predictors, one sign and many) decode bit for bit and are
   re-encoded byte for byte.
3. Every existing golden chunk and `testdata/grizzbit/encoder-equivalence.bin` reproduced exactly; no existing file
   under `testdata/` changed.
4. Fuzzing: a `decode_v6` target and the round-trip target each at least 2 CPU-hours with the nightly limits, no
   finding; both in the nightly matrix.
5. Kani: existing proofs pass, plus `ord32` / `from_ord32` as inverses for every 32-bit pattern and the
   run-length expansion's bounds.
6. Server integration and every existing test (workspace, `cargo pgrx test` on every CI PostgreSQL, SDK with the
   native decoder and compressed transfer, embedded edition, reference reader), plus a pg test that forces a wrong
   version-6 code and checks that the append is refused.
7. The docs: version 6 in `docs/chunk-format.md` marked "Draft, under study B8" until a release ships it.

## 7. Build record

Appended here before the benchmark run: build commit, gate results and fuzz hours, development figures, the
setting's name, and every development decision. If a gate fails, nothing is benchmarked.

### 7.1 Record (2026-11-14, before any benchmark input was read)

**Build commit:** `4ed5e90` on `bench/b8-float32`. Server image `morletdb:b8-4ed5e90`,
`sha256:7f074a1fa3b0ade75ad99633b4fe6df34608f9b19aa31b898e6b5a7ca5d2a057`.
**Setting:** `morlet.float32_chunks` (`on`/`off`, default `on`).

**Gates (section 6), all passed on `4ed5e90`:**
- property tests (256 cases), malformed-chunk tests, 7 `v6-*` golden chunks and the float32 equivalence file
  (`testdata/grizzbit/float32-equivalence.bin`, 493,900 bytes), both written in this build;
- Kani: `ord32_is_a_bijection`, `sign_runs_are_checked`;
- fuzz `decode_v6`: 190 M runs in 7,261 s, no finding (decoder unchanged since);
  fuzz `roundtrip` (float32 included): 2,770,498 runs in 7,261 s on `4ed5e90`, no finding;
- pg_morlet on PostgreSQL 17: 188 of 188; workspace tests; Python SDK 38 of 38, none skipped, on the image above;
- S6: identical bytes on macOS arm64, Linux arm64 and Linux amd64 (`results/dev/platforms_4ed5e90.txt`).
  Run locally in Docker (`rust:1.99.0-bookworm`, network off), not in CI, because the branch is not pushed.

**Development figures (runs[5::40], 161 runs; `results/dev/`), bytes per sample:**

| input | G1 | G1+6 | G21+6 | table (G1+6) | pcodec 1.0.4 |
|---|---:|---:|---:|---:|---:|
| force (53.9 M samples) | 6.584 | 3.022 | 3.018 | 3.363 | 2.874 |
| ctrl (2.16 M samples) | 6.542 | 2.287 | 2.195 | 3.042 | 1.607 |

All exact; no chunk larger with version 6. Speed (quiet, 1 thread, best of 3), G1+6 encode/decode M samples/s:
force 22.59 / 89.58, ctrl 12.64 / 67.17. Storage and table figures were measured on `9407a06`; the later commits
changed only encoder speed, and the stored bytes are identical (checked by the hash and equivalence tests).

**Development decisions:**
- magnitude and correction streams without LPC (corrections as side streams), for encode speed on ctrl;
- the grid coding is tried only when 4 · distinct ≤ 3 · samples;
- for Grizzbit 1, a provable lower bound on the float-kind length skips coding it when version 6 is already smaller;
- one shared unstable sort of the distinct values for grid detection and version 6.

Nothing here was tuned on runs[15::20] or B6's benchmark inputs.
