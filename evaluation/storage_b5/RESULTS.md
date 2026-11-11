# Study B5: Grizzbit 2 — results

**Published 2026-11-11 with the owner's approval** (measured 2026-11-10). Only the protocol, harness and results are
published. Grizzbit 2's code (pco chunk kind, chunk format version 4) stays on branch `bench/b5-grizzbit2` and is not
merged: B5 failed, and version 4 is reserved and rejected by every reader. The harness `b5codec/` builds only at the
build commit `5ce0328`, where the grizzbit crate has the pco kind.

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at `758f6cf`; build commit `5ce0328`; build record `bf8327f` (section
8). Harness committed in `81a76bd` before any benchmark input was read; it was tested on the development inputs
only (B4's four development Elf series, a synthetic waveform file, and B3's development set). Every criterion is
judged as frozen.

## Verdict

**B5 fails. Grizzbit 2 does not pass, and the release keeps Grizzbit 1.** S2 fails on storage, which decides B5
whatever happens in the B3 sessions. S6 fails too: in the first session it failed in one ingestion cell, and in the
re-run session of amendment B5.S6r, which is the session S6 is judged on, it fails in two read cells (details
below). S1, S3, S4 and S5 hold.

| Criterion | Result | Key figures |
|---|---|---|
| S1 exact | **holds** | every chunk of all 28 inputs round trips bit for bit (both programs); all 72 B3 loads of both sessions pass B3's full check; 0 read mismatches on D2 r1 for all three arms in both sessions |
| S2 large losses ≤ 1.25× CH best | **fails** | SSD-bench 1.005, Bird-migration 1.044, Food-price 0.766 hold; **Basel-temp 1.561, Basel-wind 1.378 fail** |
| S3 no other gap > 1.10× | **holds** | worst: Paderborn physical 1.012, POI-lon 1.002; Bitcoin-price G2 = G1 (46.11 bits, 1.60× CH-ALP-zstd9, a remaining loss) |
| S4 a chunk changes only when smaller | **holds** | 0 violations on 28 inputs; every changed chunk is version 4 and strictly smaller; every other chunk byte-identical |
| S5 decode ≥ 50 M samples/s | **holds** | waveforms 72.8 (Sleep-EDF) to 157.9 (IMS) M samples/s |
| S6 B3 standing (judged on the B5.S6r re-run) | **fails: (a) and (b)** | (a) R4 recent: the release is faster than QuestDB (1.15 / 1.47), Grizzbit 2 has no statement (1.07 / 1.28). (b) R3: Grizzbit 2 is slower than the release (0.87 / 0.81: 3.27 / 4.59 ms against 2.83 / 3.72). Every ingestion cell now holds |
| *S6, first session (superseded)* | *(a) fails, (b) holds* | *(a) track P 8 writers D2, one load disturbed by an operator test suite (4.30 M samples/s); kept in full below* |

## Run

- **Window:** 2026-11-10 20:44:03 – 21:00:42 (one session, well inside the budgets: storage and codec speed
  20:44:49–20:45:15, B3 session 20:45:31–21:00:42; results 0.7 MB, B3 volumes removed after each load).
