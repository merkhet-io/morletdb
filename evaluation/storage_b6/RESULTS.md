# Study B6: Grizzbit 2.1 — results

**UNPUBLISHED: measured 2026-11-11, awaiting the owner's approval.** Nothing here goes to the website, docs or paper
until then (PROTOCOL.md section 6).

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at `ff504b4`; build commit `50562e8` (CI-only commit `62566b8`); build
record `3cedcec` (section 9). Harness committed in `40fe0cd` and `8f871e4` before any benchmark input was read; it was
tested on development inputs and synthetic data only, with a dry run into `results/dev/dryrun/`. The run itself is
`54f8e12`. Every criterion is judged as frozen; nothing above section 8 of the protocol was changed.

## Verdict

**B6 passes. S1–S6 all hold.** By section 5 a release may now offer Grizzbit 2.1 (opt-in, `morlet.chunk_format =
grizzbit21`). Making it the default is a separate, later release decision. Two speed margins are tight, and they
are listed first under "For the owner".

| Criterion | Result | Key figures |
|---|---|---|
| S1 exact | **holds** | every chunk of all 36 inputs (30 benchmark, 6 development) round trips bit for bit; all 36 B3 loads pass B3's full check; 0 read mismatches on D2 r1 for all three arms |
| S2 smaller on waveforms (≤ 0.95× in total, smaller on ≥ 4 of 6) | **holds** | total 24,082,401 / 27,540,505 bytes = **0.8744**; smaller on **6 of 6** (Sleep-EDF 0.690 to ECG 0.996) |
| S3 never larger | **holds** | 30 of 30 inputs G21 ≤ G1; every changed chunk is version 5 and strictly smaller; every other chunk byte-identical to G1's |
| S4 decode ≥ 50, encode ≥ 10 M samples/s | **holds** | 12 of 12 inputs; **lowest decode 53.1** (B3 phase_current_1 and _2), **lowest encode 10.8** (Sleep-EDF) |
| S5 same bytes everywhere | **holds** | macOS/arm64, Linux/arm64, Linux/amd64 (emulated): all 36 inputs; Raspberry Pi: the 6 waveforms; no differing input |
| S6 B3 standing | **holds: (a) and (b)** | (a) every statement the release makes against QuestDB, Grizzbit 2.1 makes in the same direction; (b) no statement that Grizzbit 2.1 is slower than the release, in any read or statistics cell |

## Run

- **Window:** 2026-11-11 19:13:01 – 19:59:30, **46 minutes**, one session, far inside the budgets (storage and codec
  speed 2 h, platform hashes 3 h, B3 session 3 h; section 7):
  - setup and input preparation 19:13:01–19:13:05;
  - storage 19:13:05–19:14:12 (measured 19:13:36–19:14:12);
  - codec speed 19:14:12–19:15:14;
  - B3 session 19:15:14–19:56:39 (36 loads);
  - platform hashes 19:56:39–19:57:23;
  - Raspberry Pi 19:57:23–19:59:30.
- **Start condition** (B3.16's, enforced before every timed pass and every load, amendment B5.S6r):
  - 1-minute load average 4.00 at 19:13:01, before setup (snapshot in `results/machine/run.txt`);
  - codec speed: 90 passes, load at the pass 2.13–2.90, longest wait 5 s, condition met every time;
  - B3 session: 36 of 36 loads started with the condition met, load at the start 2.69–2.98 (median 2.89), median wait
    41.5 s, longest 129 s; snapshots after the loads 2.88–7.67 (median 4.0), per load in `results/b3-*/<arm>/machine/`;
  - AC power; no other builds, tests or benchmarks on the Mac during the run.
- **Arms and artefacts** (`results/build.json`, `results/b6_programs.json`):
  - `morletdb:b6` `sha256:99ad124d…`, built with deploy/Dockerfile from a git archive of `50562e8`;
  - release `ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1` `sha256:03ed3cc2…`;
  - QuestDB 10.1.0 `sha256:3b11dc5f…`;
  - decoders: B6 `libmorlet_ffi.dylib` sha256 `4cd33b93…` (max chunk version 5); release decoder sha256 `343a8d9f…`;
  - codec programs: `rawcodec_bench` `574314a6…` (G1 arm, as in B4 and B5); `b6codec` `31ac6d8a…` (macOS build; the
    Linux builds are `b773dc8d…` on arm64, also used on the Pi, and `92629626…` on amd64);
  - platform images: `rust:1.99.0-bookworm`, arm64 config `sha256:e393e07b…` and amd64 config `sha256:b36c2467…`
    (one index, `rust@sha256:114c7a44…`); Pi `python:3.12.10-slim-bookworm` `sha256:06001cf2…`.
- **Void loads and failed attempts:** none. No load was repeated, and no step stopped.

## Inputs

- **30 benchmark inputs** (section 3): the six waveforms, B4's 18 Elf benchmark series, and the six B3 machine channels.
- **6 development inputs**, reported apart and in italics in every table, judged by no criterion: B4's four
  development Elf series (Blockchain-tr, POI-lat, Stocks-USA, electric_vehicle_charging) and B4's two unfiltered
  Paderborn waveforms.
- **G1 reproduces B4 and B5:** on all 28 inputs those studies measured, G1's bytes equal B4's and B5's
  (`G1_matches_references` in `results/b6_summary.json`), so the G1 arm is the release codec measured before.

### Paderborn: the B6 inputs equal B4's

Section 8 found that B1's Paderborn held-out set includes three of the five bearings Grizzbit 2.1 was developed on,
and the owner decided that every file of the five development bearings is excluded and the two Paderborn waveforms are
rebuilt without them. The rebuild uses B4's construction (`p2_dump` `first_chunks`, 1,024 chunks of 4,096 samples over
`paderborn("heldout")`, development bearings skipped), as the owner confirmed.

