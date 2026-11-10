# Study B5: Grizzbit 2 — protocol (FROZEN)

Frozen 2026-11-10 in the commit that adds this file, on main at `1be8e41`. At that point Grizzbit 2 is not
implemented, and none of B5's benchmark inputs has been read for B5. The owner approved B5 (issue #326).

Everything below is fixed now. What development decides later is written into the **build record**
(section 2, gate 8) before any benchmark input is read. The build record can fix open implementation details. It can
never change a criterion, an input list or a rule.

## 1. Question, hypotheses and the build

**Question.** MorletDB's chunk codec is now called **Grizzbit**. Grizzbit 1 is chunk format versions 2 and 3,
specified in [docs/chunk-format.md](../../docs/chunk-format.md) and today implemented in
`crates/morlet-core/src/rawcodec.rs` (moving to `crates/grizzbit`). B4 found that Grizzbit 1 loses badly on
floats without a value grid: 2.2–4.0× ClickHouse's best arm on five Elf series. Does a second chunk coding
based on Pcodec close that gap at MorletDB's chunk size, without changing any chunk where Grizzbit 1 is already
smaller, and without losing read speed or the B3 standing against QuestDB?

**Hypotheses** (from B4's published figures and the Grizzbit 2 development spike; tested by the criteria in
section 5):
- **H1:** on B4's five large losses (SSD-bench, Bird-migration, Basel-temp, Basel-wind, Food-price), Grizzbit 2
  is at most 1.25× ClickHouse's best arm.
- **H2:** on every other benchmark input except Bitcoin-price, Grizzbit 2 is at most 1.10× ClickHouse's best
  arm.
- **H3:** where Grizzbit 1 is smaller, it stays. Only chunks whose pco coding is smaller change.
- **H4:** single-thread decode stays at or above 50 M samples/s on B4's waveforms. B3's statements against
  QuestDB hold.

**The Grizzbit 2 development spike** is commit `a15c611` on branch `spike/grizzbit2-pco`. It is development
only: B4's four development Elf series plus XJTU-SY and FEMTO, which are not B4 inputs. It found:
- pco level 8, run per 4,096-sample chunk, comes within 0.3–0.6% of pco on the whole series wherever pco wins.
  Blockchain-tr is the exception: keeping the smaller per chunk lands there at 1.07× whole-series pco.
- No chunk failed its round trip.
- Keeping the smaller per chunk roughly halves encode speed.
- pco decodes 144–297 M samples/s at 4,096 samples.
- Grid chunks sometimes lose to pco: 15 of 25 on Blockchain-tr.

**The Grizzbit 2 build** is fixed now:
- **Dependency:** the Rust crate `pco`, pinned exactly at `=1.0.4`, with `Cargo.lock` committed. Not 1.1.0
  (2026-11-03), for three reasons:
  - B4's Pcodec reference and the spike both used 1.0.4.
  - 1.1.0 was a week old when this protocol was frozen.
  - pco's format rule says 1.1 decoders read 1.0 data, so a later move is an upgrade gated by the golden pco
    chunks. It is not a reason to start on 1.1.

  Licence: Apache-2.0, with a permissive dependency tree, as checked in the spike.
- **Coding:** pco's **wrapped** format at **compression level 8**. Mode and delta detection stay at pco's
  defaults. One pco chunk with one page per Grizzbit chunk; pco's standalone format is not used.
- **Frame:** the wrapped bytes sit inside Grizzbit's own chunk frame:

  | Field | Value |
  |---|---|
  | version | `4` (new tag) |
  | kind | `3` (pco) |
  | n | varint, 1..32,768 |
  | body | pco wrapped header, chunk metadata and page |
  | crc | CRC-32C of every preceding byte |

  The exact body layout goes into docs/chunk-format.md and the build record. Versions 2 and 3 are never
  reinterpreted, and every Grizzbit 1 chunk stays readable.
- **Decoder rules:** the decoder applies Grizzbit 1's rules to version 4:
  - It checks the checksum before returning any sample.
  - It rejects any length mismatch and any trailing bytes.
  - It rejects any sample that is not finite or lies outside ±1e9.
- **Keep the smaller, per chunk:** the encoder codes every chunk with Grizzbit 1 exactly as today, and also
  with the pco kind. It stores the pco chunk only if it has strictly fewer bytes. On a tie, Grizzbit 1 is kept.
  So a Grizzbit 1 chunk that stays is byte for byte what Grizzbit 1 alone produces.
- **Chunk sizes are unchanged:** 4,096 samples by default, and the final partial chunk of each input as stored.
  B5 records the chunk sizes it stores.