- **Start condition** (B3.16's, enforced by the runner): met at 20:44:49 (storage), 20:45:00 (speed) and 20:45:31
  (B3 session) with 1-minute load averages 2.98, 2.99 and 2.75, AC power, lid open; snapshots in `results/machine/`
  and per load in `results/b3-*/<arm>/machine/`.
- **Arms and artefacts** (`results/build.json`): `morletdb:b5` `sha256:2f9d5e33…` built with deploy/Dockerfile from an
  export of `5ce0328`; release `ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1` `sha256:03ed3cc2…` (the release's
  image-digest.txt); QuestDB 10.1.0 `3b11dc5f…`. Decoders: B5 `libmorlet_ffi.dylib` sha256 `e55b6d38…` (cargo build
  --locked --release -p morlet-ffi at `5ce0328`); release decoder sha256 `343a8d9f…` from
  `morletdb-0.1.0a3-py3-none-macosx_11_0_arm64.whl` (wheel sha256 `a08b6cb0…`, equal to the release's SHA256SUMS).
  `Cargo.lock` at the build commit sha256 `d3eb4159…`, pco 1.0.4.
- **Void loads:** none. All 36 loads (18 track S, 18 track P) passed B3's full correctness check on the first attempt;
  no rerun was needed.

## Storage (section 3)

Bits per sample (8 × chunk bytes / samples, frame and checksum included), 4,096-sample chunks (the waveforms' stored
chunk lengths), quantum 1. **G1 recomputed equals B4's M-auto byte for byte on all 28 inputs, and each input's
samples and sha256 equal B4's,** so no codec-history explanation is needed. CH best and Pcodec are B4's figures
(`b4_final.json`), not re-run.

| Input | Samples | Chunks | G1 | G2 | CH best (arm) | Pcodec | G2/CH best | G1/CH best | G2/Pcodec | G2/G1 | Criterion |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|
| CX counts | 4,194,304 | 1,024 | 4.61 | 4.61 | 5.33 (CH-PCO) | 5.23 | 0.865 | 0.865 | 0.881 | 1.000 | S3 ≤ 1.10 |
| IMS decimals | 4,194,304 | 1,024 | 8.81 | 8.81 | 9.19 (CH-PCO) | 8.12 | 0.958 | 0.958 | 1.085 | 1.000 | S3 ≤ 1.10 |
| Paderborn counts | 4,194,304 | 1,024 | 7.88 | 7.88 | 8.47 (CH-PCO) | 8.31 | 0.931 | 0.931 | 0.949 | 1.000 | S3 ≤ 1.10 |
| Paderborn physical | 4,194,304 | 1,024 | 9.17 | 9.03 | 8.92 (CH-PCO) | 8.68 | 1.012 | 1.028 | 1.040 | 0.985 | S3 ≤ 1.10 |
| Sleep-EDF | 4,194,304 | 1,024 | 14.66 | 14.62 | 18.33 (CH-PCO) | 20.27 | 0.798 | 0.800 | 0.721 | 0.998 | S3 ≤ 1.10 |
| ECG | 4,194,000 | 4,194 | 7.40 | 7.40 | 8.08 (CH-PCO) | 7.84 | 0.916 | 0.916 | 0.944 | 1.000 | S3 ≤ 1.10 |
| Air-pressure | 95,928 | 24 | 11.45 | 10.15 | 10.77 (CH-PCO) | 8.73 | 0.943 | 1.063 | 1.163 | 0.887 | S3 ≤ 1.10 |
| Air-sensor | 8,664 | 3 | 46.10 | 45.92 | 46.63 (CH-PCO) | 46.57 | 0.985 | 0.989 | 0.986 | 0.996 | S3 ≤ 1.10 |
| Basel-temp | 100,001 | 25 | 52.38 | 32.89 | 21.07 (CH-zstd9) | 15.27 | **1.561** | 2.486 | 2.154 | 0.628 | S2 ≤ 1.25 |
| Basel-wind | 100,000 | 25 | 53.45 | 30.03 | 21.79 (CH-PCO) | 14.29 | **1.378** | 2.453 | 2.101 | 0.562 | S2 ≤ 1.25 |
| Bird-migration | 89,867 | 22 | 45.90 | 17.74 | 16.99 (CH-PCO) | 15.05 | 1.044 | 2.701 | 1.179 | 0.387 | S2 ≤ 1.25 |
| Bitcoin-price | 7,116 | 2 | 46.11 | 46.11 | 28.80 (CH-ALP-zstd9) | 46.20 | 1.601 | 1.601 | 0.998 | 1.000 | S3: G2 ≤ G1 |
| City-lat | 41,001 | 11 | 20.46 | 20.43 | 20.63 (CH-PCO) | 20.54 | 0.990 | 0.992 | 0.995 | 0.999 | S3 ≤ 1.10 |
| City-lon | 41,001 | 11 | 21.65 | 21.62 | 22.26 (CH-PCO) | 22.14 | 0.971 | 0.972 | 0.977 | 0.999 | S3 ≤ 1.10 |
| City-temp | 100,001 | 25 | 7.50 | 7.20 | 7.73 (CH-PCO) | 7.72 | 0.932 | 0.970 | 0.933 | 0.960 | S3 ≤ 1.10 |
| Dew-point-temp | 100,001 | 25 | 8.19 | 8.19 | 9.09 (CH-PCO) | 8.98 | 0.901 | 0.901 | 0.912 | 1.000 | S3 ≤ 1.10 |
| Food-price | 100,001 | 25 | 32.14 | 11.30 | 14.75 (CH-ALP-zstd9) | 11.84 | 0.766 | 2.179 | 0.954 | 0.351 | S2 ≤ 1.25 |
| IR-bio-temp | 99,602 | 25 | 3.60 | 3.60 | 4.41 (CH-PCO) | 4.29 | 0.818 | 0.818 | 0.841 | 1.000 | S3 ≤ 1.10 |
| PM10-dust | 93,662 | 23 | 3.99 | 3.82 | 4.28 (CH-PCO) | 4.45 | 0.891 | 0.930 | 0.858 | 0.957 | S3 ≤ 1.10 |
| POI-lon | 100,001 | 25 | 64.02 | 54.93 | 54.81 (CH-PCO) | 54.55 | 1.002 | 1.168 | 1.007 | 0.858 | S3 ≤ 1.10 |
| SSD-bench | 8,927 | 3 | 31.40 | 7.92 | 7.88 (CH-PCO) | 7.59 | 1.005 | 3.985 | 1.044 | 0.252 | S2 ≤ 1.25 |
| Stocks-DE | 100,002 | 25 | 5.52 | 5.36 | 6.14 (CH-PCO) | 6.58 | 0.873 | 0.900 | 0.815 | 0.971 | S3 ≤ 1.10 |
| Stocks-UK | 100,002 | 25 | 5.30 | 5.26 | 5.96 (CH-PCO) | 8.58 | 0.882 | 0.888 | 0.613 | 0.993 | S3 ≤ 1.10 |
| Wind-Speed | 99,132 | 25 | 5.08 | 5.08 | 5.85 (CH-PCO) | 5.40 | 0.868 | 0.868 | 0.942 | 1.000 | S3 ≤ 1.10 |
| *Blockchain-tr (dev)* | 99,999 | 25 | 13.96 | 13.26 | 13.16 (CH-PCO) | 12.37 | 1.007 | 1.060 | 1.072 | 0.950 | none |
| *POI-lat (dev)* | 100,001 | 25 | 60.84 | 53.58 | 53.51 (CH-PCO) | 53.30 | 1.001 | 1.137 | 1.005 | 0.881 | none |
| *Stocks-USA (dev)* | 100,002 | 25 | 4.56 | 4.56 | 5.20 (CH-PCO) | 5.23 | 0.877 | 0.877 | 0.872 | 1.000 | none |
| *electric_vehicle_charging (dev)* | 3,395 | 1 | 10.03 | 10.03 | 10.46 (CH-PCO) | 10.39 | 0.959 | 0.959 | 0.965 | 1.000 | none |

What the table says:
- **H1 / S2:** Grizzbit 2 closes three of B4's five large losses (SSD-bench 4.0× → 1.005×, Bird-migration 2.7× →
  1.04×, Food-price 2.2× → 0.77×, now smaller than every ClickHouse arm) and halves the other two, which remain
  **1.56× (Basel-temp) and 1.38× (Basel-wind)**. On those two, per-chunk pco is itself 2.15× and 2.10× Pcodec on the
  whole series, which points to the 4,096-sample chunk rather than the coding (the spike's 0.3–0.6% per-chunk
  penalty did not hold on these series; not attributed further, other chunk sizes being outside B5). Basel-temp's best ClickHouse arm is CH-zstd9 (21.07 bits).
- **H2 / S3:** every other benchmark input is at most 1.012× ClickHouse's best; 17 of the 24 are smaller than it.
- **Bitcoin-price:** no chunk changes (2 float chunks); the loss to CH-ALP-zstd9 (1.60×) remains, as declared.
- **Waveforms:** CX, IMS, Paderborn counts and ECG are unchanged or nearly (0–9 chunks). Paderborn physical: 730 of
  1,024 grid chunks become pco (−1.5%); IMS decimals stays 1.085× Pcodec (no chunk is smaller as pco).

### Changed chunks (S4) and chunk kinds

Chunk lengths are as stored: 4,096 per chunk, and each input's final partial chunk (`chunk_lengths` per input in
`b5_storage.json`). Every chunk was coded by both the codec bench example (`rawcodec_bench [--grizzbit2] --per-chunk`,
built from `5ce0328`) and `b5codec` (byte identity), and their per-chunk version, kind and bytes agree on every input.