The rebuilt waveforms are **byte for byte B4's**: Paderborn counts sha256 `e7e1bad4…` and Paderborn physical
`77fb932c…`, equal to B4's `pad-c` and `pad-p`, chunk files included; G1 is 4,132,933 and 4,806,153 bytes, B4's M-auto
figures. The reason is in `results/b6_inputs.json`: B4's 1,024-chunk window is filled before the development bearings
are reached. The window uses 17 files of K003, K004 and K005 only:

- K003: `N09_M07_F10_K003_1`, `_2`; `N15_M01_F10_K003_3`, `_4`; `N15_M07_F04_K003_1`, `_3`; `N15_M07_F10_K003_1`, `_2`;
- K004: `N09_M07_F10_K004_1`, `_3`; `N15_M01_F10_K004_1`, `_2`; `N15_M07_F04_K004_1`, `_2`; `N15_M07_F10_K004_1`, `_2`;
- K005: `N09_M07_F10_K005_1`;

and `files_skipped_development_bearings` is empty. **So section 8's overlap did not affect B4's Paderborn inputs, nor
B5's** (B5's G1 bytes for them match too). It does affect any longer Paderborn construction, which is why the exclusion stays.
B4's copies appear below as development rows with identical figures.

### B3 channels

From B3's benchmark set of 124 acquisitions, 104 are kept and 20 excluded (the four `_1.mat` files of each of K001,
KA01, KA09, KB24 and KI08, the development bearings). vibration_1 and the two phase currents have 26,744,663 samples
(6,579 chunks) each; force, speed and torque 1,664,202 (416 chunks).

## Storage

Bits per sample, chunk size 4,096. G21/G1 is the byte ratio. "Changed" counts chunks where Grizzbit 2.1 stored a
version-5 chunk instead of G1's. CH best, Pcodec and G2 are B4's and B5's published figures, for reference only.

| Input | Samples | Chunks | G1 | G21 | G21/G1 | Changed | CH best (arm) | Pcodec | G2 (B5) | Criterion |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| CX counts | 4,194,304 | 1,024 | 4.61 | 4.41 | 0.9565 | 995 | 5.33 (CH-PCO) | 5.23 | 4.61 | S2, S3 |
| IMS decimals | 4,194,304 | 1,024 | 8.81 | 8.39 | 0.9521 | 1,024 | 9.19 (CH-PCO) | 8.12 | 8.81 | S2, S3 |
| Sleep-EDF | 4,194,304 | 1,024 | 14.66 | 10.11 | 0.6900 | 1,024 | 18.33 (CH-PCO) | 20.27 | 14.62 | S2, S3 |
| ECG | 4,194,000 | 4,194 | 7.40 | 7.37 | 0.9960 | 865 | 8.08 (CH-PCO) | 7.84 | 7.40 | S2, S3 |
| Paderborn counts (B6) | 4,194,304 | 1,024 | 7.88 | 7.75 | 0.9827 | 980 | — | — | — | S2, S3 |
| Paderborn physical (B6) | 4,194,304 | 1,024 | 9.17 | 7.90 | 0.8622 | 1,024 | — | — | — | S2, S3 |
| Air-pressure | 95,928 | 24 | 11.45 | 10.95 | 0.9569 | 24 | 10.77 (CH-PCO) | 8.73 | 10.15 | S3 |
| Air-sensor | 8,664 | 3 | 46.10 | 46.10 | 1.0000 | 0 | 46.63 (CH-PCO) | 46.57 | 45.92 | S3 |
| Basel-temp | 100,001 | 25 | 52.38 | 52.38 | 1.0000 | 0 | 21.07 (CH-zstd9) | 15.27 | 32.89 | S3 |
| Basel-wind | 100,000 | 25 | 53.45 | 53.45 | 1.0000 | 0 | 21.79 (CH-PCO) | 14.29 | 30.03 | S3 |
| Bird-migration | 89,867 | 22 | 45.90 | 45.90 | 1.0000 | 0 | 16.99 (CH-PCO) | 15.05 | 17.74 | S3 |
| Bitcoin-price | 7,116 | 2 | 46.11 | 46.11 | 1.0000 | 0 | 28.80 (CH-ALP-zstd9) | 46.20 | 46.11 | S3 |
| City-lat | 41,001 | 11 | 20.46 | 20.06 | 0.9803 | 10 | 20.63 (CH-PCO) | 20.54 | 20.43 | S3 |
| City-lon | 41,001 | 11 | 21.65 | 21.65 | 1.0000 | 0 | 22.26 (CH-PCO) | 22.14 | 21.62 | S3 |
| City-temp | 100,001 | 25 | 7.50 | 7.49 | 0.9991 | 1 | 7.73 (CH-PCO) | 7.72 | 7.20 | S3 |
| Dew-point-temp | 100,001 | 25 | 8.19 | 8.14 | 0.9930 | 22 | 9.09 (CH-PCO) | 8.98 | 8.19 | S3 |
| Food-price | 100,001 | 25 | 32.14 | 32.14 | 1.0000 | 0 | 14.75 (CH-ALP-zstd9) | 11.84 | 11.30 | S3 |
| IR-bio-temp | 99,602 | 25 | 3.60 | 3.54 | 0.9815 | 15 | 4.41 (CH-PCO) | 4.29 | 3.60 | S3 |
| PM10-dust | 93,662 | 23 | 3.99 | 3.99 | 1.0000 | 0 | 4.28 (CH-PCO) | 4.45 | 3.82 | S3 |
| POI-lon | 100,001 | 25 | 64.02 | 64.02 | 1.0000 | 0 | 54.81 (CH-PCO) | 54.55 | 54.93 | S3 |
| SSD-bench | 8,927 | 3 | 31.40 | 31.40 | 1.0000 | 0 | 7.88 (CH-PCO) | 7.59 | 7.92 | S3 |
| Stocks-DE | 100,002 | 25 | 5.52 | 5.52 | 1.0000 | 0 | 6.14 (CH-PCO) | 6.58 | 5.36 | S3 |
| Stocks-UK | 100,002 | 25 | 5.30 | 5.30 | 1.0000 | 0 | 5.96 (CH-PCO) | 8.58 | 5.26 | S3 |
| Wind-Speed | 99,132 | 25 | 5.08 | 4.88 | 0.9603 | 25 | 5.85 (CH-PCO) | 5.40 | 5.08 | S3 |
| B3 vibration_1 | 26,744,663 | 6,579 | 9.27 | 7.82 | 0.8432 | 6,578 | — | — | — | S3 |
| B3 phase_current_1 | 26,744,663 | 6,579 | 10.56 | 8.63 | 0.8173 | 6,578 | — | — | — | S3 |
| B3 phase_current_2 | 26,744,663 | 6,579 | 10.74 | 8.61 | 0.8013 | 6,578 | — | — | — | S3 |
| B3 force | 1,664,202 | 416 | 8.66 | 7.45 | 0.8600 | 416 | — | — | — | S3 |
| B3 speed | 1,664,202 | 416 | 9.19 | 9.19 | 1.0000 | 0 | — | — | — | S3 |
| B3 torque | 1,664,202 | 416 | 48.90 | 48.90 | 1.0000 | 0 | — | — | — | S3 |
| *Blockchain-tr (dev)* | 99,999 | 25 | 13.96 | 13.96 | 1.0000 | 0 | 13.16 (CH-PCO) | 12.37 | 13.26 | none |
| *POI-lat (dev)* | 100,001 | 25 | 60.84 | 60.84 | 1.0000 | 0 | 53.51 (CH-PCO) | 53.30 | 53.58 | none |
| *Stocks-USA (dev)* | 100,002 | 25 | 4.56 | 4.56 | 1.0000 | 0 | 5.20 (CH-PCO) | 5.23 | 4.56 | none |
| *electric_vehicle_charging (dev)* | 3,395 | 1 | 10.03 | 9.80 | 0.9770 | 1 | 10.46 (CH-PCO) | 10.39 | 10.03 | none |
| *Paderborn counts (B4) (dev)* | 4,194,304 | 1,024 | 7.88 | 7.75 | 0.9827 | 980 | 8.47 (CH-PCO) | 8.31 | 7.88 | none |
| *Paderborn physical (B4) (dev)* | 4,194,304 | 1,024 | 9.17 | 7.90 | 0.8622 | 1,024 | 8.92 (CH-PCO) | 8.68 | 9.03 | none |