- **Write-time verification applies (#366):** with `morlet.verify_appends` on (the default), every stored
  chunk, version 4 included, is decoded again and compared bit for bit before it is stored, on every append
  path. A mismatch fails the append as today, with no silent fallback to the other coding.
- **Encoder output is deterministic:** the same samples give the same bytes within one build. This is tested.
  It is not promised across pco upgrades, and nothing depends on it, because replay checks compare samples.
- **Decoders:** every decoder that MorletDB ships for the server path learns version 4: `pg_morlet`, and the
  SDK's native decoder (`crates/morlet-ffi`), needed for compressed transfer.
- **File format 1:** the embedded edition's file format 1 never contains a version 4 chunk, because its spec
  allows only versions 2 and 3. Its writer keeps Grizzbit 1 coding, and a test checks this.

## 2. Implementation gates (all before any benchmark input is read)

1. **Round trip and property tests** for the version 4 kind:
   - Grizzbit 1's existing property tests, run through the keep-the-smaller encoder.
   - Edge cases: n = 1, n = 32,768, constants, signed zeros, denormals, ±1e9, alternating extremes, float32
     values held as float64, near-grid values, and random bit patterns within the accepted range.
   - Every decode compared by `to_bits`.
2. **Golden pco chunks:** at least six new chunks in `testdata/rawcodec` (or the grizzbit crate's corpus),
   each with its `.f64` samples and `.json`. Cases: float without a grid, float32 held as float64, near-grid
   values, a constant, a single sample, and the maximum length. A CI test decodes each bit for bit. This
   corpus is the upgrade check for any later `pco` version.
3. **Grizzbit 1 golden chunks unchanged:** every existing v2/v3 golden chunk decodes bit for bit, and the
   encoder still produces each one byte for byte.
4. **Fuzzing:**
   - A new fuzz target decodes arbitrary bytes as a chunk, with version 4 reachable.
   - The existing `roundtrip` target exercises the keep-the-smaller encoder.
   - Each runs at least 2 CPU-hours locally with no crash, timeout or mismatch.
   - Both join the nightly fuzz matrix.
   - Hours and corpus sizes go into the build record.
5. **Kani:** the existing proofs pass. New proofs cover MorletDB's own version-4 frame code (header, lengths,
   checksum dispatch) with pco stubbed. pco's decoder itself (third-party, about 100 `unsafe` sites) is not
   proved: fuzzing and write-time verification cover it. docs/verification.md says so.
6. **All existing tests pass:** workspace tests, the pgrx suite (`cargo pgrx test`, every PostgreSQL version
   in CI), SDK tests including the native decoder, embedded edition and file-format tests, and the reference
   reader on `testdata/morlet-file-1`.
7. **Development inputs, the only real data B5 may read before the benchmark run:**
   - B4's four development Elf series (seed 20261104): Blockchain-tr, POI-lat, Stocks-USA,
     electric_vehicle_charging.
   - XJTU-SY Bearing1_1 (35 Hz / 12 kN), horizontal, files 1–123.
   - FEMTO Bearing1_1, horizontal, files 1–1638.
   - B3's development set, for the harness smoke check only (as in B3.13–B3.20).

   Synthetic data may be used freely in tests.
8. **Build record:** appended to this file in its own commit before the benchmark run. It contains:
   - the build commit and the exact body layout;
   - `pco` version and its lockfile hash;
   - gate results and fuzz hours;
   - development figures, in `results/dev/`;
   - every decision development made, such as the tie rule as built or any pco configuration call.

   If a gate fails, nothing is benchmarked.

## 3. Storage measurements (benchmark inputs)

**Inputs** are B4's benchmark inputs as B4 parses them (`measure_b4.py` loaders, unchanged):
- **Waveforms (6):** CX counts, IMS decimals, Paderborn counts, Paderborn physical, Sleep-EDF, ECG
  (`data/b1-runs`).
- **Elf (18):** B4's 22 series minus the four development series:
  - Air-pressure, Air-sensor, Basel-temp, Basel-wind, Bird-migration, Bitcoin-price;
  - City-lat, City-lon, City-temp, Dew-point-temp, Food-price, IR-bio-temp;
  - PM10-dust, POI-lon, SSD-bench, Stocks-DE, Stocks-UK, Wind-Speed.

That is 24 benchmark inputs. The four development series are measured in the same run, but they are reported
apart as development and not counted in any criterion.

**Arms**, all at 4,096-sample chunks and quantum 1 (the codec detects grids itself, as B4's M-auto):
- **G1:** Grizzbit 1 at the build commit with the pco kind disabled. This is B4's M-auto, recomputed as a
  check. It should match B4's `b4_final.json` exactly. If it does not, the difference is reported and
  explained from the codec's history since B4 before anything else is read, and G1 (B5) is the comparison base.
- **G2:** the Grizzbit 2 build.
- **References, taken from B4's results and not re-run:**
  - ClickHouse's best arm: the smallest of CH-ALP, CH-ALP-zstd9, CH-PCO and CH-zstd9 in `b4_final.json`,
    ClickHouse 26.10.3.7.
  - Pcodec 1.0.4 on the whole series: the PCO arm.

  B4's protocol does not ask for a re-run, and both arms were exact.

**Measured per input:**
- bits per sample, computed as B4 does: 8 × encoded chunk bytes / samples, frame and checksum included;
- G2 / ClickHouse best, and G2 / Pcodec;
- chunk counts per kind for G1 and G2: v2 grid, v3 grid, float, raw, v4 pco;
- the number of chunks whose bytes differ between G1 and G2;
- a bit-exact round trip of every chunk, compared by `to_bits`.

**Harness:** `measure_b5.py` with the codec bench example extended for G2, reporting per-chunk kind and
size. Results go to `results/b5_storage.json`.

## 4. Speed measurements

**A. Codec, single thread.**
- Inputs: B4's six waveforms. The 18 Elf benchmark series are reported too, without a criterion.
- Measures: encode and decode of G1 and G2, in M samples/s. Each figure is the input's total samples divided
  by its total time over all chunks, best of 3 passes.
- Start condition, as B3.16:
  - the 1-minute load average is below 3;
  - no process other than Docker's uses more than half a core;
  - the machine is on AC power, and the lid is open, or closed without causing sleep.

  The runner waits for this and records it. Machine snapshots are taken before and after.
- Results go to `results/b5_speed.json`.

**B. B3 ingestion and reads.** B3's dataset, correctness checks, durability classes, reads (R1–R5),
interval statistics and statement rule all apply unchanged. The references are B3.16 (single connection,
against QuestDB) and B3.17 (parallel writers). B5 measures them again in one session. The arms are:
- **`morletdb-i2` (release):** `ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1`, the current release.
  - Path: B3.16's arm, with B3.10's write path (256 chunks per transaction, batched raw appends, server
    defaults for `morlet.encode_threads` and `morlet.verify_appends`).
  - Reads as the SDK does them, with compressed transfer for reads of at least 32,768 samples.
  - Decoder: the release wheel's `libmorlet_ffi.dylib`, checked against the release's `SHA256SUMS`.
- **`morletdb-i2` (Grizzbit 2):** image `morletdb:b5`, built with `deploy/Dockerfile` from the build commit.
  - The same arm otherwise.
  - Decoder: `libmorlet_ffi.dylib` built from the same commit, with its sha256 recorded, because the release
    decoder cannot read version 4.
  - Without that library the arm fails; it never falls back to raw reads.
- **QuestDB 10.1.0** with its B3 configuration.

**Runs** use `run_b5_b3.sh`, with the harness in `evaluation/tsdb_b3` unchanged except for image, decoder and
volume-name selection:
- **Track S** (B3.16):
  - For r = 1, 2, 3, D2 then D1, load in this order: Grizzbit 2, QuestDB, release. That is 18 loads.
  - On D2 r1 of each arm: storage, reads and statistics from a fresh client process.
  - MorletDB storage is also reported per group (high, mid, process) as chunk bytes, with chunk-kind counts.
- **Track P** (B3.17):
  - For r = 1, 2, 3, with (8 writers, D2) then (8 writers, D1), load in this order: Grizzbit 2, QuestDB,
    release. That is 18 loads, ingestion only.
- **Correctness:** B3's full check runs after every load. A failed load is logged and rerun once at the end
  of the session.
- **Results:** `results/b3-single/` and `results/b3-parallel/`.
- **Start conditions:** the runner enforces B3.16's conditions before the session, and checks power and the
  lid before every load.
- **Smoke check before the run:** one development-set D2 load of each arm, in `results/dev/b3-smoke/`.

## 5. Pass criteria and statement rules (reworded from #326)

All criteria are judged on the 24 benchmark inputs and the B3 session. Each is reported as holding or
failing, with its figures.

- **S1 (exact):** every chunk of every input, every B3 read and every B3 correctness check is bit for bit
  exact. A single mismatch fails B5. The measurement then stops, and the failure is reported.
- **S2 (large losses):** on SSD-bench, Bird-migration, Basel-temp, Basel-wind and Food-price,
  G2 ≤ 1.25 × ClickHouse's best arm. In B4 these stood at 4.0×, 2.7×, 2.5×, 2.5× and 2.2×.
- **S3 (no other large gap):** on every other benchmark input, G2 ≤ 1.10 × ClickHouse's best arm.
  - **One exception, declared now from B4's published figures alone: Bitcoin-price.**
  - There, Pcodec (46.20 bits) and CH-PCO (46.23) are themselves 1.60× the winning arm, CH-ALP-zstd9 (28.80).
    A pco kind cannot reach 1.10×.
  - For Bitcoin-price, S3 requires only G2 ≤ G1, and the loss is reported as remaining.

  #326 wrote one bound of 1.10× for all inputs and 1.25× for the five. Read literally, the second would be
  void, so this protocol reads it as 1.25× for the five and 1.10× for the rest.
- **S4 (a chunk changes only when the new coding is smaller):**
  - Every G2 chunk that differs from G1's chunk is a version 4 chunk with strictly fewer bytes.
  - Every other chunk is byte-identical to G1's.
  - Reported per input: how many chunks changed, out of how many, and for the six waveforms by Grizzbit 1
    kind. This replaces #326's "grid chunks byte-identical": the spike showed grid chunks sometimes lose to pco,
    and those are allowed to change.
- **S5 (decode):** G2 decodes at least 50 M samples/s, single-threaded, on each of the six waveforms
  (section 4A). Encode speed of G1 and G2 is reported with its ratio. No encode threshold applies, because
  keeping the smaller runs both codecs by design. Its cost is judged end to end in S6.
- **S6 (B3 standing):** both parts must hold, judged within one session.
  - **(a)** Every statement the release arm makes against QuestDB in this session, the Grizzbit 2 arm also
    makes, in the same direction, on every measure of tracks S and P. The Grizzbit 2 arm has no statement that
    QuestDB is faster where the release arm has none. Statements follow B3's rule: at least 10% in all three
    loads for ingestion, using the ratio of medians and the all-loads bound; at both p50 and p95 for reads and
    statistics.
  - **(b)** Grizzbit 2 against the release, directly, on every read measure and on interval statistics: no
    statement that Grizzbit 2 is slower.
  - Ingestion of Grizzbit 2 against the release is reported per cell, with server CPU and peak memory. Any
    slowdown of 10% or more in all three loads is a statement and is written into the docs as a cost.
  - It is not a failure on its own. Reasons:
    - #326 protects the comparison with QuestDB, which (a) tests exactly.
    - Ingestion was not server-bound in B3.17 (about 2 of 8 cores in use).
    - A fixed slowdown threshold would add a criterion #326 did not set.

  If the release arm's statements in this session differ from B3.16 or B3.17, the difference is reported. S6
  compares within the session, never across sessions.

**Verdict:**
- **Grizzbit 2 passes B5 if S1–S6 all hold.** Only then may a release make it the default encoder.
- Whatever the outcome, every figure is reported, the development series included and labelled.

**On failure:**
- The failure is reported as measured. No input, chunk or measure is dropped, and no subset is presented as
  a pass. The release keeps Grizzbit 1.
- A fallback variant is a new frozen amendment (B5.1, …) with its own run, never a silent change to the
  build. Candidates:
  - pco only on chunks Grizzbit 1 does not code as grid, which cuts encode cost on grid data and gives up
    some near-grid wins;
  - pco at level 4.
- Because an amendment comes after B5's benchmark inputs have been read, its result is labelled "after B5's
  results were known".

## 6. Docs, paper and website (decided now, whatever the outcome)

- **Not marketed:** the owner does not want Grizzbit marketed on the website. No Grizzbit name, row, lane or
  claim is added there.
- **Docs and paper:** `docs/grizzbit.md`, the spec (docs/chunk-format.md, with version 4 only if Grizzbit 2
  ships) and the paper get B5's result as measured, including any failure, Bitcoin-price's remaining loss and
  the encode cost.
- **The website is touched only where a B4-derived statement changes.** There are two such statements today:
  1. The benchmark matrix row "ClickHouse 26.10 (ALP, PCO), Pcodec 1.0.4, Parquet ALP": smaller than CH PCO on
     5 of 6 signals and than Pcodec on 4 of 6; Pcodec smaller on IMS decimals and Paderborn physical.
  2. The "Where another choice fits better" card "Floats without a value grid".

  B5 measures a development build, so **neither changes on B5 alone.**
- **When a release ships Grizzbit 2 by default:**
  - Row 1 takes B5's waveform figures, labelled with that release.
  - Card 2 takes B5's per-input ratios, naming Bitcoin-price's remaining loss.
  - These are facts about MorletDB's storage, with no codec name.
- **If Grizzbit 2 does not ship:** card 2 gets B4's real numbers instead (2.2–4.0× on floats without a grid),
  as #326 asks "whichever way".
- **B3 figures:** the B3 tabs keep B3.16 and B3.17 figures. A release containing Grizzbit 2 gets its own B3
  stage before any B3 figure on the site changes.

## 7. Machine, budget, results, and what is not in B5

- **Machine:**
  - Apple M5 Max (18 cores, 128 GB), macOS 27, Docker Desktop as in B3, with B3's container limits
    (`--cpus 8 --memory 16g`).
  - B3's own containers and volumes are not reused. B5 creates fresh volumes named `b5-*`.
- **Budget:**
  - Storage and codec speed: at most 2 hours of runtime.
  - B3 session: at most 3 hours.
  - Disk: 50 GB.
  - A run that would exceed the budget stops and reports why. Loads not started by 07:00 are not run, and
    the loads done are reported as such, as in B3.19 and B3.20.
- **Results** in `evaluation/storage_b5/results/`:

  | Path | Contents |
  |---|---|
  | `dev/` | development figures and smoke checks |
  | `build.json` | build commit, image digests, decoder sha256, `pco` version |
  | `b5_storage.json` | storage measurements |
  | `b5_speed.json` | codec speed |
  | `b3-single/`, `b3-parallel/` | B3 tracks S and P |
  | `RESULTS.md` | written after the run, beside this file |

- **Not in B5:**
  - Other pco versions or levels, other chunk sizes, and per-input tuning, which would be a new amendment.
  - An own float32 or float-quantization mode.
  - Re-running ClickHouse, Pcodec or Parquet.
  - ClickHouse ingest and read speed, which needs its own frozen B3 stage, as #326 notes.
  - Other B3 systems.
  - Embedded-edition files with version 4 chunks. That needs file format 2, and a decision on how the
    standard-library reference reader handles pco, before any release writes them.
  - Edge devices and cross-machine speed.
  - Cold-cache reads.
  - Any website change beyond section 6.

## 8. Build record (gate 8)

Written 2026-11-10, after the gates and before any benchmark input is read. It records what development
decided; it changes no criterion, input list or rule above. No B4 benchmark input was read for B5: the only real
data read were the development inputs of gate 7 (below).

### Build

- **Build commit:** `5ce0328` on `bench/b5-grizzbit2` ("Grizzbit 2 (B5 build): pco chunk kind, chunk format
  version 4"), on top of `e9619ab`, the merge of main at `38bdd91` (which contains `crates/grizzbit`) into this
  branch at `758f6cf`. The image `morletdb:b5` and the B5 decoder are built from this commit. Later commits on the
  branch add only results and this record.
- **`pco`:** 1.0.4, `pco = "=1.0.4"` in `crates/grizzbit/Cargo.toml`; Cargo.lock entry checksum
  `52f01ab51a847e7002371c2384b2c88f50dd45982cc2764e7997751971f1bfbf`; `Cargo.lock` at the build commit has
  sha256 `d3eb41593f14efc399fd316033121e19a9742e9dd08dceb0d6eb3f2cd0cf7e38` (the fuzz crate's own lockfile
  `crates/grizzbit/fuzz/Cargo.lock`: `8cffdf54089876e72248493d243c28865da31ce509c2e75cfd36308e8304a2b7`). New
  dependencies, all Apache-2.0 or MIT-compatible: pco, better_io, dtype_dispatch (Apache-2.0), half,
  rand_core 0.6.4, rand_xoshiro (MIT or Apache-2.0), zerocopy, zerocopy-derive (BSD-2-Clause, Apache-2.0 or MIT),
  crunchy (MIT).

### Body layout (version 4), exact

All integers little-endian.

| Bytes | Field |
|---|---|
| 1 | version `0x04` |
| 1 | kind `0x03` |
| 1–3 | n, unsigned LEB128 varint, 1..32,768 |
| 2 | pco wrapped header: format version major `0x04`, minor `0x01` (pco 1.0.4 writes format 4.1) |
| ≥ 1 | pco chunk metadata as `ChunkCompressor::write_meta` writes it: mode, delta encoding, then per latent variable its ANS size and bins, ending at a byte boundary |
| ≥ 1 | the one pco page as `ChunkCompressor::write_page(0)` writes it: delta state, ANS final states, the interleaved ANS-coded latents, ending at a byte boundary |
| 4 | CRC-32C of every preceding byte |

- Written by `FileCompressor::default()`: `write_header`, then `chunk_compressor(samples,
  &ChunkConfig::default().with_compression_level(8))`, `write_meta`, `write_page(0)`. Every other `ChunkConfig`
  field is pco's default: `ModeSpec::Auto`, `DeltaSpec::Auto`, `PagingSpec::EqualPagesUpTo(2^18)` (so one page
  for every n ≤ 32,768; the encoder checks `n_per_page() == [n]`), `enable_8_bit` false.
- Read by `FileDecompressor::new`, `chunk_decompressor::<f64>`, `page_decompressor(rest, n)` and one `read` of
  exactly n samples. The read must report all n samples processed and the page finished, and the remaining
  input must be empty.
- Examples: `testdata/rawcodec/v4-pco-single.chunk` is `04 03 01 | 04 01 | 00 10 00 90 2a 18 95 d4 ad 65 fb 01
  00 | 72 97 cc d4` (22 bytes); every version-4 chunk starts `04 03 <varint n> 04 01`.

### Gate results

| Gate | Result |
|---|---|
| 1. Round trip and property tests | **Pass.** Grizzbit 1's property tests run through the keep-the-smaller encoder (`codec_props.rs`: 2 new properties, 500 cases each per run, alongside the 3 Grizzbit 1 properties): every output is Grizzbit 1's bytes or a strictly smaller version-4 chunk, decodes bit for bit, and every version-4 chunk (kept or not) round trips; resealed mutations of version-4 chunks never panic. Edge cases (`tests/grizzbit2_edges.rs`, 25 cases, each through the version-4 coder alone and through keep-the-smaller, decoded and compared by `to_bits`, encoded twice for determinism): n = 1 (5 values incl. 0, −0, 1e9, a denormal), n = 32,768 (a constant and a float walk), constants on and off a grid, signed zeros, denormals (signed), ±1e9, alternating extremes, extremes with ulp neighbours, float32 held as float64 (4,096 and 32,768), near-grid decimals with ulp noise, decimals as products, values within a thousandth of a grid, random bit patterns within ±1e9, random magnitudes, integer counts; 16 of the 25 keep the pco chunk. Frame rejection and pco modes (`version4_tests`, 9 tests): checksum first, count above/below/0/32,769, trailing byte, missing byte, header without body, NaN/±inf/beyond ±1e9 inside a valid pco body, every other kind under version 4 and kind 3 under versions 2 and 3, dictionaries and lookback windows (below). `cargo test -p grizzbit`: 31 unit, 5 property, 3 edge-case, 3 golden, 1 equivalence test, all passing. |
| 2. Golden pco chunks | **Pass.** 6 new chunks in `testdata/rawcodec` with `.f64` and `.json` (`"encoder": "version4"`): `v4-pco-float` (4,096 samples, 23,891 bytes), `v4-pco-float32` (4,096, 10,743), `v4-pco-near-grid` (4,096, 7,789), `v4-pco-constant` (4,096 × 0.1, 23), `v4-pco-single` (1, 22), `v4-pco-max-length` (32,768, 169,456). `codec_golden.rs` decodes each bit for bit and checks that the encoder still writes it byte for byte; `morlet-ffi`'s C-interface test and the SDK's `test_native` decode them through the native decoder. Inputs are integer arithmetic and IEEE products only (no libm). |
| 3. Grizzbit 1 golden chunks unchanged | **Pass.** The 16 existing v2/v3 chunks decode bit for bit and `encode` still writes each byte for byte; `testdata/grizzbit/encoder-equivalence.bin` (221 records) is reproduced exactly; no file under `testdata/rawcodec` or `testdata/grizzbit` was changed, renamed or removed (18 files added). |
| 4. Fuzzing | **Pass.** See "Fuzzing" below: `decode_v4` 2.67 CPU-hours and `roundtrip` 2.67 CPU-hours on the build commit's codec, no crash, timeout, memory-limit violation or mismatch. Both targets are in the nightly matrix (`.github/workflows/verification.yml`; `decode_v4` seeded from the golden version-4 chunks). |
| 5. Kani | **Pass.** 12 of 12 harnesses verified (CBMC 6.11.0, Kani 0.68.0), the 7 existing ones and 5 new ones of the version-4 frame code with pco stubbed: `version4_header_roundtrips` (3.0 s), `version4_kind_dispatch` (0.12 s), `version4_decode_checks_count_and_samples` (6.7 s), `version4_checksum_is_checked_before_pco` (1.0 s), `keep_smaller_keeps_grizzbit1_unless_strictly_smaller` (1.7 s). docs/verification.md states that pco's decoder is not proved and what covers it. |
| 6. All existing tests | **Pass,** locally on the build commit: `cargo test --locked -p grizzbit -p morlet-core -p morlet-ffi -p morlet-sqlite` (all pass); `cargo clippy --locked` on the same four crates `--all-targets -D warnings` (clean); `cargo fmt --all --check` (clean); `cargo pgrx test` pg17, pg18 and pg19 (153 of 153 each, pgrx 0.19.3); SDK tests `test_offline test_native test_integration test_local test_transfer` against a PostgreSQL 17 server with this build's extension (default Grizzbit 2), the native decoder and the embedded edition from this commit (34 of 34, none skipped); embedded edition and file-format tests (`morlet-sqlite`, 6 of 6, including the new file-format-1 test); the reference reader on `testdata/morlet-file-1` with the library (4 of 4, none skipped). Not run locally, left to CI on the pull request: the with-pgvector extra scripts and the upgrade mode of `ci_local.sh`, the benchmark smoke test and the other Python suites. |
| 7. Development inputs | Only these were read: B4's development Elf series Blockchain-tr, POI-lat, Stocks-USA and electric_vehicle_charging (by name; B4's `dev_names()` was not called, because it hashes every Elf file); XJTU-SY Bearing1_1 (35 Hz / 12 kN) horizontal, files 1–123; FEMTO Bearing1_1 horizontal, files 1–1,638. B3's development set was not needed (no smoke check yet). Synthetic data in tests. |
| 8. Build record | This section. |

### Fuzzing

Local runs on the build commit's codec (the grizzbit library code fuzzed is identical to `5ce0328`'s), on the
Apple M5 Max, 2026-11-10 19:45:29–20:25:31: cargo-fuzz 0.13.2, nightly-2026-10-11, `-O -a` (optimised, debug
assertions and overflow checks on), libFuzzer `-jobs=4 -workers=4 -max_total_time=2400` per target, the nightly
workflow's limits (`-timeout=20 -rss_limit_mb=2048`, `-malloc_limit_mb` 64 for `decode_v4` and 256 for
`roundtrip`, `-max_len` 65,536 and 300,000), at `nice 19`. Each job held about one core throughout (process CPU
time ≈ wall time, checked with `ps` at 20 minutes), so each target ran 4 × 2,401 s = **2.67 CPU-hours**.