| Input | Changed / chunks | Changed, by G1 kind | G1 kinds | G2 kinds |
|---|---:|---|---|---|
| CX counts | 9 / 1024 | 9 v2 grid | 1024 v2 grid | 1015 v2 grid, 9 v4 pco |
| IMS decimals | 0 / 1024 | - | 1024 v2 grid | 1024 v2 grid |
| Paderborn counts | 0 / 1024 | - | 1024 v2 grid | 1024 v2 grid |
| Paderborn physical | 730 / 1024 | 730 v2 grid | 1024 v2 grid | 294 v2 grid, 730 v4 pco |
| Sleep-EDF | 32 / 1024 | 2 v2 grid, 30 v3 grid | 32 v2 grid, 992 v3 grid | 30 v2 grid, 962 v3 grid, 32 v4 pco |
| ECG | 0 / 4194 | - | 4194 v2 grid | 4194 v2 grid |
| Air-pressure | 22 / 24 | 22 v3 grid | 24 v3 grid | 2 v3 grid, 22 v4 pco |
| Air-sensor | 2 / 3 | 2 v2 float | 3 v2 float | 1 v2 float, 2 v4 pco |
| Basel-temp | 24 / 25 | 24 v2 float | 25 v2 float | 1 v2 float, 24 v4 pco |
| Basel-wind | 25 / 25 | 25 v2 float | 25 v2 float | 25 v4 pco |
| Bird-migration | 22 / 22 | 22 v2 float | 22 v2 float | 22 v4 pco |
| Bitcoin-price | 0 / 2 | - | 2 v2 float | 2 v2 float |
| City-lat | 1 / 11 | 1 v2 float | 1 v2 float, 10 v2 grid | 10 v2 grid, 1 v4 pco |
| City-lon | 1 / 11 | 1 v2 float | 1 v2 float, 10 v2 grid | 10 v2 grid, 1 v4 pco |
| City-temp | 10 / 25 | 10 v2 grid | 25 v2 grid | 15 v2 grid, 10 v4 pco |
| Dew-point-temp | 0 / 25 | - | 25 v2 grid | 25 v2 grid |
| Food-price | 25 / 25 | 13 v2 float, 12 v2 grid | 13 v2 float, 12 v2 grid | 25 v4 pco |
| IR-bio-temp | 0 / 25 | - | 25 v2 grid | 25 v2 grid |
| PM10-dust | 2 / 23 | 2 v2 grid | 23 v2 grid | 21 v2 grid, 2 v4 pco |
| POI-lon | 25 / 25 | 25 v2 raw | 25 v2 raw | 25 v4 pco |
| SSD-bench | 3 / 3 | 1 v2 float, 2 v2 grid | 1 v2 float, 2 v2 grid | 3 v4 pco |
| Stocks-DE | 5 / 25 | 5 v2 grid | 25 v2 grid | 20 v2 grid, 5 v4 pco |
| Stocks-UK | 1 / 25 | 1 v2 grid | 25 v2 grid | 24 v2 grid, 1 v4 pco |
| Wind-Speed | 0 / 25 | - | 25 v2 grid | 25 v2 grid |
| *Blockchain-tr (dev)* | 15 / 25 | 15 v2 grid | 25 v2 grid | 10 v2 grid, 15 v4 pco |
| *POI-lat (dev)* | 25 / 25 | 25 v2 float | 25 v2 float | 25 v4 pco |
| *Stocks-USA (dev)* | 0 / 25 | - | 25 v2 grid | 25 v2 grid |
| *electric_vehicle_charging (dev)* | 0 / 1 | - | 1 v2 grid | 1 v2 grid |

