# Study B9: Grizzbit 2.2 speed, no format change — protocol (FROZEN)

**FROZEN 2026-11-16** in the commit that adds this file, on branch `perf/394-simd-lpc` on top of `345eb26` (the
#394 speed work merged with main at `9361aba`, which includes #507). The criteria are the done criteria of #394 as
the owner amended them on 2026-11-16. Everything else here (inputs, arms, tolerances, order, statistics) is fixed
now, before any benchmark input has been read by any build of the #394 work.

**Local and unpublished.** The branch is not pushed. Nothing of B9 is pushed or cited before the owner approves.
The owner reviews this protocol before the run. A change after that review is allowed only as a dated amendment
in section 10, written before any benchmark input is read. After the first benchmark input is read, nothing above
section 10 changes.

**Labels.** Every figure quoted here from the #394 commits or their logs is a **development measurement** on
development and synthetic inputs (KA09 of B3's development set, three synthetic inputs). None is a result.

## 1. Question

Grizzbit 2.1 (chunk format version 5) passed B6 with thin speed margins, and on the Raspberry Pi 3B+ it decodes
the B3 phase currents at about 4.6 M samples/s (development, `evaluation/codec_g21`). #394 changes only the
implementation, not the format:
- a SIMD LPC prediction kernel (NEON; SSE2 or AVX2 chosen at run time) with a narrow i32 path;
- first-occurrence slots through an open-addressing table;
- the Rice/escape decode loop keeping the bit position in a local;
- the LPC encoder checking and copying a chunk to i32 once;
- the fixed-predictor restore fused into the partition decode loop.

Does this make decoding at least 1.3× faster on the edge reference box, with no input slower on either machine,
and with every byte unchanged?

## 2. Hypotheses and pass criteria

All four must hold. Each is reported as **holds**, **fails** or **not demonstrated** (a measurement it needs was
not obtained under section 6), with its figures. "Not demonstrated" counts as not holding for the verdict.

Terms used below:
- **The 12 inputs:** B6's six waveforms (CX counts, IMS decimals, Sleep-EDF, ECG, Paderborn counts (B6), Paderborn
  physical (B6)) and B6's six B3 machine channels (vibration_1, phase_current_1, phase_current_2, force, speed,
  torque). These are the inputs of B6's section 4A criterion, the set #394 names as "the B6 waveforms and B3
  channels" (section 4).
- **Configurations** (encoder settings that ship, section 3): **G1** (Grizzbit 1, version 6 off), **G1+6**
  (Grizzbit 1 with version 6, the default), **G21** (Grizzbit 2.1, version 6 off) and **G21+6** (Grizzbit 2.1 with
  version 6: `morlet.chunk_format = grizzbit21` with the default `morlet.float32_chunks = on`).
- **Ratio:** candidate speed / baseline speed for one machine, input, configuration and operation (encode or
  decode), computed as in section 7 (best of 3 per run, as B6's section 4A, best run per commit within an A-B-B-A
  block).

