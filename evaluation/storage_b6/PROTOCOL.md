# Study B6: Grizzbit 2.1 — protocol (FROZEN)

**FROZEN 2026-11-11** in the commit that adds this line, on branch `bench/b6-grizzbit21` on top of `709b444`.
That is on the Grizzbit 2.1 development build `1ede52b` (draft PR #378). The owner approved B6, confirmed
the thresholds in section 5, and decided the input question in section 8, item 1, all on 2026-11-11. No B6
benchmark input has been read with a Grizzbit 2.1 build. The pre-freeze checks in section 8 are resolved.

After the freeze, development decisions go into the **build record** (section 2, gate 8). The build record is
written before any benchmark input is read. It can fix open implementation details. It can never change a
criterion, an input list or a rule.

## 1. Question, hypotheses and the build

**Question.** Grizzbit 1 (chunk format versions 2 and 3) is MorletDB's chunk codec. Grizzbit 2.1 (#377, chunk
format version 5) adds two codings to it:
- corrections stored once per grid index;
- a per-chunk linear predictor (LPC) of order up to 32.

Does Grizzbit 2.1 make sensor waveforms measurably smaller, never larger, at speeds that keep edge ingestion and
reads where they are? Does it write the same bytes on every platform MorletDB ships for? And does it keep B3's
standing against QuestDB?

**Why B6 exists.** B5 tested Grizzbit 2 (pco, format version 4) and failed: S2 did not hold. Version 4 stays
reserved and is never reused. Grizzbit 2.1 is a different coding, so it gets its own frozen study. Like B5, it is
judged on inputs its development never read.