## Codec speed (section 4A)

Single thread, M samples/s, total samples / total time over all chunks, best of 3 passes (`results/b5_speed.json`;
1-minute load 2.99–3.07 during the measurement). G2's encode includes Grizzbit 1's encode, by design.

| Input | G1 encode | G2 encode | G2/G1 | G1 decode | G2 decode | G2/G1 |
|---|---:|---:|---:|---:|---:|---:|
| CX counts | 39.7 | 19.2 | 0.48 | 121.8 | 122.2 | 1.00 |
| IMS decimals | 26.5 | 12.0 | 0.45 | 158.2 | 157.9 | 1.00 |
| Paderborn counts | 33.9 | 14.5 | 0.43 | 143.7 | 143.6 | 1.00 |
| Paderborn physical | 16.6 | 9.6 | 0.58 | 83.6 | 124.5 | 1.49 |
| Sleep-EDF | 11.1 | 6.2 | 0.56 | 71.5 | 72.8 | 1.02 |
| ECG | 11.5 | 6.6 | 0.57 | 116.0 | 116.7 | 1.01 |
| Air-pressure | 12.0 | 7.7 | 0.64 | 75.2 | 155.0 | 2.06 |
| Air-sensor | 9.3 | 5.5 | 0.60 | 98.2 | 115.0 | 1.17 |
| Basel-temp | 9.6 | 5.3 | 0.56 | 78.7 | 110.7 | 1.41 |
| Basel-wind | 9.6 | 5.3 | 0.55 | 82.4 | 112.9 | 1.37 |
| Bird-migration | 10.5 | 6.6 | 0.63 | 89.8 | 163.7 | 1.82 |
| Bitcoin-price | 10.3 | 6.1 | 0.60 | 90.7 | 90.6 | 1.00 |
| City-lat | 16.9 | 7.8 | 0.46 | 111.5 | 109.9 | 0.99 |
| City-lon | 16.9 | 7.7 | 0.46 | 161.6 | 160.6 | 0.99 |
| City-temp | 23.2 | 13.2 | 0.57 | 121.5 | 138.7 | 1.14 |
| Dew-point-temp | 20.1 | 9.6 | 0.47 | 118.8 | 119.8 | 1.01 |
| Food-price | 12.2 | 7.5 | 0.62 | 96.6 | 148.5 | 1.54 |
| IR-bio-temp | 20.8 | 13.6 | 0.65 | 105.8 | 105.2 | 0.99 |
| PM10-dust | 28.9 | 16.8 | 0.58 | 124.3 | 128.3 | 1.03 |
| POI-lon | 9.9 | 6.0 | 0.60 | 192.6 | 135.2 | 0.70 |
| SSD-bench | 12.9 | 8.6 | 0.67 | 94.9 | 153.8 | 1.62 |
| Stocks-DE | 23.0 | 14.1 | 0.61 | 120.2 | 126.4 | 1.05 |
| Stocks-UK | 23.2 | 14.3 | 0.62 | 119.5 | 120.6 | 1.01 |
| Wind-Speed | 30.8 | 17.4 | 0.57 | 125.1 | 125.2 | 1.00 |