**H1 (Pi decode at least 1.3× faster).** On the Raspberry Pi 3B+, in configuration **G21+6**, the decode ratio
is **at least 1.30 on each of the 12 inputs**.
- Per input, as B6 judged section 4A per input. No aggregate, mean or subset replaces a failing input.
- The sample-weighted aggregate over the 12 inputs is reported, without a criterion.
- G21+6 is the configuration the issue is about: Grizzbit 2.1 as a user who opts in gets it today, version 6 being
  on by default. It equals G21 on 10 of the 12 inputs; on CX counts and Paderborn counts (B6) version 6 replaces
  most chunks (B8). G1, G1+6 and G21 decode ratios on the Pi, and every ratio on the M5 Max, are reported without a
  1.3× criterion. A gain on the M5 Max is reported, not required (owner's amendment).
- The threshold is applied to the unrounded ratio; 1.2999 fails.

**H2 (no input slower, on either machine).** On the Apple M5 Max **and** on the Raspberry Pi 3B+, for each of
the 12 inputs, each of the four configurations, encode and decode:
- **decode ratio ≥ 0.97** and **encode ratio ≥ 0.95**.
- A ratio below its bound means that input is slower, and H2 fails. A ratio between the bound and 1.00 is
  reported as "within the noise tolerance", neither as a loss nor as a gain.
- That is 12 × 4 × 2 = 96 comparisons per machine, 192 in all. Each is listed.

Why these tolerances (decided now, from B6's and B8's published variance on the M5 Max and the development logs
on the Pi):
- Within one B6 session (`storage_b6/results/b6_speed.json`), the three passes of an input differ by at most
  1.4% in decode on the 12 inputs (median 0.4%), and by at most 4.1% in encode, apart from Sleep-EDF's first
  encode pass (16%, a cold first pass that best of 3 absorbs).
- Between sessions three days apart, Grizzbit 1's best of 3 on the same inputs and the same Grizzbit 1 chunks
  (B6 on 2026-11-11, B8 on 2026-11-14) differs by at most 1.8% in decode (ratios 0.982–1.001) and 4.1% in encode
  (0.983–1.041). B3 speed's encode (0.744) is left out. It is most likely a code change, not noise: B8's build
  changed Grizzbit 1's grid detection (one shared sort, B8 build record), which float inputs such as B3 speed
  run through.
- On the Pi (development logs, A-B-B-A, same binary in one block), the two runs of one binary differ by at most
  about 3% in decode, and in encode usually by under 1% but by up to 9% in a single low run. Taking each
  commit's best run removes such single low runs.
- B9 compares within one session, interleaved, with four runs per commit (section 6), so its noise is below the
  between-session figures. 3% for decode is about 1.7× the largest between-session decode difference; 5% for
  encode is about 1.2× the largest encode difference. Smaller bounds would fail on noise alone; larger ones
  would hide a real loss of the size #394 is about (the thin B6 margins are 6–8%).

**H3 (no format change: byte identity on four platforms).** All of the following:
- **(a) Benchmark inputs.** For every input of section 4 (the 12 inputs, the 18 Elf series and the four
  development Elf series) and every configuration, the SHA-256 of the input's concatenated chunk bytes written by
  the candidate equals the baseline's. This is checked on:
  - **macOS/arm64**, native, on the M5 Max;
  - **Linux/arm64**, in Docker on the M5 Max (`rust:1.99.0-bookworm`, linux/arm64);
  - **the Raspberry Pi 3B+**, native aarch64 (Cortex-A53), the 12 inputs only;
  - **Linux/x86_64**, in Docker on the M5 Max under emulation (linux/amd64), as B6 did. The LPC kernel the
    emulated CPU selects (SSE2 or AVX2) is recorded. If the owner provides a native x86_64 Linux host before the
    run, the same hashes are also computed there, and that run counts as well.

  The hashes must also agree across all platforms for each input and configuration.
- **(b) Native tests on every platform.** On the candidate commit these tests pass natively:
  - `codec_golden`, `encoder_equivalence` and `float32_equivalence`;
  - the LPC kernel equivalence unit tests (`kernels_match_the_definition_on_extremes`,
    `kernels_match_the_definition_on_random_windows`, `kernels_agree_on_streams`,
    `narrow_path_switches_over_mid_chunk`, `detected_kernel_is_available`);
  - the corpus test of gate 5 (section 5).

  The platforms are:
  - macOS/arm64: locally and in CI (`macos-15`);
  - Linux/arm64: in CI (`ubuntu-24.04-arm`);
  - **Linux/x86_64: natively in CI** (`ubuntu-24.04`, the `core` job and the native job). The log line
    `LPC kernels: [...], detected ...` must show both `Sse2` and `Avx2` compared, so both x86_64 kernels are
    checked natively against the scalar kernel and the format's definition;
  - the Pi: the test binaries built for linux/arm64 (glibc) and run there in the container of section 6.

  The CI part needs the candidate pushed. **Pushing needs the owner's approval.** Until that CI run exists, H3 is
  reported as **pending**, never as holding.
- **(c) No golden changes.** `git diff 9361aba <candidate> -- testdata/` is empty.

A single differing byte fails H3. The measurement then stops and the cause is reported.

**H4 (fuzzing and proofs).** These are judged as gates (section 5, gates 3 and 4), before any benchmark input is
read:
- **Fuzzing on the candidate.** Each target at least **2 CPU-hours**, with no crash, timeout, memory-limit
  violation, failed assertion or mismatch:
  - `decode_v5` and `roundtrip`, as #394 requires;
  - **`decode_sealed` and `decode_v6`** too. The fused fixed-predictor restore changes `decode_stream`, which
    every chunk version uses. `decode_sealed` reaches the version-2 and version-3 parsers, and `decode_v6`
    reaches the LPC kernel and the fused restore through version 6. `decode_v5` alone covers neither.
- **Kani.** All **20** harnesses verify on the candidate. That is the 18 in CI's list today plus the two new ones,
  `lpc_kernel::proofs::narrow_product_is_the_wrapping_product` and
  `lpc_kernel::proofs::narrow_sum_of_format_coefficients_cannot_wrap`. `.github/workflows/verification.yml` must
  list all 20 on the candidate commit (gate 1), and CI's Kani job must pass there. Like H3(b), the CI part waits
  for an approved push and is "pending" until then.

**Verdict.** #394's done criteria are met only if H1–H4 all hold. Whatever the outcome, every figure is reported,
including the M5 Max gains, the Elf series and the reported-only arms.

## 3. Arms and the build

- **Baseline: `9361aba`**, main with #507. This is the reference for every criterion.
- **Candidate:** the commit recorded in the build record (section 5, gate 8). Its `crates/grizzbit/src` must be
  identical to `345eb26`'s: `git diff 345eb26 <candidate> -- crates/grizzbit/src` is empty. Commits between
  `345eb26` and the candidate may change only CI workflows, tests, docs and `evaluation/`. A codec source change
  before the build record needs a dated amendment in section 10 that names the new codec tree. After the build
  record there is none.
- **The candidate hash is written at the top of `results/build.json` and of every results file before the first
  benchmark input is read.**

**Why `9361aba` and not `c7113c9`.** `c7113c9` is main just before the speed work. #507 (`a74c88c`, merged in
`9361aba`) changed encoder selection on main in between. It now writes a version-5 or version-6 LPC stream only
when the written stream is strictly shorter, and it adds new correction-section skips. It changed no decoder
code. So, against `c7113c9`:
- **Bytes:** some version-5 and version-6 chunks differ by design (three version-6 goldens show it). Byte
  identity against `c7113c9` cannot hold, and could not separate #394 from #507.