| Target | CPU-hours | Inputs run | Coverage (edges / features) | Corpus at the end (per job) | Peak RSS | Result |
|---|---:|---:|---|---|---:|---|
| `decode_v4` | 2.67 | 123.3 M (31.9 + 28.6 + 32.0 + 30.8 M) | 1,623 / 3,867 | 443–448 inputs, 469–485 KB | 605 MB | no crash, timeout, memory-limit violation or failed assertion |
| `roundtrip` | 2.67 | 5.65 M (1.40 + 1.42 + 1.43 + 1.41 M) | 4,661 / 19,566 | 2,539–2,580 inputs, 3.0 MB | 874 MB | no crash, timeout, memory-limit violation or mismatch |

- `decode_v4` was seeded from the 6 golden version-4 chunks (bytes 2 to n − 4); `roundtrip` started from the
  corpus of a 2-job, 2-minute development run.
- libFuzzer's length control grew `roundtrip` inputs to about 10.7 KB, so its chunks stayed below about 5,000
  samples in this run; chunks of up to 32,768 samples are covered by the edge-case tests and golden chunks, and
  by the nightly runs, whose corpus persists.
- Development runs before the official ones: `decode_v4` found the dictionary allocation (decision 4) in 20
  seconds of its first run; after the fix, 4 jobs × 3 minutes (2.4 M inputs) and the official run found
  nothing.