### What the table says

- **Waveforms (S2):** all six get smaller. Sleep-EDF gains most (14.66 → 10.11, 0.690), then Paderborn physical
  (0.862); CX counts, IMS and Paderborn counts gain 2–5%; ECG barely moves (0.996, 865 of 4,194 chunks changed).
  Grizzbit 2.1 is now below B4's best ClickHouse arm and below Pcodec on five of the six waveforms (for Paderborn, B4's
  figures are on the identical development rows); on IMS Pcodec stays ahead (8.12 against 8.39).
- **B3 channels:** the three 64 kHz channels lose 15–20% (vibration_1 0.843, phase currents 0.817 and 0.801), force
  0.860; speed and torque (float chunks) are unchanged.
- **Elf series:** Grizzbit 2.1 helps only where G1 already stores a grid (Air-pressure 0.957, Wind-Speed 0.960, City-lat
  0.980, IR-bio-temp 0.982, Dew-point-temp 0.993, City-temp 0.999). The float-heavy series (Basel-temp, Basel-wind,
  Bird-migration, Bitcoin-price, Food-price, POI-lon, SSD-bench, Air-sensor) are byte-identical to G1: B5's large losses
  against ClickHouse and Pcodec remain, and B6 did not aim at them.
- **S3:** no input grows, and no chunk is replaced by a larger one.

### Changed chunks and version-5 paths