- **Encode speed:** #507 now writes the fixed stream before deciding on LPC. An encode comparison against
  `c7113c9` would mix #507's cost or gain into #394's.
- **Decode speed:** the decoder code of `c7113c9` and `9361aba` is the same. Against `9361aba`, both arms decode
  the same chunk bytes (H3), so decode compares code alone.

`9361aba` therefore isolates #394 for all three questions. **`c7113c9` is measured as a reported-only arm** on
the M5 Max (section 6.D). It shows the change of main since before the speed work, #507 included, without a
criterion.

**Building both arms identically:**
- Each arm is a `git archive` of its commit with the **same** B9 harness source copied in. The harness is
  `evaluation/speed_b9/b9codec`, committed before the build record, with grizzbit as its only (path) dependency.
- Both are built in one session with the pinned toolchain (`rust-toolchain.toml`, 1.99.0) and
  `cargo build --release --locked --offline`. The release profile is B8's (`lto = "thin"`, `panic = "unwind"`).
  `RUSTFLAGS` is unset, with no `target-cpu`, and the build environment is identical for both arms.
- **Builds:**
  - macOS/arm64: native;
  - Linux/arm64 (also used on the Pi): in `rust:1.99.0-bookworm` (glibc, bookworm, as the Pi's image);
  - Linux/amd64: in `rust:1.99.0-bookworm`, linux/amd64.
- **Recorded:** the image digests, `rustc -vV`, the environment and the SHA-256 of every binary.
- **The harness, `b9codec`, based on B8's `b8codec`:**
  - `b9codec speed 1 FILE` runs one pass, single thread. For each configuration in the fixed order G1, G1+6, G21,
    G21+6, it encodes every chunk and then decodes every chunk, timing each chunk.
  - Every decode is compared with the input by `to_bits`, outside the timed region. A mismatch aborts.
  - Per configuration it prints the encode and decode speed (total samples / total time over all chunks) and
    the SHA-256 of the concatenated chunk bytes.
  - `b9codec hash FILE` prints the four hashes only.
  - The arms run the same harness binary source. Only the grizzbit crate differs.

## 4. Inputs

**Benchmark inputs** (B6's 30 benchmark inputs, built exactly as B6 and B8 built them, 4,096-sample chunks,
quantum 1, every B3 acquisition starting a new chunk):
- **The 12 inputs (H1, H2, H3):**
  - waveforms: `cx`, `ims`, `sleep`, `ecg`, `pad-c-b6`, `pad-p-b6`;
  - B3 channels: `b3-vibration_1`, `b3-phase_current_1`, `b3-phase_current_2` (64 kHz) and `b3-force`,
    `b3-speed`, `b3-torque` (4 kHz), from B3's benchmark set minus the five development bearings, 104
    acquisitions.
- **The 18 Elf series (H3 only; speed reported on the M5 Max without a criterion):** Air-pressure, Air-sensor,
  Basel-temp, Basel-wind, Bird-migration, Bitcoin-price, City-lat, City-lon, City-temp, Dew-point-temp,
  Food-price, IR-bio-temp, PM10-dust, POI-lon, SSD-bench, Stocks-DE, Stocks-UK, Wind-Speed. Air-pressure is
  listed apart in the report, because #394 set out to profile it (0.43× Grizzbit 1 in B6).
- **Development rows (reported apart, in italics, judged by nothing):** B4's four development Elf series
  (Blockchain-tr, POI-lat, Stocks-USA, electric_vehicle_charging). B4's unfiltered Paderborn inputs are byte for
  byte `pad-c-b6` and `pad-p-b6` (B6 results), so they are not measured twice.

**What "held-out" means here.** These inputs were read by B6 (Grizzbit 1 and 2.1 at `50562e8`) and by B8, so
their sizes and their B6 and B8 speeds are published. The published figures were known during #394; they
motivated it. No build of the #394 work has read any of these inputs. According to the commit messages
`cd6a83d..bae89dc` and the development logs, development used only:
- `ka09`: KA09 phase_current_1 of B3's development set, a T1/B6 development input;
- three synthetic inputs: `syn_current`, `syn_resonance` and `apg_wide`.

The build record must state how each synthetic input was generated. If any of them was derived from a benchmark
input, that input is relabelled development: it is reported apart and leaves H1 and H2. For one of the 12 inputs,
that is recorded as a deviation before the run, with the reduced set named. IU4 data (B7, B8) is not used: it is
unpublished and not named by #394.

**How the benchmark inputs are opened, for this confirmation only, after the build record:**
- `prepare_b6.py benchmark WORK` builds them into a fresh directory `b9-work/`.
- Before any measurement, each chunk file's SHA-256 is checked against `chunk_file_sha256` in
  `evaluation/storage_b6/results/b6_inputs.json` (B8's `results/bench/b6_inputs.json` holds the same values).
  One mismatch stops B9 before anything is timed.
- The 12 chunk files are copied to the Pi, and their SHA-256 is checked there. They are deleted from the Pi after
  the run.
- Nothing else reads them.

## 5. Gates (all before any benchmark input is read) and the build record

1. **CI lists everything:** `verification.yml` lists all 20 Kani harnesses. The native jobs (`sdk-wheels.yml`)
   and `core` run the H3(b) tests, the kernel tests with output shown, so the `LPC kernels:` line is in the log.
2. **Tests on the candidate:**
   - `cargo test --locked -p grizzbit -p morlet-core -p morlet-ffi -p morlet-sqlite`;
   - `cargo clippy --locked` on the same crates, `--all-targets -- -D warnings`;
   - `cargo fmt --all -- --check`.

   This runs on macOS/arm64, with the grizzbit test binaries also run under linux/amd64 emulation.
3. **Fuzzing (H4).** On the candidate, the targets are built with cargo-fuzz 0.13.2 and `nightly-2026-10-11`,
   with `-O -a`, and run with the nightly workflow's limits:
   - `-timeout=20 -rss_limit_mb=2048`;
   - `-malloc_limit_mb` 64 (256 for `roundtrip`);
   - `-max_len` 65,536 (300,000 for `roundtrip`).

   Each target runs in its own directory, seeded as the nightly workflow seeds it (earlier corpora may be added).
   CPU time is user + system from `/usr/bin/time -l`, and wall time does not count. Every run, completed or
   interrupted, is listed, and only completed runs with measured CPU time count. Fuzzing never overlaps a timed
   step.
4. **Kani (H4):** all 20 harnesses on the candidate, Kani 0.68.0, with the CBMC version recorded. The time of
   each harness is listed.
5. **Corpus byte test (H3(b)):** a test in `crates/grizzbit/tests/` encodes a deterministic synthetic corpus in
   all four configurations and compares the SHA-256 of each configuration's concatenated bytes with the value
   that `9361aba` writes.
   - **The corpus, generated in the test from a fixed seed, with no file under `testdata/`:**
     - tones, chirps and noise on integer, decimal and offset grids;
     - chunks whose LPC order spans 1–32;
     - wide index spans that take the open-addressing table path, and dense ones;
     - per-sample, table and exception corrections;
     - float32-exact chunks of both version-6 kinds;
     - float chunks.
   - The expected values are computed by running the same test source against a `git archive` of `9361aba`.
   - It gives a native Linux/x86_64 byte comparison in CI beyond the goldens.
6. **Harness test, development data only:** `b9codec` and the runners are run end to end into `results/dev/`, on
   `ka09`, the synthetic inputs and B6's development inputs (`prepare_b6.py dev`). The dry run includes:
   - one synthetic input of 26,744,663 samples on the Pi. Its peak RSS and pass time are recorded, so that the
     B3 64 kHz channels are known to fit in the Pi's 1 GB and in the budget;
   - the Pi sampler (section 6.B), checked to record temperature and `get_throttled` once a second.

   These figures are labelled development.
7. **Byte identity on development data:** the four configurations' hashes on the development inputs are equal
   between the arms, on macOS/arm64 and Linux/arm64.
8. **Build record:** appended to this file in its own commit before the run. It contains:
   - the candidate commit and the baseline commit;
   - every binary's SHA-256 and every image digest;
   - the toolchain;
   - the gate results, fuzz CPU-hours and Kani times;
   - the provenance of the synthetic inputs;
   - the development figures;
   - every decision development made.

   It can fix implementation details. It never changes a criterion, an input list or a rule. If a gate fails,
   nothing is measured.

## 6. Measurements, machines and conditions

**A. Order of the run.**
1. Inputs: build them and verify them (section 4).
2. M5 Max speed (6.C).
3. The reported `c7113c9` arm (6.D).
4. Platform hashes in Docker (H3(a)).
5. The Pi: hashes, tests, then speed (6.B).

Steps 2 and 3 run with nothing else on the Mac. The Pi steps may run while the Mac does step 4 or is idle; they
load only the Pi. The inputs are copied to the Pi before step 2.

**B. Raspberry Pi 3B+ (H1, H2, H3).** This is X2's edge reference box (Raspberry Pi 3 Model B Plus Rev 1.3,
Cortex-A53, 1 GB).
- **Container:** the linux/arm64 (glibc) `b9codec` of each arm runs in `python:3.12.10-slim-bookworm`, pinned at
  `sha256:06001cf262e0684d505218be89ef045e23cc3270af43a127d4ab94a4a78e1344` (B6's Pi image, never pulled during
  the run), with `docker run --rm --network none --cpus 1`.
- **Start condition, checked on the host before every pass:**
  - 1-minute load average below 0.5;
  - SoC temperature below 60 °C (`vcgencmd measure_temp`);
  - `get_throttled` bits 0–3 clear (no under-voltage, frequency cap, throttling or soft temperature limit now);
  - no other container running and no process above 10% CPU.

  The harness waits for the condition, at most 30 minutes per pass.
- **During every pass**, a host-side sampler records temperature, ARM clock and `get_throttled` once a second.
  The sticky bits (16–19) are recorded before and after each pass. A reboot to clear them before the run is
  allowed but not required (in development they read `0x80000` throughout).
- **Snapshots** before and after each block: `uname`, `uptime`, `free -m`, `docker ps`, the CPU governor,
  temperature, clock and `get_throttled`.
- **Blocks:** one per input, in B6's order (`cx`, `ims`, `sleep`, `ecg`, `pad-c-b6`, `pad-p-b6`,
  `b3-vibration_1`, `b3-phase_current_1`, `b3-phase_current_2`, `b3-force`, `b3-speed`, `b3-torque`). Each block
  runs **A-B-B-A-A-B-B-A** (A = baseline, B = candidate), and each run is 3 passes, each its own process. So a
  block has 24 passes and 12 per commit.