- Hours remaining for gate 4: none.

### Development figures

`results/dev/dev_storage.json`, written by `dev_figures.py` (4,096-sample chunks, quantum 1, both arms through
the codec bench example, which decodes every chunk and compares it by `to_bits`; all exact). G1 = Grizzbit 1
(pco kind disabled), G2 = this build. Speeds were taken while fuzzing ran and are not controlled.

| Input | Samples | Chunks | G1 bits/sample | G2 bits/sample | G2/G1 | Chunks changed (G1 kind) |
|---|---:|---:|---:|---:|---:|---|
| Blockchain-tr | 99,999 | 25 | 13.96 | 13.26 | 0.950 | 15 (v2 grid) |
| POI-lat | 100,001 | 25 | 60.84 | 53.58 | 0.881 | 25 (v2 float) |
| Stocks-USA | 100,002 | 25 | 4.56 | 4.56 | 1.000 | 0 |
| electric_vehicle_charging | 3,395 | 1 | 10.03 | 10.03 | 1.000 | 0 |
| XJTU-SY Bearing1_1 horizontal | 4,030,464 | 984 | 63.66 | 29.79 | 0.468 | 984 (921 v2 float, 63 v2 raw) |
| FEMTO Bearing1_1 horizontal | 4,193,280 | 1,024 | 10.65 | 10.65 | 1.000 | 0 |