| Input | Changed / chunks | G1 kinds | G21 kinds | Version-5 paths (table / exceptions only / none; LPC) |
|---|---:|---|---|---|
| CX counts | 995 / 1,024 | 1024 v2 grid | 29 v2 grid, 995 v5 grid | 0 / 0 / 995; LPC 995 |
| IMS decimals | 1,024 / 1,024 | 1024 v2 grid | 1024 v5 grid | 0 / 0 / 1024; LPC 1024 |
| Sleep-EDF | 1,024 / 1,024 | 32 v2 grid, 992 v3 grid | 1024 v5 grid | 1024 / 0 / 0; LPC 993 |
| ECG | 865 / 4,194 | 4194 v2 grid | 3329 v2 grid, 865 v5 grid | 0 / 0 / 865; LPC 865 |
| Paderborn counts (B6) | 980 / 1,024 | 1024 v2 grid | 44 v2 grid, 980 v5 grid | 0 / 0 / 980; LPC 980 |
| Paderborn physical (B6) | 1,024 / 1,024 | 1024 v2 grid | 1024 v5 grid | 1024 / 0 / 0; LPC 1010 |
| Air-pressure | 24 / 24 | 24 v3 grid | 24 v5 grid | 17 / 7 / 0; LPC 15 |
| Air-sensor | 0 / 3 | 3 v2 float | 3 v2 float | — |
| Basel-temp | 0 / 25 | 25 v2 float | 25 v2 float | — |
| Basel-wind | 0 / 25 | 25 v2 float | 25 v2 float | — |
| Bird-migration | 0 / 22 | 22 v2 float | 22 v2 float | — |
| Bitcoin-price | 0 / 2 | 2 v2 float | 2 v2 float | — |
| City-lat | 10 / 11 | 1 v2 float, 10 v2 grid | 1 v2 float, 10 v5 grid | 0 / 0 / 10; LPC 10 |
| City-lon | 0 / 11 | 1 v2 float, 10 v2 grid | 1 v2 float, 10 v2 grid | — |
| City-temp | 1 / 25 | 25 v2 grid | 24 v2 grid, 1 v5 grid | 0 / 0 / 1; LPC 1 |
| Dew-point-temp | 22 / 25 | 25 v2 grid | 3 v2 grid, 22 v5 grid | 0 / 0 / 22; LPC 22 |
| Food-price | 0 / 25 | 13 v2 float, 12 v2 grid | 13 v2 float, 12 v2 grid | — |
| IR-bio-temp | 15 / 25 | 25 v2 grid | 10 v2 grid, 15 v5 grid | 0 / 13 / 2; LPC 4 |
| PM10-dust | 0 / 23 | 23 v2 grid | 23 v2 grid | — |
| POI-lon | 0 / 25 | 25 v2 raw | 25 v2 raw | — |
| SSD-bench | 0 / 3 | 1 v2 float, 2 v2 grid | 1 v2 float, 2 v2 grid | — |
| Stocks-DE | 0 / 25 | 25 v2 grid | 25 v2 grid | — |
| Stocks-UK | 0 / 25 | 25 v2 grid | 25 v2 grid | — |
| Wind-Speed | 25 / 25 | 25 v2 grid | 25 v5 grid | 0 / 0 / 25; LPC 25 |
| B3 vibration_1 | 6,578 / 6,579 | 6579 v2 grid | 1 v2 grid, 6578 v5 grid | 6578 / 0 / 0; LPC 6172 |
| B3 phase_current_1 | 6,578 / 6,579 | 6579 v2 grid | 1 v2 grid, 6578 v5 grid | 6578 / 0 / 0; LPC 6578 |
| B3 phase_current_2 | 6,578 / 6,579 | 6579 v2 grid | 1 v2 grid, 6578 v5 grid | 6578 / 0 / 0; LPC 6578 |
| B3 force | 416 / 416 | 416 v3 grid | 416 v5 grid | 415 / 1 / 0; LPC 127 |
| B3 speed | 0 / 416 | 416 v2 float | 416 v2 float | — |
| B3 torque | 0 / 416 | 416 v2 float | 416 v2 float | — |
| *Blockchain-tr (dev)* | 0 / 25 | 25 v2 grid | 25 v2 grid | — |
| *POI-lat (dev)* | 0 / 25 | 25 v2 float | 25 v2 float | — |
| *Stocks-USA (dev)* | 0 / 25 | 25 v2 grid | 25 v2 grid | — |
| *electric_vehicle_charging (dev)* | 1 / 1 | 1 v2 grid | 1 v5 grid | 0 / 0 / 1; LPC 1 |
| *Paderborn counts (B4) (dev)* | 980 / 1,024 | 1024 v2 grid | 44 v2 grid, 980 v5 grid | 0 / 0 / 980; LPC 980 |
| *Paderborn physical (B4) (dev)* | 1,024 / 1,024 | 1024 v2 grid | 1024 v5 grid | 1024 / 0 / 0; LPC 1010 |

The version-5 path is how the chunk stores the prediction residuals: a corrections table, exceptions only, or none;
"LPC" counts chunks whose predictor is the LPC kernel rather than a fixed one. No chunk used per-sample corrections.

## Codec speed

Single thread, M samples/s, best of three passes, Grizzbit 1 (`rawcodec_bench`) against Grizzbit 2.1 (`b6codec`).
The last column but one gives the 1-minute load average at the start condition before each pass. S4's figures are
the waveforms and the B3 channels; the Elf rows are reported only.