**C. Apple M5 Max (H2, H3).** 18 cores, 128 GB, macOS 27, native macOS/arm64 binaries.
- **Start condition**, B6's section 4A (B3.16, amendment B5.S6r), checked before every pass:
  - 1-minute load average below 3;
  - no process other than Docker's using more than half a core;
  - AC power;
  - the lid open, or closed without causing sleep.

  The harness waits at most 30 minutes per pass.
- **No other builds, tests, fuzzing or benchmarks** run on the Mac during steps 2 and 3, by the operator or any
  agent. No containers run then. The processes are not run with `nice` or `taskpolicy`.
- **Snapshots** before and after each block: `uptime`, `pmset -g batt`, `pmset -g therm` and the top processes.
- **Blocks:** the 12 inputs in B6's order, then the 18 Elf series and the four development series (reported
  only). Each block is A-B-B-A-A-B-B-A with 3 passes per run, as on the Pi.

**D. Reported-only arm `c7113c9`, M5 Max only, after 6.C.** One A-B-B-A per input of 6.C, with A = `c7113c9` and
B = the candidate, the same harness and conditions. It reports speed ratios and how many chunks differ per input
and configuration (#507's selection). There is no criterion, and it can change no verdict.

**E. What invalidates a pass, and the redo rule:**
- **Invalid pass:**
  - on the Pi, a 1-second sample at or above 60.0 °C, any of `get_throttled` bits 0–3 set, or a sticky bit that
    appears between the readings before and after the pass;
  - on the Mac, sleep or battery power within the pass (`pmset -g log`), or a build, test, fuzz or benchmark
    process found running;
  - on either machine, a harness error or a pass that did not complete.
- **A block with any invalid pass is void.** It is redone in full, from its first run, at most twice more (three
  attempts). Every attempt is kept in the results with its reason.
- **A block is never redone for its figures.** Looking at a block's ratios is not a reason to redo it.
- **If the start condition is not met within 30 minutes,** the block is set aside and retried once after the
  other blocks. If it still cannot run, or three attempts are void, that input is "not measured". Every
  hypothesis that needs it is then **not demonstrated**.
- **A round-trip mismatch or a hash difference is not an invalid pass.** It stops B9 and is reported under H3.

**F. Recorded per pass:** machine, arm, commit, binary SHA-256, input, pass index, start and end time, the start
condition values and waiting time, and per configuration the encode and decode speed and the chunk-bytes hash.
On the Pi it also records the sampler's maximum temperature, minimum clock and every `get_throttled` value.

## 7. Analysis (fixed now)

For each machine, input, configuration and operation (encode, decode):
1. **Pass speed** = the input's samples / the sum of the per-chunk times (B6 4A), in M samples/s.
2. **Run speed** = the best of its 3 passes (B6 4A: best of 3).
3. **Commit speed** in a block = the best of that commit's 4 run speeds.
4. **Ratio** = candidate commit speed / baseline commit speed, unrounded. H1 and H2 are judged on this ratio
   alone.

**Reported as diagnostics, deciding nothing:**
- the four pair ratios (B1/A1, B2/A2, B3/A3, B4/A4 in the run order A1 B1 B2 A2 A3 B3 B4 A4);
- the ratio of the median run speeds;
- the spread of each commit's run speeds.

A pair ratio on the other side of a bound from the block ratio is listed.

**Aggregate** per machine and configuration over the 12 inputs: total samples / Σ (samples_i / speed_i), the
sample-weighted harmonic mean, as `codec_g21`. It is reported without a criterion.

**Reported against earlier figures, informational only:** for G21+6 and G1+6 on the Mac, against B6's floors
(decode 50, encode 10 M samples/s); on the Pi, against B6's published Pi speeds for the six waveforms (G21, an
older build). These are different sessions, so no ratio is formed with them.