Every changed chunk is a version-4 chunk with strictly fewer bytes than G1's (checked by the script). This agrees
with the spike: grid chunks sometimes lose to pco (15 of 25 on Blockchain-tr).

### Development decisions

1. **How callers choose the coding.** `grizzbit::encode(x, q)` stays Grizzbit 1, byte for byte, so the golden
   chunks and the equivalence vector keep testing Grizzbit 1 unchanged. Grizzbit 2 is
   `grizzbit::encode_with(x, q, Format::Grizzbit2)`; `Format::Grizzbit1` (the `Default`) is `encode`. B5's G1
   arm ("pco kind disabled") is `Format::Grizzbit1`. `grizzbit::encode_version4(x)` returns the version-4 chunk
   whatever its size, for tests, golden chunks and fuzzing.
2. **Keep the smaller, as built.** `encode_with` computes Grizzbit 1 first, then the version-4 chunk, and keeps
   the version-4 chunk only if `len(v4) < len(g1)`; on a tie, or if pco returns an error (never seen; the
   `roundtrip` fuzz target asserts it does not happen for valid samples), Grizzbit 1's bytes are returned. pco
   is always run; there is no shortcut that skips it.
3. **pco configuration.** `ChunkConfig::default().with_compression_level(8)`; everything else at pco's defaults
   (Auto mode and delta detection, default paging, which gives one page). The encoder treats more than one page
   as "pco not available" (keeps Grizzbit 1); it cannot happen for n ≤ 32,768.