**S5 holds:** G2 decodes the six waveforms at 72.8–157.9 M samples/s. **Encode cost:** keeping the smaller runs both
codings, so G2 encodes at 0.43–0.58× G1 on the waveforms and 0.46–0.67× on the Elf series.

## B3 session 1 (section 4B; superseded for S6 by amendment B5.S6r)

This is the first session, reported in full. S6 is judged on session 2 below, the re-run of amendment B5.S6r.

One session, 36 loads: track S (B3.16: r = 1–3, D2 then D1, Grizzbit 2, QuestDB, release) and track P (B3.17:
r = 1–3, 8 writers D2 then D1, same order). Reads and statistics on D2 r1 from a fresh client process; both MorletDB
arms read R2 and R3 by compressed transfer with their own decoder (`encoded_read: true`). Statement rule (B3's):
ingestion ≥ 10% in all three loads (ratio of medians, all-loads bound in brackets); reads and statistics ≥ 10% at
both p50 and p95; R5 queries per second as B3.16 reported it. Figures are "other / this arm" for latencies and
"this arm / other" for rates: > 1 means the first-named arm is better.

| Measure | Release vs QuestDB | Grizzbit 2 vs QuestDB | Grizzbit 2 vs release |
|---|---|---|---|
| Ingestion S, D2 | **1.46 (1.45)** | **1.29 (1.22)** | **0.89 (0.90), slower** |
| Ingestion S, D1 | **1.64 (1.61)** | **1.46 (1.41)** | **0.89 (0.90), slower** |
| Ingestion P (8 writers), D2 | **1.81 (1.49)** | no statement (1.53) | no statement (0.84) |
| Ingestion P (8 writers), D1 | **2.03 (1.81)** | **1.89 (1.55)** | no statement (0.93) |
| R1 4,096 (p50 / p95) | **1.51 / 1.71** | **1.53 / 1.60** | no statement (1.02 / 0.93) |
| R2 128,000 | **3.51 / 3.53** | **3.34 / 3.71** | no statement (0.95 / 1.05) |
| R3 3 × 64,000 | **3.19 / 3.19** | **3.61 / 3.73** | **1.13 / 1.17** |
| R4 recent | no statement (0.95 / 1.25) | no statement (1.00 / 1.25) | no statement (1.05 / 1.00) |
| R4 older | no statement (1.01 / 1.27) | no statement (1.06 / 1.25) | no statement (1.05 / 0.98) |
| R5 queries/s (4 threads) | **1.51** | **1.63** | no statement (1.08) |
| Interval statistics | **5.42 / 8.86** | **5.75 / 9.62** | no statement (1.06 / 1.09) |

Latencies (ms, p50 / p95):

| Read | Grizzbit 2 | Release | QuestDB |
|---|---|---|---|
| R1 | 0.620 / 1.083 | 0.631 / 1.010 | 0.951 / 1.728 |
| R2 | 2.085 / 2.695 | 1.984 / 2.828 | 6.968 / 9.994 |
| R3 | 2.614 / 3.438 | 2.957 / 4.012 | 9.443 / 12.817 |
| R4 recent | 0.622 / 1.008 | 0.656 / 1.008 | 0.625 / 1.263 |
| R4 older | 0.576 / 0.980 | 0.607 / 0.962 | 0.613 / 1.224 |
| R5 | 0.835 / 1.516 | 0.928 / 1.593 | 1.067 / 3.780 |
| Statistics | 0.436 / 0.502 | 0.463 / 0.545 | 2.508 / 4.831 |

Interval statistics agree with the samples within 3.5e-16 (MorletDB arms) and 8.1e-14 (QuestDB) relative.

Ingestion per load (M samples/s; server CPU % mean; `docker stats` memory peak MiB):

| Cell | Grizzbit 2 | Release | QuestDB |
|---|---|---|---|
| S D2 | 11.90, 12.57, 12.61 — CPU 179, 163, 164; peak 179, 178, 177 | 14.05, 14.19, 14.17 — CPU 124, 123, 127; peak 181, 178, 179 | 9.58, 9.71, 9.72 — CPU 308, 304, 290; peak 2304, 2352, 2306 |
| S D1 | 12.00, 12.28, 12.27 — CPU 170, 170, 169; peak 178, 175, 174 | 13.84, 13.73, 13.79 — CPU 122, 121, 122; peak 179, 178, 178 | 8.50, 8.43, 6.56 — CPU 320, 326, 287; peak 2204, 2233, 2017 |
| P D2 | 19.95, 18.03, **4.30** — CPU 426, 403, 129; peak 230, 271, 269 | 21.66, 21.38, 20.26 — CPU 234, 232, 239; peak 234, 227, 242 | 13.65, 11.79, 10.38 — CPU 752, 721, 595; peak 2353, 2594, 2631 |
| P D1 | 20.17, 19.99, 17.92 — CPU 424, 415, 382; peak 243, 234, 268 | 21.87, 21.46, 20.88 — CPU 225, 236, 259; peak 240, 232, 231 | 11.55, 10.59, 9.17 — CPU 711, 694, 623; peak 2405, 2691, 2592 |

**Ingestion against the release, session 1:** single-connection ingestion of Grizzbit 2 was 11% slower than the
release in both durability classes (ratio of medians 0.89, every load ≥ 10% slower), a statement in this session,
with about 40% more server CPU (164–179% against 121–127%). With 8 writers there was no statement (0.84 and 0.93).
The cost reported under S6 is session 2's (below), where this statement does not hold.

**S6 in session 1, and the machine.** S6(a) failed in exactly one cell: track P, 8 writers, D2. There the release
kept B3.17's statement against QuestDB and Grizzbit 2 did not. Grizzbit 2's loads there were 19.95, 18.03 and
**4.30** M samples/s. The third (r3, 20:58:06–20:58:49) took 23.6 s against about 5 s, with server CPU at 129%.

During track P the operator ran a test suite (`cargo pgrx test`) on the same Mac. The snapshots around r3 show the
1-minute load rising from 8.55 to 16.71 with `cargo` and `rustc` running. The session's start condition was met
(1-minute load 2.75 at 20:45:31), but the frozen protocol checks quiet only before the session. The machine was
busy throughout: 1-minute load 2.7–16.7 at the per-load snapshots, 5-minute 6–11. The load was correct, so it was
not void and was not rerun. The owner approved re-running the whole session as amendment B5.S6r, with the start
condition checked before every load.

**Release against B3.16 and B3.17 (other sessions, other releases: descriptive only).** B3.16 measured 0.3.7, B3.17
0.3.9; B5's release arm is 0.3.10. Differences in this session's release statements: R1 now has a statement
(1.51 / 1.71; B3.16 none, 1.02 / 1.33); R4 older no longer favours QuestDB (1.01 / 1.27; B3.16 QuestDB 1.5 / 1.2);
interval statistics 5.4 / 8.9 (B3.16 4.4 / 7.3); 8-writer ingestion 1.81 (D2) and 2.03 (D1) (B3.17 1.61 and 1.81).
Ingestion from one connection 1.46 / 1.64 (B3.16 1.53 / 1.58), R2 3.5 / 3.5 (2.6 / 2.6), R3 3.2 / 3.2 (2.9 / 2.7),
R5 1.51 (1.57).

### MorletDB storage on D2 r1 (per group, chunk bytes = stored chunk lengths)

| Group | Grizzbit 2 | Release |
|---|---|---|
| high (3 × 64 kHz) | 120,041,371 B: 4,657 v2 grid (22.1 MB), 18,869 v4 pco (97.9 MB) | 122,148,949 B: 23,526 v2 grid |
| mid (3 × 4 kHz) | 16,217,838 B: 93 v2 float, 496 v3 grid, 899 v4 pco | 16,577,315 B: 992 v2 float, 496 v3 grid |
| process (1 Hz) | 3,999 B: 1 v2 raw, 97 v2 grid, 19 v2 float, 7 v3 grid (identical) | 3,999 B (identical) |
| Database | 168,520,851 B | 171,289,747 B |

Grizzbit 2 stores B3's chunks 1.7% smaller (high group, with B3's declared quanta) and 2.2% smaller (mid). QuestDB's
native volume on D2 r1: 1,250 MB. Session 2 stored exactly the same chunks: same digest over all stored chunks for
each arm, same bytes per group, same database size.