**Tables in `RESULTS.md`:**
- H1: the 12 inputs with baseline, candidate, ratio and holds/fails;
- H2: 192 rows, the ratio against its bound, with every ratio below 1.00 marked;
- H3: hashes per platform, plus the CI runs and their kernel lines;
- H4: fuzz CPU-hours and executions per target, and the Kani list;
- the reported-only rows (Elf series, development series, `c7113c9`);
- the start conditions, every void block, and the deviations.

## 8. On failure, and deviations

- **A failing hypothesis is reported as it is,** with its figures, at the top of `RESULTS.md`. Nothing is dropped,
  and no subset is presented as a pass. H1 and H2 have no "nearly".
- **No tuning on these inputs, and no re-runs looking for a better figure.** The redo rule of 6.E applies only to
  invalid passes.
- **A fix or a new idea after the results** is a new commit judged by a new frozen amendment (B9.1, …), labelled
  "after B9's results were known". Its inputs are no longer held out for that code, and the amendment says so.
- **Deviations:** anything done differently from this protocol is listed in `RESULTS.md` under "Deviations",
  numbered, with when, why and its effect. A deviation that would change a criterion, an input or a statistic is
  not made. The hypothesis it would touch is reported as not demonstrated.
- **H3 and H4's CI parts** stay "pending" until the owner allows the candidate to be pushed and CI has run. The
  verdict is then "pending", not "pass".