4. **Decoder bounds on two pco allocations.** In development the new `decode_v4` fuzz target found within 20
   seconds that a 46-byte version-4 chunk made pco allocate 128 MiB: pco's reader allocates a declared
   dictionary (up to 2^25 numbers) before checking that the body holds it. pco also accepts lookback windows up
   to 2^24 numbers and allocates them when the page is opened. The frame code therefore:
   - reads the dictionary length from the body before pco does (wrapped header 1 or 2 bytes, then a 4-bit mode
     variant and, for mode 4, a 25-bit length, least significant bit first), refuses a dictionary longer than
     n, and after pco has read the metadata refuses the chunk if pco's dictionary length differs from that
     pre-read;
   - refuses a lookback window above 2^15 numbers after pco reads the metadata and before it opens the page.
     2^15 is the most pco 1.0.4's encoder writes (its private `ENCODING_LOOKBACK_MAX_WINDOW_N_LOG`), so no chunk
     the encoder writes is refused. pco's encoder never writes dictionary mode (only with `ModeSpec::TryDict`);
     dictionary chunks of up to n entries still decode (tested with `TryDict`).
   Errors are `CodecError::Corrupt` with "pco header", "pco chunk metadata", "pco page", "pco sample count",
   "trailing data", "pco dictionary" or "pco lookback window".