## B3 session 2: amendment B5.S6r (S6 is judged here)

- **What ran:** the complete session again, 2026-11-10 21:06:45 – 22:25:19. It used the same 36 loads, order,
  repetitions, durability classes, images, decoders and harness (`run_b5_b3.sh s6r`, results in `results/s6r/`,
  fresh `b5-s6r-*` volumes). B3.16's start condition was checked **before every load**.
- **Gate:** all 36 loads passed it, so none was reported as not run. Waits were median 36 s, maximum 1,132 s. The
  longest waits were before release D2 r2, held about 19 minutes while macOS's `mediaanalysisd` and `mds` used more
  than half a core.
- **Loads:** all 36 passed B3's full correctness check on the first attempt. None was void or rerun.
- **Load at the per-load snapshots:** 1-minute load 2.70–10.39, median 2.98.
- **One disturbance after a gate:** a foreign multi-process Python job, not part of B5, started at 21:26:40. That
  was 4 s after the gate passed for Grizzbit 2 track S D2 r2. Its ingestion (8.1 s) ran at 12.51 M samples/s against
  12.40 and 12.75 for r1 and r3, so no effect is visible. The load average then rose above 100, and the gate held the
  following loads until the job ended.

| Measure | Release vs QuestDB | Grizzbit 2 vs QuestDB | Grizzbit 2 vs release |
|---|---|---|---|
| Ingestion S, D2 | **1.41 (1.40)** | **1.28 (1.21)** | no statement (0.91) |
| Ingestion S, D1 | **1.63 (1.54)** | **1.45 (1.41)** | no statement (0.89) |
| Ingestion P (8 writers), D2 | **1.61 (1.59)** | **1.48 (1.44)** | no statement (0.92) |
| Ingestion P (8 writers), D1 | **1.74 (1.60)** | **1.64 (1.55)** | no statement (0.94) |
| R1 4,096 (p50 / p95) | **1.32 / 1.61** | **1.28 / 1.41** | no statement (0.97 / 0.87) |
| R2 128,000 | **3.19 / 4.57** | **2.99 / 4.46** | no statement (0.94 / 0.98) |
| R3 3 × 64,000 | **3.70 / 4.24** | **3.20 / 3.44** | **0.87 / 0.81, slower** |
| R4 recent | **1.15 / 1.47** | no statement (1.07 / 1.28) | no statement (0.93 / 0.87) |
| R4 older | **1.25 / 1.57** | **1.33 / 1.58** | no statement (1.06 / 1.00) |
| R5 queries/s (4 threads) | **1.65** | **1.72** | no statement (1.04) |
| Interval statistics | **5.40 / 9.49** | **5.42 / 8.94** | no statement (1.00 / 0.94) |