## 9. Budget, results and what is not in B9

- **Budget:**
  - inputs and verification: 0.5 hours;
  - M5 Max speed: 2 hours;
  - `c7113c9` arm: 1 hour;
  - Docker platform hashes: 3 hours;
  - the Pi: 8 hours (about 2.5 hours of compute plus cooling waits, from the dry run);
  - fuzzing: 8 CPU-hours, about 2.5 hours of wall time (gate);
  - Kani: 1 hour (gate).

  A step that overruns its budget stops, and its unmeasured inputs are reported as not measured.
- **Results** go in `evaluation/speed_b9/results/`:

  | Path | Contents |
  |---|---|
  | `dev/` | development figures and the dry run |
  | `build.json` | candidate and baseline commits, binaries, images, toolchain |
  | `inputs.json` | input manifest and hash check |
  | `mac_speed.json`, `pi_speed.json`, `c7113c9_speed.json` | section 6, every pass |
  | `pi_sampler/` | the Pi's 1-second samples |
  | `platforms.json` | H3(a) hashes |
  | `fuzz/`, `kani.txt` | the gates |
  | `machine/` | snapshots |
  | `RESULTS.md` | written after the run, beside this file |

- **Not in B9:**
  - **Making Grizzbit 2.1 the default:** out of scope and deferred by the owner. B9 changes no setting and no
    default;
  - any format change, new coding, chunk size or quantum other than 4,096 and 1;
  - x86_64 speed (only its bytes are checked);
  - the B3 server session (ingestion and reads), the SDK and the embedded edition;
  - IU4 data;
  - encode speed floors on the Pi;
  - any website or paper change. Docs get B9's result as measured, after the owner approves.

## 10. Amendments before the run, and the build record

(None yet. Amendments are dated, written before any benchmark input is read, and never change a criterion after
that point. The build record (gate 8) is appended here.)