| Input | G1 encode | G21 encode | G21/G1 | G1 decode | G21 decode | G21/G1 | Load at the 3 passes | Criterion |
|---|---:|---:|---:|---:|---:|---:|---|---|
| CX counts | 64.9 | 34.5 | 0.53 | 122.0 | 107.0 | 0.88 | 2.89, 2.89, 2.89 | S4 |
| IMS decimals | 38.7 | 25.1 | 0.65 | 158.9 | 100.5 | 0.63 | 2.89, 2.66, 2.66 | S4 |
| Sleep-EDF | 17.4 | **10.8** | 0.62 | 71.1 | 71.6 | 1.01 | 2.66, 2.53, 2.53 | S4 |
| ECG | 19.9 | 15.0 | 0.75 | 116.5 | 116.2 | 1.00 | 2.53, 2.53, 2.53 | S4 |
| Paderborn counts (B6) | 62.4 | 35.1 | 0.56 | 144.4 | 110.0 | 0.76 | 2.53, 2.53, 2.53 | S4 |
| Paderborn physical (B6) | 24.0 | 14.8 | 0.61 | 84.0 | 82.0 | 0.98 | 2.53, 2.48, 2.48 | S4 |
| Air-pressure | 16.2 | 8.8 | 0.55 | 76.1 | 32.8 | 0.43 | 2.48, 2.48, 2.48 | none |
| Air-sensor | 21.4 | 26.4 | 1.24 | 93.8 | 98.8 | 1.05 | 2.48, 2.48, 2.48 | none |
| Basel-temp | 21.8 | 22.8 | 1.04 | 78.2 | 78.5 | 1.00 | 2.48, 2.48, 2.48 | none |
| Basel-wind | 23.8 | 25.0 | 1.05 | 81.0 | 82.0 | 1.01 | 2.48, 2.48, 2.48 | none |
| Bird-migration | 25.6 | 26.7 | 1.04 | 87.0 | 89.0 | 1.02 | 2.48, 2.48, 2.48 | none |
| Bitcoin-price | 20.7 | 26.3 | 1.27 | 89.1 | 90.4 | 1.01 | 2.48, 2.48, 2.48 | none |
| City-lat | 26.5 | 19.9 | 0.75 | 110.1 | 105.2 | 0.96 | 2.48, 2.48, 2.48 | none |
| City-lon | 26.2 | 21.7 | 0.83 | 159.7 | 162.2 | 1.02 | 2.48, 2.48, 2.48 | none |
| City-temp | 32.0 | 25.1 | 0.78 | 120.7 | 121.5 | 1.01 | 2.48, 2.48, 2.48 | none |
| Dew-point-temp | 26.6 | 20.7 | 0.78 | 121.6 | 116.4 | 0.96 | 2.48, 2.48, 2.48 | none |
| Food-price | 24.5 | 23.0 | 0.94 | 96.0 | 94.9 | 0.99 | 2.48, 2.48, 2.48 | none |
| IR-bio-temp | 24.9 | 19.7 | 0.79 | 104.3 | 121.0 | 1.16 | 2.48, 2.48, 2.48 | none |
| PM10-dust | 37.5 | 28.6 | 0.76 | 123.7 | 124.3 | 1.00 | 2.48, 2.48, 2.48 | none |
| POI-lon | 23.0 | 23.8 | 1.04 | 190.9 | 193.7 | 1.01 | 2.48, 2.48, 2.48 | none |
| SSD-bench | 24.4 | 24.3 | 1.00 | 89.2 | 95.0 | 1.07 | 2.36, 2.36, 2.36 | none |
| Stocks-DE | 30.7 | 24.1 | 0.79 | 119.5 | 120.3 | 1.01 | 2.36, 2.36, 2.36 | none |
| Stocks-UK | 30.6 | 24.0 | 0.78 | 117.8 | 121.7 | 1.03 | 2.36, 2.36, 2.36 | none |
| Wind-Speed | 43.2 | 29.1 | 0.67 | 124.9 | 123.2 | 0.99 | 2.36, 2.36, 2.36 | none |
| B3 vibration_1 | 23.0 | 13.8 | 0.60 | 77.7 | 74.8 | 0.96 | 2.36, 2.36, 2.33 | S4 |
| B3 phase_current_1 | 19.9 | **11.7** | 0.58 | 77.1 | **53.1** | 0.69 | 2.23, 2.13, 2.36 | S4 |
| B3 phase_current_2 | 19.9 | **11.7** | 0.59 | 77.2 | **53.1** | 0.69 | 2.33, 2.54, 2.90 | S4 |
| B3 force | 18.5 | 12.3 | 0.67 | 72.1 | 81.1 | 1.12 | 2.75, 2.75, 2.75 | S4 |
| B3 speed | 48.8 | 49.5 | 1.01 | 104.3 | 104.4 | 1.00 | 2.75, 2.75, 2.75 | S4 |
| B3 torque | 25.4 | 25.7 | 1.01 | 87.0 | 86.9 | 1.00 | 2.75, 2.75, 2.75 | S4 |

- **Margins (S4):** decode is lowest on the two B3 phase currents, **53.1 M samples/s against the floor of 50**
  (6%), where G1 decodes 77; encode is lowest on Sleep-EDF, **10.8 against 10** (8%), with B3 phase currents at 11.7
  and B3 force at 12.3. The criterion holds, but on a slower machine or a busier one these two figures could fall
  below the floors. In both inputs every chunk uses the corrections table.
- Encode costs 21–47% on the inputs where chunks change (ratios 0.53–0.79). On the S4 inputs decode costs up to 37%
  (IMS 0.63, phase currents 0.69); on Sleep-EDF, ECG, Paderborn physical and B3 force it is about as fast or faster.
- Air-pressure, an Elf series judged by no speed criterion, decodes at 32.8 M samples/s (0.43× G1): 24 chunks, 17 with
  the corrections table and 7 with exceptions only.

## Platforms (S5)

SHA-256 of each input's concatenated Grizzbit 2.1 chunks, written by `b6codec` on each platform
(`results/b6_platforms.json`, `results/platforms/`). macOS/arm64 is native; Linux/arm64 and Linux/amd64 run in
`rust:1.99.0-bookworm` containers, amd64 under emulation; the Pi (Raspberry Pi 3 Model B Plus Rev 1.3, Linux
6.1.21-v8+ aarch64, load 0.12) runs the Linux/arm64 `b6codec` in `python:3.12.10-slim-bookworm` on the six waveforms.