Latencies (ms, p50 / p95):

| Read | Grizzbit 2 | Release | QuestDB |
|---|---|---|---|
| R1 | 0.651 / 1.200 | 0.630 / 1.045 | 0.833 / 1.686 |
| R2 | 2.401 / 3.094 | 2.250 / 3.022 | 7.169 / 13.806 |
| R3 | 3.270 / 4.586 | 2.831 / 3.718 | 10.468 / 15.769 |
| R4 recent | 0.673 / 1.164 | 0.627 / 1.011 | 0.721 / 1.487 |
| R4 older | 0.569 / 0.981 | 0.603 / 0.985 | 0.755 / 1.547 |
| R5 | 0.868 / 1.547 | 0.916 / 1.569 | 1.191 / 3.812 |
| Statistics | 0.455 / 0.539 | 0.457 / 0.508 | 2.467 / 4.819 |

Ingestion per load (M samples/s; server CPU % mean; `docker stats` memory peak MiB):

| Cell | Grizzbit 2 | Release | QuestDB |
|---|---|---|---|
| S D2 | 11.83, 12.51, 12.75 — CPU 181, 166, 163; peak 179, 177, 178 | 13.72, 14.27, 13.78 — CPU 102, 120, 124; peak 178, 178, 178 | 8.90, 9.75, 9.81 — CPU 304, 284, 282; peak 2277, 2307, 2308 |
| S D1 | 12.40, 12.44, 12.22 — CPU 171, 166, 168; peak 175, 176, 173 | 13.38, 13.97, 13.99 — CPU 101, 117, 116; peak 178, 178, 179 | 8.56, 8.69, 7.57 — CPU 320, 333, 286; peak 2215, 2242, 2292 |
| P D2 | 21.19, 20.71, 21.43 — CPU 366, 383, 362; peak 228, 218, 231 | 23.20, 23.08, 22.77 — CPU 187, 182, 193; peak 238, 240, 238 | 14.34, 14.32, 10.70 — CPU 754, 742, 701; peak 2400, 2482, 2507 |
| P D1 | 21.51, 21.56, 20.61 — CPU 363, 361, 406; peak 229, 233, 231 | 22.98, 22.81, 21.15 — CPU 187, 183, 249; peak 236, 236, 230 | 13.25, 13.15, 12.32 — CPU 674, 587, 637; peak 2386, 2744, 2613 |

**S6 verdict (session 2): fails, in two read cells. Every ingestion cell holds.**
- **S6(a), R4 recent:** the release is faster than QuestDB (1.15 / 1.47). Grizzbit 2 has no statement (1.07 / 1.28):
  it is 1.07× at p50, short of 1.10.
- **S6(b), R3:** Grizzbit 2 is slower than the release, 0.87 at p50 and 0.81 at p95 (3.27 / 4.59 ms against 2.83 /
  3.72 ms).