**Hypotheses.** These come from the development measurement (`evaluation/codec_g21`, commit `1ede52b`) and are
tested by the criteria in section 5:
- **H1:** on the six waveforms (B4's, with Paderborn rebuilt without the development bearings), Grizzbit 2.1 stores at least 5% fewer bits than Grizzbit 1 in total. On
  development inputs it stored 10.7% fewer over all 21 inputs, 16.8% fewer on Paderborn and 5.3% fewer on
  FEMTO.
- **H2:** no chunk gets larger. A version-5 chunk is kept only if it is strictly smaller, and every other chunk
  is Grizzbit 1's chunk, byte for byte.
- **H3:** decoding stays at or above 50 M samples/s and encoding at or above 10 M samples/s, single thread, on each waveform
  and each of B3's machine channels. In development the lowest values were 53 M for decode (phase currents) and 14.0 M for encode
  (force).
- **H4:** the encoder writes the same bytes on macOS/arm64, Linux/arm64 and Linux/x86_64.
- **H5:** B3's statements against QuestDB hold.

**The Grizzbit 2.1 development build** is branch `dev/grizzbit-2.1` at `1ede52b` (draft PR #378). It is
crate-only: `grizzbit::encode_with(.., Format::Grizzbit21)`, the version-5 decoder, 8 golden version-5 chunks,
the `decode_v5` fuzz target and 2 Kani proofs. It has not yet been wired into the server, the SDK or the native
decoder (gate 6).

**The build is fixed now:**
- **Format:** chunk format version 5 as specified in `crates/grizzbit/src/g21.rs` at `1ede52b`. The spec text
  moves into docs/chunk-format.md in a section marked "Draft ... under study B6". It becomes part of the
  specification only if a release ships Grizzbit 2.1. Versions 2 and 3 are never reinterpreted. Version 4 is
  rejected as before.
- **Encoder:** the tuned encoder at `1ede52b`:
  - **LPC order selection:** only the order with the best estimate from the Levinson errors is coded exactly
    (`estimated`, as chosen in development).
  - **Side streams:** the table and exception streams try fixed predictors 0–1.
  - **Main stream:** fixed predictors 0–4 plus LPC.
- **Keep the smaller, per chunk:** the encoder codes every chunk with Grizzbit 1 exactly as today and stores the
  version-5 chunk only if it has strictly fewer bytes. On a tie, Grizzbit 1 is kept. So a Grizzbit 1 chunk that
  stays is byte for byte what Grizzbit 1 alone produces.
- **Decoder rules, the same as Grizzbit 1's:**
  - the checksum is checked before any sample is returned;
  - any length mismatch or trailing byte is rejected;
  - any sample that is not finite or lies outside ±1e9 is rejected.

  The LPC decoder uses integer arithmetic only.
- **Determinism:** the LPC fit uses IEEE basic operations only, in a fixed order:
  - no fused multiply-add;
  - no libm;
  - each autocorrelation lag summed in sample order.

  The encoder's bytes are part of the contract and are tested across platforms (S5), unlike B5's pco kind.
- **Chunk sizes are unchanged:** 4,096 samples by default, and the final partial chunk of each input as stored.
- **Write-time verification (#366) applies:** with `morlet.verify_appends` on (the default), every stored chunk,
  version 5 included, is decoded again and compared bit for bit before it is stored, on every append path. A
  mismatch fails the append, with no silent fallback.
- **Opt-in setting:** pg_morlet gets `morlet.chunk_format` = `grizzbit1` | `grizzbit21`, a registered enum
  setting. The default stays `grizzbit1` in this build. B6's arm sets `grizzbit21` in its server configuration.
  Making it the default is a release decision, taken only after B6 passes.
- **Decoders:** every decoder MorletDB ships for the server path learns version 5:
  - `pg_morlet`;
  - the SDK's native decoder (`crates/morlet-ffi`), needed for compressed transfer;
  - `morlet_core::transfer::MAX_CHUNK_VERSION`, which becomes 5. The SQL default of `max_chunk_version` stays
    3, so a caller that does not state its version gets samples.
- **File format 1:** the embedded edition's file format 1 never contains a version-5 chunk. Its writer keeps
  Grizzbit 1, and its reader refuses versions above 3. A test checks both.

The server integration follows B5's build record, decisions 6–11, with version 5 in place of version 4.

## 2. Implementation gates (all before any benchmark input is read)

1. **Round trip and property tests:** Grizzbit 1's property tests run through the keep-the-smaller encoder,
   plus edge cases:
   - n = 1 and n = 32,768;
   - constants and signed zeros;
   - denormals and ±1e9;
   - alternating extremes;
   - grid data with offset and multiplier grids, few and many distinct indices;
   - LPC on pure tones, chirps, white noise and steps;
   - random bit patterns within the accepted range.

   Every decode is compared by `to_bits`.
2. **Golden version-5 chunks:** the 8 golden chunks in `testdata/rawcodec` decode bit for bit, and the encoder
   still writes each one byte for byte, in CI on macOS/arm64, Linux/x86_64 and Linux/arm64.
3. **Grizzbit 1 golden chunks unchanged:** every v2/v3 golden chunk, and
   `testdata/grizzbit/encoder-equivalence.bin`, are reproduced exactly. No existing file under `testdata/` is
   changed.
4. **Fuzzing:** `decode_v5` and `roundtrip` (keep-the-smaller encoder) each run at least 2 CPU-hours with no
   crash, timeout, memory-limit violation or mismatch. The runs use the nightly workflow's limits with
   `-O -a`. Both targets are in the nightly matrix.
5. **Kani:** every existing proof passes, plus proofs of the version-5 frame and the LPC decoder's integer
   arithmetic: no overflow panic, and the same result as a wide-integer reference on bounded inputs.
6. **Server integration and all existing tests:**
   - workspace tests;
   - `cargo pgrx test` on every PostgreSQL version in CI;
   - SDK tests, including the native decoder and compressed transfer against a `grizzbit21` server;
   - embedded edition and file-format tests;
   - the reference reader on `testdata/morlet-file-1`;
   - a pg test that forces a wrong version-5 code and checks that the append is refused.
7. **Development inputs, the only real data B6 may read before the benchmark run:**
   - T1's development inputs (`evaluation/codec_t261/prepare.py`, `2a89c40`): B3 development acquisitions K001,
     KA01, KA09, KB24 and KI08 (vibration_1, phase_current_1, phase_current_2, force) and FEMTO Bearing1_1
     horizontal;
   - B4's four development Elf series: Blockchain-tr, POI-lat, Stocks-USA and electric_vehicle_charging;
   - XJTU-SY Bearing1_1 horizontal (B5's development input);
   - B3's development set, for the harness smoke check only.

   Synthetic data may be used freely.
8. **Build record:** appended here in its own commit before the benchmark run. It contains:
   - the build commit;
   - image digests and decoder sha256;
   - gate results and fuzz hours;
   - development figures, in `results/dev/`;
   - every decision development made.

   If a gate fails, nothing is benchmarked.

## 3. Storage measurements (benchmark inputs)

**Development bearings.** Grizzbit 2.1 was developed on five Paderborn bearings: K001, KA01, KA09, KB24 and KI08.
Every file of these five bearings is excluded from every benchmark input below (section 8, item 1).

**Inputs:**
- **Waveforms (6), the inputs of S2:** CX counts, IMS decimals, Sleep-EDF and ECG as B4 parses them
  (`data/b1-runs`, `measure_b4.py` loaders, unchanged), and **Paderborn counts (B6)** and **Paderborn physical
  (B6)**:
  - built by B1's loader and rules (`evaluation/compression/datasets.py`, `paderborn("heldout")`: the first 2
    accepted vibration files per bearing and setting), minus every file of the five development bearings;
  - the file list and the SHA-256 of each built input are recorded in the build record before any measurement.

  B4's unfiltered Paderborn inputs are measured too, but reported as development.
- **Elf (18):** B4's 22 series minus its four development series:
  - Air-pressure, Air-sensor, Basel-temp, Basel-wind, Bird-migration, Bitcoin-price;
  - City-lat, City-lon, City-temp, Dew-point-temp, Food-price, IR-bio-temp;
  - PM10-dust, POI-lon, SSD-bench, Stocks-DE, Stocks-UK, Wind-Speed.

- **B3 machine channels (6 inputs), the inputs where phase currents are the critical case:** B3's benchmark set
  (`evaluation/tsdb_b3`, every 20th file starting with the first: 124 acquisitions), minus every file of the
  five development bearings:
  - `vibration_1`, `phase_current_1`, `phase_current_2` at 64 kHz;
  - `force`, `speed`, `torque` at 4 kHz;
  - each channel is one input: its acquisitions are concatenated in B3's file order, and every acquisition starts
    a new chunk, as B3 stores them;
  - the number of acquisitions left after the exclusion is recorded in the build record.

  B3's benchmark set and its development set (every 20th file starting at the 11th) share no file. The exclusion
  also removes benchmark files that come from the bearings 2.1 was developed on.

That is 30 benchmark inputs. B4 and B5 have read the B4-derived inputs, and B3 has read its benchmark set, so
their Grizzbit 1 sizes are known. No Grizzbit 2.1 code, and none of its development (T1, codec_g21), has read any
of them. The development series are measured in the
same run but reported apart and not counted in any criterion.

**Arms,** all at 4,096-sample chunks and quantum 1:
- **G1:** Grizzbit 1 at the build commit. It must match B4's `b4_final.json` and B5's G1 exactly. Any difference
  is explained from the codec's history before anything else is read. The encoder tuning in `baa9ff6` is
  meant to change no Grizzbit 1 byte, so a difference here fails gate 3 after the fact and stops B6.
- **G21:** the Grizzbit 2.1 build.
- **References, from published results and not re-run (no criterion):**
  - ClickHouse's best arm and Pcodec 1.0.4 (B4);
  - Grizzbit 2 (B5's G2).

  They are reported so that #326's question, floats without a value grid, is visible next to B6's answer.

**Measured per input:**
- bits per sample (8 × encoded chunk bytes / samples, frame and checksum included);
- G21/G1;
- chunk counts per kind (v2 grid, v3 grid, float, raw, v5) and per version-5 path (table, exceptions, per
  sample, LPC);
- the number of chunks whose bytes differ between G1 and G21;
- a bit-exact round trip of every chunk, compared by `to_bits`.

**Results:** `results/b6_storage.json`.

## 4. Speed and platform measurements

**A. Codec, single thread.**
- **Inputs:** the six waveforms and the six B3 machine channels. The 18 Elf series are reported without a
  criterion.
- **Measures:** encode and decode of G1 and G21, in M samples/s: the input's total samples over its total time
  across all chunks, best of 3 passes, codec in process.
- **Start condition (B3.16, checked before every pass, as amendment B5.S6r learned):**
  - 1-minute load average below 3;
  - no process other than Docker's using more than half a core;
  - AC power;
  - the lid open, or closed without causing sleep.

  Machine snapshots are taken before and after.
- **Results:** `results/b6_speed.json`.

**B. Platforms.** G21 encodes every chunk of all 30 benchmark inputs on:
- macOS/arm64, native;
- Linux/arm64 in Docker;
- Linux/x86_64 in Docker under emulation. This is slow but sufficient: the encoder uses IEEE basic operations
  only.

The SHA-256 of every input's concatenated chunk bytes is compared across the three. The edge reference box (the
Raspberry Pi 3B+ of X2, aarch64) also encodes and decodes the six waveforms. Its speeds are reported without a
criterion, and its hashes count towards S5.

**Results:** `results/b6_platforms.json`.

**C. B3 ingestion and reads.** As B5 section 4B and amendment B5.S6r, unchanged except for the arms:
- **Release:** `ghcr.io/merkhet-io/morletdb` at the then-current release, by digest, with its release decoder.
- **Grizzbit 2.1:** image `morletdb:b6` built with `deploy/Dockerfile` from the build commit, with
  `morlet.chunk_format = grizzbit21`, and the native decoder built from the same commit.
- **QuestDB 10.1.0** with its B3 configuration.

Runs:
- **Track S:** r = 1, 2, 3, D2 then D1, in the order Grizzbit 2.1, QuestDB, release.
- **Track P:** 8 writers, the same order.
- **Before every load:** the start condition is checked. A load whose condition does not hold within 20 minutes
  is reported as not run.
- **Correctness:** B3's full check runs after every load.
- **Smoke check:** one development-set D2 load per arm first.

**Results:** `results/b3-single/` and `results/b3-parallel/`.

## 5. Pass criteria (thresholds confirmed by the owner, 2026-11-11)

All criteria are judged on the 30 benchmark inputs and the B3 session. Each is reported as holding or failing,
with its figures.

- **S1 (exact):** every chunk of every input, every B3 read and every B3 correctness check is bit for bit exact.
  A single mismatch fails B6. The measurement then stops, and the failure is reported.
- **S2 (smaller on waveforms):** over the six waveforms together (total bytes), **G21 ≤ 0.95 × G1**, and G21 <
  G1 on at least **4 of the 6**.
- **S3 (never larger):**
  - every G21 chunk that differs from G1's chunk is a version-5 chunk with strictly fewer bytes;
  - every other chunk is byte-identical to G1's;
  - on every one of the 30 inputs, G21 ≤ G1.
- **S4 (speed):** on each of the six waveforms and each of the six B3 machine channels, single thread:
  - G21 **decode ≥ 50 M samples/s**;
  - G21 **encode ≥ 10 M samples/s**.

  The 10 M floor is about 150× real time at 64 kHz, with headroom for slower hardware. Encode and decode ratios
  against G1 are reported.
- **S5 (same bytes everywhere):** the per-input hashes of section 4B are identical on all platforms measured.
  One differing byte fails B6. The cause is reported, and the encoder is not shipped until it is fixed and B6's
  platform part is rerun under an amendment.
- **S6 (B3 standing):**
  - **(a)** every statement the release arm makes against QuestDB in this session, the Grizzbit 2.1 arm also
    makes, in the same direction;
  - **(b)** no statement that Grizzbit 2.1 reads or computes interval statistics slower than the release.

  Both follow B5's S6 and B3's statement rule. Ingestion against the release is reported per cell, with server
  CPU and peak memory. A slowdown of 10% or more in all three loads is written into the docs as a cost, not a
  failure.

**Verdict:**
- **Grizzbit 2.1 passes B6 if S1–S6 all hold.** Only then may a release offer it, and only a later release
  decision makes it the default.
- Whatever the outcome, every figure is reported, the development series included and labelled.

**On failure:**
- Nothing is dropped, and no subset is presented as a pass.
- The release keeps Grizzbit 1.
- A variant is a new frozen amendment (B6.1, …) with its own run, labelled "after B6's results were known".

## 6. Docs, paper and website (decided now)

- **Not marketed:** no Grizzbit name, row, lane or claim goes on the website.
- **Docs and paper:** docs/grizzbit.md, the spec and the paper get B6's result as measured, including any
  failure and the encode cost.
- **Website:** it changes only when a release ships Grizzbit 2.1 by default, and then only the B4-derived
  storage statements (B5 section 6), labelled with that release, with no codec name.
- **B3 figures** on the site change only after that release gets its own B3 stage.
- **Results stay unpublished** until the owner approves.

## 7. Machine, budget, results, and what is not in B6

- **Machine:**
  - Apple M5 Max (18 cores, 128 GB), macOS 27, Docker Desktop with B3's container limits
    (`--cpus 8 --memory 16g`);
  - fresh volumes named `b6-*`;
  - **no other builds, tests or benchmarks on the Mac while B6 runs**, by the operator or any agent (the
    B5.S6r lesson).
- **Budget:**
  - storage and codec speed: 2 hours;
  - platform hashes: 3 hours;
  - B3 session: 3 hours;
  - disk: 50 GB.

  Loads not started by 07:00 are not run and are reported as such.
- **Results** go in `evaluation/storage_b6/results/`:

  | Path | Contents |
  |---|---|
  | `dev/` | development figures and smoke checks |
  | `build.json` | build commit, image digests, decoder sha256 |
  | `b6_storage.json`, `b6_speed.json`, `b6_platforms.json` | sections 3, 4A and 4B |
  | `b3-single/`, `b3-parallel/` | section 4C |
  | `RESULTS.md` | written after the run, beside this file |

- **Not in B6:**
  - other LPC order strategies or orders above 32, and other chunk sizes;
  - an adaptive or arithmetic entropy coder (T1's remaining 0.05–0.1 bits/sample);
  - re-running ClickHouse, Pcodec, SRLA or T.261;
  - embedded-edition files with version-5 chunks (needs file format 2);
  - Windows and 32-bit platforms;
  - cold-cache reads;
  - any website change beyond section 6.

## 8. Pre-freeze checks (resolved in the freezing commit)

1. **Paderborn provenance: overlap found and resolved, 2026-11-11.**
   - **Finding:** B1's "Paderborn counts" and "Paderborn physical" come from `paderborn("heldout")`, every bearing
     outside `evaluation/decision/bearings.py`'s `DEV` and KA08. That held-out set includes KA09, KB24 and KI08,
     three of the five bearings 2.1 was developed on.
   - **Resolution, decided by the owner on 2026-11-11:**
     - every file of the five development bearings is excluded from every benchmark input;
     - the two Paderborn waveforms are rebuilt without them (section 3), keeping S2's "4 of 6";
     - B3's benchmark channels, with the same exclusion, are added for S3 and S4;
     - B4's unfiltered Paderborn inputs are reported as development.
2. **Thresholds: confirmed by the owner on 2026-11-11.** S2: 5% and 4 of 6. S4: decode 50 M, encode 10 M,
   single thread, on the benchmark machine. Edge hardware is not judged by S4. The Raspberry Pi is about 10×
   slower in development, so an edge figure is reported separately.
3. **Release arm: resolved at the freeze.** The release arm is `ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1`
   (the beta has not shipped). It is pinned by digest in `results/build.json`. If a newer release ships before the
   B3 session, the session still uses 0.3.10-alpha.1, so the build being compared stays fixed.
4. **Beta interplay:** B6 does not gate the beta (#201). The beta freezes the format with Grizzbit 1. If B6
   passes, version 5 is added later as an additive chunk version with upgrade scripts. Every version 2 and 3
   chunk stays valid, and readers older than that release cannot read version-5 chunks.