| Input | SHA-256 of the concatenated G21 chunk bytes | Bytes | Platforms agreeing |
|---|---|---:|---|
| CX counts | `fb7d922605a80600fcb00de15d075868ae933bef200162b0301e5bd72e250b50` | 2,312,039 | macos-arm64, linux-arm64, linux-amd64, pi-arm64 |
| IMS decimals | `4afac68415e88492302d2e911117cb3ad1b31d4bb694e85eaa824503437d1f7b` | 4,397,296 | macos-arm64, linux-arm64, linux-amd64, pi-arm64 |
| Sleep-EDF | `d76de6ed78ef9ea8bea05a7c925fba85d8c5cde0139c65e1de281c366d0fb1ca` | 5,302,788 | macos-arm64, linux-arm64, linux-amd64, pi-arm64 |
| ECG | `d131910dad780da8ba9dc54b352c45931c634043488ee2015957c01d031c76a0` | 3,865,048 | macos-arm64, linux-arm64, linux-amd64, pi-arm64 |
| Paderborn counts (B6) | `661a61cdee3585822ca07fc1dfb3748e7f8e87166ed71cc73fc82c3f9e4ae901` | 4,061,566 | macos-arm64, linux-arm64, linux-amd64, pi-arm64 |
| Paderborn physical (B6) | `7395080d9819b236fdb3f900ba6db8dec23692b12acfec97529ea5bfa1466f86` | 4,143,664 | macos-arm64, linux-arm64, linux-amd64, pi-arm64 |
| Air-pressure | `ae0f843643bda61c1f535e6439a60dea49ea6bde5cd0b74f646c439e451f35ab` | 131,338 | macos-arm64, linux-arm64, linux-amd64 |
| Air-sensor | `d139d02ebc67f2bd42480639ed07f56cca6d5746241073ac05d7ef6a49de5c01` | 49,926 | macos-arm64, linux-arm64, linux-amd64 |
| Basel-temp | `a45b0092bb42169d4f929e0e5982303ab65624f2cbdbeb76e44b20baa7c7eb3c` | 654,705 | macos-arm64, linux-arm64, linux-amd64 |
| Basel-wind | `d5e77209a50b12041947dfbb19c4548cae05af5be7fcd4d6926101f9986dc7ef` | 668,101 | macos-arm64, linux-arm64, linux-amd64 |
| Bird-migration | `60e81d9918ceef4cf14f2459420342f0c0862dbc8f795d1ee3cf42bd7770b2e4` | 515,656 | macos-arm64, linux-arm64, linux-amd64 |
| Bitcoin-price | `8b0658db1baeeab6b33c55923c850dea95795adc765d3275fc7ab78c1cdefec9` | 41,013 | macos-arm64, linux-arm64, linux-amd64 |
| City-lat | `41f134e9dcad620f587e88d271698511e8496dc1ad509ff2ae0accf87a76bf2f` | 102,801 | macos-arm64, linux-arm64, linux-amd64 |
| City-lon | `10c5ebd7b6c987501f52d6847c794ca5b5929d3521d6ec90dbe82e7ede19d4fa` | 110,959 | macos-arm64, linux-arm64, linux-amd64 |
| City-temp | `311fcd2cbd2589ca75f36dd2ff78a4aa5b67e09d8935c4aa6936645ae28e283c` | 93,641 | macos-arm64, linux-arm64, linux-amd64 |
| Dew-point-temp | `46497251cd2a31450516be7eb7ac5ce7ebccdc334fc30e9213db97eeb8215793` | 101,697 | macos-arm64, linux-arm64, linux-amd64 |
| Food-price | `7f510036b1627d7875c0e83a518e9dd26cc1c804813c12973202187678d0958b` | 401,801 | macos-arm64, linux-arm64, linux-amd64 |
| IR-bio-temp | `cd8b998dcb21afb72a3d2fb7685bbe9eff65909dd3bed966663ef43ef52b8b09` | 44,014 | macos-arm64, linux-arm64, linux-amd64 |
| PM10-dust | `f77b055cf26a3e621b2c4bc4d7ecab74259f87e95d844e4ba3337aceda276f71` | 46,666 | macos-arm64, linux-arm64, linux-amd64 |
| POI-lon | `78446f20ec6efb81eaae99ec21b229c4a2296cc01b55d9023462fa3a973f93f0` | 800,208 | macos-arm64, linux-arm64, linux-amd64 |
| SSD-bench | `4e83298ed99f54dd628c254ce2c18ead00235f06cf56ebdcfbf70872e6e3c373` | 35,037 | macos-arm64, linux-arm64, linux-amd64 |
| Stocks-DE | `80f05af9607244815fc3fce435790d17b13fd1f23819ddee3cdaad23819672b5` | 69,000 | macos-arm64, linux-arm64, linux-amd64 |
| Stocks-UK | `e876d7d52690a41edaa5e23c0060c787fcae50368dcb62b5844f2282aedb16d2` | 66,227 | macos-arm64, linux-arm64, linux-amd64 |
| Wind-Speed | `eeec937b108cd50edc7cd416fb92123c096da8c9809cf228e66d434badfc7b48` | 60,454 | macos-arm64, linux-arm64, linux-amd64 |
| B3 vibration_1 | `abef49aed85448cb375301527d209f862f4208ab7e4b1429be6cc0925e6492ca` | 26,130,601 | macos-arm64, linux-arm64, linux-amd64 |
| B3 phase_current_1 | `ceef2b2aa559d2fa74b5fc8cad234f67c12490de2b0c7083b0c09a76e0cfdab4` | 28,849,675 | macos-arm64, linux-arm64, linux-amd64 |
| B3 phase_current_2 | `be1579b020717d17464898b72265f8ae7f519ebf4d26112f01997adff4095f72` | 28,778,275 | macos-arm64, linux-arm64, linux-amd64 |
| B3 force | `e0626a64f7165cfda9c3152369c96f329412591241622d5022aa4031e0bb1a08` | 1,549,569 | macos-arm64, linux-arm64, linux-amd64 |
| B3 speed | `867429b36a87af5db40a3d05747251b9d933c1f873924fc6b48c11df92b0dd04` | 1,911,638 | macos-arm64, linux-arm64, linux-amd64 |
| B3 torque | `d31d2bd7bf997f5184e1533b5440562679274d2a3eb6eee7b439b2e8d7044acf` | 10,171,885 | macos-arm64, linux-arm64, linux-amd64 |
| *Blockchain-tr (dev)* | `ba94245f47edab4abe511e2e0fb42bfbc9d31bf7972dec0d15d864473c3356ad` | 174,505 | macos-arm64, linux-arm64, linux-amd64 |
| *POI-lat (dev)* | `74c2d3424ffb906836d333fa1da1327e1bbe51e0367dfd5c466e70dc41f278bb` | 760,516 | macos-arm64, linux-arm64, linux-amd64 |
| *Stocks-USA (dev)* | `be6e9b7812ceb6f7cb92b940d6019ba4939ddb74058ef7c78432d97454b4712a` | 57,027 | macos-arm64, linux-arm64, linux-amd64 |
| *electric_vehicle_charging (dev)* | `0c9d65d1f4f526bf7e9c51e40bbb953c4954daaf09b1e5ab63944c28d72ddeb3` | 4,159 | macos-arm64, linux-arm64, linux-amd64 |
| *Paderborn counts (B4) (dev)* | `661a61cdee3585822ca07fc1dfb3748e7f8e87166ed71cc73fc82c3f9e4ae901` | 4,061,566 | macos-arm64, linux-arm64, linux-amd64 |
| *Paderborn physical (B4) (dev)* | `7395080d9819b236fdb3f900ba6db8dec23692b12acfec97529ea5bfa1466f86` | 4,143,664 | macos-arm64, linux-arm64, linux-amd64 |