- **All other measures:** Grizzbit 2 makes every statement the release makes against QuestDB, and is not slower than
  the release on any other read or on statistics. All four ingestion cells hold, including track P 8 writers D2 (1.48,
  bound 1.44), the cell that failed in session 1.

**Read-to-read variation between sessions.** Some of these read ratios moved by more than 10% between the two
sessions on identical stored chunks. R3, Grizzbit 2 against the release, was 1.13 / 1.17 (Grizzbit 2 faster) in
session 1 and is 0.87 / 0.81 (slower) in session 2. R4 recent, release against QuestDB, was 0.95 / 1.25 (no
statement) and is now 1.15 / 1.47.

Both sessions decode with the same decoders and read the same chunks. Between sessions, Grizzbit 2's own R3 rose
from 2.61 / 3.44 to 3.27 / 4.59 ms, while the release's moved from 2.96 / 4.01 to 2.83 / 3.72 ms. This is not
attributed: B5 has no repeated read measurement within a session to separate noise from cost. As the amendment
fixes, the re-run counts, and S6 fails.

**Ingestion cost (section 5, S6, from session 2):** Grizzbit 2 against the release has **no statement** in any cell.
The ratios of medians are 0.91 (S, D2), 0.89 (S, D1), 0.92 (P, D2) and 0.94 (P, D1); in S D2 and S D1 at least one
Grizzbit 2 load is within 10% of a release load. Grizzbit 2 does use more server CPU: about 1.65–1.8 cores against
1.0–1.25 from one connection, and about 3.6–4.1 against 1.8–2.5 cores with 8 writers. Peak memory is the same,
about 175–230 MiB. So the cost is about 6–11% throughput, below the statement threshold, at roughly 1.4–2× the
server CPU.

**Release against session 1 (same release, same day: descriptive).** R1 1.32 / 1.61 (1.51 / 1.71), R4 recent
1.15 / 1.47 (none), R4 older 1.25 / 1.57 (none), 8-writer ingestion 1.61 / 1.74 (1.81 / 2.03).

## Harness notes (no criterion changed)

These are how the frozen protocol was carried out, recorded so nothing is silent:

1. **Byte identity (S4).** The codec bench example reports each chunk's version, kind and bytes, not its bytes.
   `b5codec` (this directory, the grizzbit crate of this branch, identical to `5ce0328`) codes each chunk both ways
   and compares the bytes; the harness requires its per-chunk version, kind and bytes to equal the bench example's.
2. **Track P's MorletDB arm.** B3.17's `parallel.py` names B3.10's write path `morletdb-i1`; both MorletDB arms of
   track P run it with their own image (`morletdb-i2` differs from it only in reads, which track P does not do).
3. **Chunk-kind counts per group** come from `b3_load.py`, which runs `run.py load` unchanged and adds a query over
   the stored chunks' version and kind bytes to the D2 r1 storage measurement.
4. **`evaluation/tsdb_b3`** changed only by `B3_VOLUME_PREFIX` (fresh `b5-g2-*`, `b5-q-*`, `b5-rel-*` volumes).
5. **The smoke check** (`results/dev/b3-smoke/`, B3's development set) ran without waiting for a quiet machine; it
   is a harness check and none of its figures is used.
6. Session 1 took 15 minutes and session 2 took 79 minutes, against a 3-hour budget each. No load was near 07:00.
7. **Amendment B5.S6r** (`aff752f`, runner `d302543`) was committed and pushed before session 2 started. Its runner
   change only adds the per-load gate, the `results/s6r/` output and the `b5-s6r-*` volume prefix.

## For the owner

- **B5 fails, plainly, on S2 alone, whatever S6 says.** S6 also fails in the B5.S6r re-run, in R4 recent (S6a) and R3
  (S6b). Section 5 allows a fallback only as a new frozen amendment (B5.1, …), labelled "after B5's results were
  known". The S2 failure is the chunk size on Basel-temp and Basel-wind: pco per chunk is 2.1× Pcodec on the whole
  series there. Neither listed fallback (grid-only exclusion, level 4) addresses that, and other chunk sizes are
  outside B5 (section 7).
- **The S6 read failures are small and flip between sessions.** R3 was Grizzbit 2 faster by 1.13 / 1.17 in session 1
  and slower by 0.87 / 0.81 in session 2. That suggests B3's read rule is sensitive to session noise at this
  margin, which may matter for any future S6-style criterion. The re-run counts, as fixed.
- **Ingestion cost:** in the re-run, no statement against the release in any cell (medians 0.89–0.94), with about
  1.4–2× the server CPU. Session 1's −11% statement did not repeat.
- Section 6: with Grizzbit 2 not shipping, card 2 ("Floats without a value grid") is to get B4's real numbers
  (2.2–4.0×); nothing was changed on the website, docs or paper.