5. **Frame code details.** `kind_of` now maps the grid kind to versions 2 and 3 only (before, `verified`
   admitted only 2 and 3 anyway, so Grizzbit 1 behaviour is unchanged). `grizzbit::MAX_VERSION = 4` is the
   highest version `decode` reads; `FORMAT_VERSION` stays 3. A unit test that used version byte 4 as an unknown
   version now uses 5.
6. **pg_morlet's default.** The B3 arm runs `morletdb:b5` "with server defaults", so this build's pg_morlet
   writes Grizzbit 2 by default: `morlet.chunk_format` = `grizzbit2` (or `grizzbit1`), a registered enum
   setting (unlike `morlet.verify_appends`, so that `current_setting` reports it once the library is loaded).
   It is read in the backend's thread and passed to the encoder threads. `morlet_encode_chunk` stays declared
   immutable although its bytes now depend on the setting. No SQL change.
7. **Write-time verification (#366).** `encode_checked` decodes whichever chunk was kept and compares it bit for
   bit; a mismatch fails the append, with no fallback to the other coding. A pg test forces a wrong version-4
   code and checks that the batch append refuses it.
8. **Compressed transfer.** `morlet_core::transfer::MAX_CHUNK_VERSION` and `morlet_ffi_max_chunk_version()` are
   4. The SQL default of `max_chunk_version` stays 3 (no SQL change), so a caller that does not state its
   version gets samples for version-4 chunks; the SDK passes its decoder's version. The pg test
   `encoded_reads_decode_to_raw_reads` now loops over client versions 4..1 and checks that its data holds
   version-4 chunks; its compressed-size check passes version 4.
9. **`morlet_recording_storage`** (SQL unchanged) counts pco chunks in `chunks` and `encoded_bytes` but in none of
   `grid_chunks`, `float_chunks`, `raw_chunks`; a pg test records this. The existing pg test of those kind counts
   now runs its appends with `SET LOCAL morlet.chunk_format = grizzbit1`, so it keeps testing Grizzbit 1's kinds.
   B5 counts kinds from the chunks themselves (section 3), not from this function.
10. **File format 1.** morlet-sqlite's writer calls `encode` (Grizzbit 1). Its reader now refuses a chunk whose
    version byte is above 3 ("chunk format version 4 is not allowed in file format 1"), on reads, statistics
    and its own write-time check. The standard-library reference reader already refused version 4.
11. **SDK transfer labels.** The server's encoder label gets " (Grizzbit 2)" when
    `current_setting('morlet.chunk_format', true)` is `grizzbit2`, so file-to-server checks report chunk bytes
    as "not compared" rather than "different" between a Grizzbit 1 file and a Grizzbit 2 server. The existing
    transfer integration tests compare bytes between equal encoders and now set `grizzbit1` in `setUp`; a new
    test covers a Grizzbit 2 server with format-1 files.
12. **Golden pco chunks** live with the others in `testdata/rawcodec`, marked `"encoder": "version4"` and
    `encoder_current: true`; a later pco that writes other bytes sets that to false and adds files.
13. **Fuzz targets.** `decode_v4` prefixes version 4 and kind 3 to the input and seals it, so every input reaches
    the frame parser and pco. `roundtrip` additionally runs `encode_with(.., Grizzbit2)` (Grizzbit 1's bytes or
    a strictly smaller version-4 chunk, same errors, decodes bit for bit) and `encode_version4` (decodes bit for
    bit). Local runs used the nightly workflow's limits and debug assertions (`-O -a`).
14. **Kani stubs** are `cfg(kani)` replacements of the two pco calls in `crates/grizzbit/src/pco_body.rs`, not
    `kani::stub`, so the CI invocation needs no unstable flag. A first form of the checksum proof (8-byte chunks
    through `decode`) ran 18 minutes without a verdict; the proof as built checks `verified` on 7-byte chunks.
15. **Bench example.** `rawcodec_bench` gains `--grizzbit2` (adds `format` and `pco_chunks` to its line) and
    `--per-chunk` (version, kind and bytes of every chunk). Without them its output is as before, so B4's
    harness still reads it.
16. **Specification.** The version-4 layout is in docs/chunk-format.md as a section marked "Draft ... under
    study B5", becoming part of the specification only if a release ships Grizzbit 2 (section 6), and here.
