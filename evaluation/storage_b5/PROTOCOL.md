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