No input differs between platforms. The two Paderborn hashes equal their B4 development copies, as the inputs do.

Pi speed, for reference (S4 does not judge edge hardware; section 8), M samples/s:

| Waveform | Pi encode | Pi decode |
|---|---:|---:|
| CX counts | 2.02 | 7.50 |
| IMS decimals | 1.68 | 6.79 |
| Sleep-EDF | 0.97 | 8.29 |
| ECG | 1.27 | 12.61 |
| Paderborn counts (B6) | 2.14 | 9.37 |
| Paderborn physical (B6) | 1.19 | 7.62 |

## B3 session (S6)

Same harness as B5's re-run session (B5.S6r): three arms, track S (one writer) and track P (8 writers), datasets D2
and D1, three loads each, interleaved. Ingestion: ratio of medians (lower confidence bound). Reads: p50 / p95 ratio
of the slower arm's latency to the faster's, warm cache. A statement needs a 10% difference (B3's rule).

| Measure | Release vs QuestDB | Grizzbit 2.1 vs QuestDB | Grizzbit 2.1 vs release |
|---|---|---|---|
| Ingestion S D2 | **faster** 1.463 (1.444) | **faster** 1.391 (1.365) | no statement (0.950) |
| Ingestion S D1 | **faster** 1.599 (1.567) | **faster** 1.538 (1.396) | no statement (0.962) |
| Ingestion P D2 | **faster** 1.603 (1.593) | **faster** 1.550 (1.520) | no statement (0.967) |
| Ingestion P D1 | **faster** 1.899 (1.773) | **faster** 1.764 (1.645) | no statement (0.929) |
| R1 | **faster** 1.203 / 1.482 | **faster** 1.197 / 1.441 | no statement (0.995 / 0.973) |
| R2 | **faster** 2.876 / 3.236 | **faster** 2.859 / 3.274 | no statement (0.994 / 1.012) |
| R3 | **faster** 3.169 / 3.890 | **faster** 3.224 / 3.967 | no statement (1.017 / 1.020) |
| R4 recent | no statement (0.998 / 1.449) | no statement (0.987 / 1.450) | no statement (0.989 / 1.001) |
| R4 older | no statement (1.017 / 1.448) | no statement (1.000 / 1.418) | no statement (0.984 / 0.979) |
| R5 (queries/s) | **faster** 1.527 | **faster** 1.508 | no statement (0.988) |
| Interval statistics | **faster** 5.513 / 9.663 | **faster** 5.465 / 8.905 | no statement (0.991 / 0.921) |

- **S6 (a) holds:** every statement of the release against QuestDB (four ingestion cells, R1, R2, R3, R5, statistics)
  is also made by Grizzbit 2.1, in the same direction; R4 makes none for either arm.
- **S6 (b) holds:** no read or statistics cell says Grizzbit 2.1 is slower than the release.
- **Ingestion against the release:** Grizzbit 2.1 is about 5% slower (ratios of medians 0.929–0.967), under B3's 10%
  statement threshold in every cell, so there is **no statement and no cost note** for the docs (section 5 asks for one
  only at 10% or more in all three loads). It uses more server CPU than the release (below), which is where the slower
  encode shows.

Read latencies on D2 r1, ms, p50 / p95:

| Read | Grizzbit 2.1 | Release | QuestDB |
|---|---|---|---|
| R1 | 0.620 / 1.061 | 0.617 / 1.032 | 0.742 / 1.529 |
| R2 | 2.117 / 2.844 | 2.105 / 2.877 | 6.053 / 9.310 |
| R3 | 2.820 / 3.519 | 2.869 / 3.589 | 9.093 / 13.960 |
| R4 recent | 0.637 / 0.959 | 0.630 / 0.960 | 0.629 / 1.391 |
| R4 older | 0.612 / 0.968 | 0.602 / 0.948 | 0.612 / 1.373 |
| R5 (queries/s) | 4,152.0 | 4,202.5 | 2,752.6 |
| Interval statistics | 0.460 / 0.535 | 0.456 / 0.493 | 2.514 / 4.764 |

R2 and R3 use the encoded transfer: the adapter passes each arm's decoder and its `max_chunk_version` to
`morlet_read_interval_encoded`, so the Grizzbit 2.1 arm decodes version-5 chunks on the client with the B6 decoder
(`4cd33b93…`), and the release arm uses the release decoder.

Ingestion per load, M samples/s, with server CPU (mean %) and peak memory (MiB), loads r1, r2, r3:

| Cell | Grizzbit 2.1 | Release | QuestDB |
|---|---|---|---|
| S D2 | 13.50, 13.44, 13.23 — CPU 127, 123, 133; peak 175, 176, 172 | 14.01, 14.16, 14.14 — CPU 122, 122, 124; peak 178, 178, 178 | 9.59, 9.67, 9.70 — CPU 296, 276, 294; peak 2335, 2306, 2308 |
| S D1 | 13.10, 13.21, 11.92 — CPU 129, 124, 156; peak 170, 173, 178 | 13.61, 13.66, 13.37 — CPU 95, 118, 102; peak 178, 178, 177 | 8.54, 8.51, 8.20 — CPU 334, 331, 310; peak 2258, 2259, 2305 |
| P D2 | 21.05, 20.71, 21.11 — CPU 308, 319, 308; peak 225, 221, 225 | 21.77, 22.03, 21.71 — CPU 232, 226, 231; peak 235, 233, 235 | 13.63, 13.58, 11.67 — CPU 739, 725, 729; peak 2502, 2364, 2618 |
| P D1 | 20.63, 20.98, 20.72 — CPU 323, 312, 314; peak 220, 216, 244 | 22.40, 22.31, 22.23 — CPU 216, 216, 208; peak 240, 235, 236 | 11.75, 12.54, 11.65 — CPU 707, 608, 712; peak 2424, 2598, 2731 |

MorletDB storage on D2 r1, by sample-rate group (`b6_chunk_kinds` in the D2 r1 result files):

| Group | Grizzbit 2.1 | Release |
|---|---|---|
| high | 100,352,320 bytes: 23,523 version-5 grid chunks (corrections table) 100,352,072 bytes, 3 version-2 grid 248 bytes | 122,148,949 bytes: 23,526 version-2 grid |
| mid | 16,279,299 bytes: 992 version-2 float 14,413,525 bytes, 495 version-5 table 1,862,765 bytes, 1 version-5 exceptions only 3,009 bytes | 16,577,315 bytes: 992 version-2 float, 496 version-3 grid |
| process | 3,999 bytes, identical | 3,999 bytes |

The high-rate group is 17.8% smaller with Grizzbit 2.1, in line with the codec measurement on the B3 channels.

### Chunk format of the Grizzbit 2.1 loads

Every Grizzbit 2.1 load refused to start unless the server's `morlet.chunk_format` read back `grizzbit21`
(`b6_b3.py`, `start()`), so no load could run silently on Grizzbit 1. Version-5 chunks were counted directly only on
track S D2 r1 (the table above: 23,523 in the high group, 496 in the mid group), whose result records
`{"asked": "grizzbit21", "server": "grizzbit21"}`. The other track S loads record `null` and the track P loads
`{"asked": "grizzbit21"}`: for those, the server setting was checked at the start, but the stored chunk versions
were not counted.

## Harness notes

- **Fixed before the run, on development data:** B5's D2 r1 completeness check compared the tag "D21" against "D2r1"
  and never matched; it is corrected. A parallel load that hung during harness testing led to a per-load timeout (2,700 s,
  `B6_LOAD_TIMEOUT`), which no benchmark load reached.
- **Decoder hash:** a Rust build embeds its build path, so the B6 decoder used is the recorded artefact from the build
  record, installed by path, not a rebuild.
- **Paderborn:** `B6_PADERBORN_CHUNKS` was left at B4's 1,024, as the owner decided; not `all`.
- **Not run:** nothing. Every step finished; no load was void.

## For the owner

1. **Tight speed margins.** S4 holds, but decode on the B3 phase currents is 53.1 against 50 and encode on
   Sleep-EDF is 10.8 against 10, both on the benchmark Mac. A slower server CPU could fall below the floors. Worth
   stating as such if Grizzbit 2.1 is offered, and worth a look before it becomes the default.
2. **Paderborn.** The rebuilt waveforms are B4's, byte for byte: B4's 1,024-chunk window never reached the development
   bearings (`results/b6_inputs.json`), so section 8's finding did not touch B4's or B5's Paderborn figures.
3. **Chunk format.** Version-5 chunks were counted directly on one load (S D2 r1); the others were guarded by the
   start check only (above).
4. **Ingestion.** About 5% slower than the release, more server CPU; below the statement threshold, so no cost note.
5. **Development inputs** are reported apart and judged by nothing; they agree with the benchmark figures.
6. **Run length.** 46 minutes against budgets of several hours; no step was close to a limit.
7. **Next decisions are yours:** whether to publish these results (section 6), and whether a release offers Grizzbit
   2.1 as opt-in. The default stays Grizzbit 1 until a separate release decision.
