# Stage-1 compression benchmark: results and gate decisions

Protocol: [`../../PROTOCOL.md`](../../PROTOCOL.md), frozen in `d571e92` before any run.
One logged pre-run change (the Paderborn grid tolerance) is recorded there.

- Every run used a clean tree. Benchmark rows carry `f281484` (the output directory is
  named after the launch commit, `ab70819`; `bench.py` is identical in both).
- Development (dev) and held-out were run once each. Operating points were chosen
  mechanically on dev (`operating.json`) before the held-out runs.
- Hardware: Apple M5 Max (18 cores), macOS 27.0.1. Library versions are in each
  `rd-*.json`.
- **448,360 encoded blocks were decoded and checked against the original samples. No
  lossless mismatch and no error-bound violation occurred.** These bounds are empirical,
  checked per block, and not mathematical guarantees.

**Verified here vs. not:** every number below was measured in this run from the stated
files.
- The comparison with MorletDB's current storage reuses the PR #6 seismic report
  (different days and stations), so it is an **estimate**, labelled as such.
- Paper and vendor compression claims (instinct PR #1) remain untested.

## Headline

1. **The biggest, safest saving is not a new codec.** MorletDB stores raw chunks as
   `float8[]`, which measured 22.7 bits/sample on IPOC seismic counts (PR #6 report,
   TOAST-compressed). Native integers with FLAC take **4.6 bits/sample** on comparable
   held-out CX counts, losslessly.
   - That is about a **5× reduction from a standard codec and the right sample type**
     (estimate across reports).
   - Naive float64 Parquet is equally wasteful: 36.9 vs 9.1 bits/sample on Paderborn.
2. **Lossless specialized coding barely beats optimized Parquet** (H1 lossless: no
   material gain). The best is FLAC at 1.19× on Paderborn and CX; on IMS nothing beats
   Parquet by more than 1.02×.
3. **Lossy coding reaches about 3× vs optimized Parquet with the bearing indicators
   preserved** (H1 lossy: material on Paderborn and IMS).
   - The winner is **plain scalar quantization** (+ FLAC or zstd), not wavelets.
   - On seismic data, the trigger detector tolerates only near-lossless bounds (1.7×).
4. **Decomposition does not beat simple coding (H4: not supported).**
   - The wavelet codec is 19–57% *larger* than simple quantization at the same bound.
   - The harmonic predictor ties simple quantization (±1%).
   - The shaft-synchronous template is 14–38% larger.
   - Simple quantization + FLAC also beats SZ3, zfp and SPERR on these 1-D integer
     signals.
5. **Progressive raw access saves bytes, but not enough to pass its gate (H3).**
   Storing the retrieval representation (envelopes) separately is far better:
   - 40× lower latency than exhaustive decoding, identical results, 4× fewer bytes.
6. **Triggered retention keeps 34% of raw data** on these noisy seismic stations,
   which is 0.58× of full FLAC including features (H6). Its event coverage rests on only
   3 catalogued arrivals, which is too few to judge.

## H1: storage (held-out; factor = optimized Parquet bytes / codec bytes; CI over units)

Optimized lossless Parquet (chosen on dev) is DELTA_BINARY_PACKED + zstd-19 for Paderborn
and CX, and BYTE_STREAM_SPLIT + zstd-19 for IMS. It uses one file per unit and one row
group per block.

Bits per sample:

| Dataset (units) | Native int | Optimized Parquet | Naive float64 Parquet | Steim2 miniSEED |
|---|---|---|---|---|
| Paderborn (23 bearings) | 16 | 9.12 | 36.87 | — |
| IMS (8 bearing channels) | 16 | 8.15 | 49.28 | — |
| CX (4 stations) | 16.1 | 5.45 | 7.87 | 6.99 |

**Lossless** (bits/sample, factor vs Parquet [95% CI]):

| Codec | Paderborn | IMS | CX |
|---|---|---|---|
| int + zstd | 8.82, 1.03 [1.02, 1.05] | 7.99, 1.02 [1.02, 1.03] | 7.35, 0.74 [0.69, 0.80] |
| delta + zstd | 8.74, 1.05 [1.04, 1.05] | 8.67, 0.94 | 6.25, 0.87 |
| **FLAC** | **7.66, 1.19 [1.18, 1.20]** | 8.07, 1.01 [1.00, 1.02] | **4.59, 1.19 [1.16, 1.21]** |
| 5/3 lifting + zstd | 8.65, 1.06 | 9.21, 0.89 | 5.76, 0.95 |

Verdict: **some gain (1.0–1.2×), not material.**

**Lossy at the dev operating point** (bits/sample, factor vs Parquet [95% CI]):

| Codec | Paderborn (ε = 13.9 counts) | IMS (ε = 24.4) | CX (ε = 1.5) |
|---|---|---|---|
| quant + zstd | 3.24, **2.82 [2.67, 2.95]** | 2.74, **2.97 [2.84, 3.11]** | 5.25, 1.04 |
| quant + FLAC | 3.18, **2.87 [2.79, 2.95]** | 2.83, **2.88 [2.78, 3.02]** | 3.23, 1.69 [1.66, 1.72] |
| harmonic + residual | 3.17, 2.88 [2.79, 2.96] | 2.77, 2.94 [2.85, 3.04] | 3.23, 1.69 |
| dwt-q (ε 27.7 / 24.4 / 3.1) | 3.02, 3.02 [2.87, 3.18] | 3.26, 2.50 | 3.83, 1.42 |
| tsa + residual (ε 13.9 / 12.2) | 3.57, 2.55 | 4.59, 1.78 | n/a |
| SZ3 | 3.59, 2.54 | 3.22, 2.53 | 3.98, 1.37 |
| SPERR (rows of 4096) | 4.16, 2.20 | 3.58, 2.27 | 5.29, 1.03 |
| zfp (ε 55 / 49 / 12) | 7.48, 1.22 | 7.76, 1.05 | 10.18, 0.53 |
| MGARD | not installable (pip, macOS arm64, Py 3.12) | | |

Verdicts:
- Paderborn and IMS: **material (≥ 2×)** for quantization, harmonic, dwt-q and SZ3.
- CX: **some gain** (up to 1.7×).
- On Paderborn, dwt-q reached a *larger* dev bound (27.7) than the others and is
  therefore 3.02× there. At the common bound, it is 29% larger (H4).

Rate–distortion curves: `fig-rate.png` (bits/sample vs ε/σ) and `fig-diag.png`
(indicator change and trigger recall vs bits/sample). Zero-change (exact) points are
omitted from the log axis.

## H2: does diagnostic evidence survive? (held-out)

- **Indicator values:** at the operating points, the 95th percentile of relative change
  is ≤ 5% for D-KURT and D-ENV on Paderborn and IMS (`summary.json` → `rd`). For
  example, at ε/σ = 1/8, quant-flac changes D-KURT by ≤ 4% on Paderborn and ≤ 1.5% on
  IMS.
- **Paderborn detection: uninformative (floor effect).** At a 5% false-alarm threshold
  from healthy dev bearings, the frozen detectors catch only 1.4% (D-KURT) and 0.2%
  (D-ENV) of damaged held-out files **in the original data**.
  - So "no loss after compression" (Δ = 0.000 for every codec) says nothing.
  - These detectors are too weak for Paderborn's cross-bearing variation. A better
    detector cannot be tuned on held-out data, and **this question stays open.**
- **IMS alarm times (cases, held-out):**
  - Set 2 bearing 1 and set 3 bearing 3: every codec alarms within 0–4 snapshots of the
    original, or earlier. RMS and D-ENV alarms are identical.
  - **Changes in behaviour, not only delays:** on set 2 bearing 1, harmonic + residual
    triggers the D-KURT alarm **33 snapshots (5.5 h) earlier**. On dev set 1 bearing 3,
    the quantization codecs trigger it 58 snapshots earlier.
  - These are earlier alarms caused by the compression error, not detections the
    original missed. They count as non-inferior under the rule but would change an
    operator's alarm log.
  - SZ3, SPERR, zfp and tsa leave every held-out alarm unchanged (±1 snapshot).
- **CX triggers (held-out):** STA/LTA recall 0.98–0.99 and precision 0.99 at the
  operating points. The rule is ≥ 0.98, which was met on dev; on held-out, zfp is at
  0.979.

## H4: decomposition vs simple coding (common ε = quant-flac's operating point)

Bytes ratio [95% CI]; < 1 means the decomposition codec is smaller.

| Codec | vs best of quant-zstd/quant-flac | vs best of SZ3/zfp/SPERR |
|---|---|---|
| dwt-q, Paderborn / IMS / CX | 1.29 [1.27, 1.31] / 1.19 / 1.57 | 1.13 / 1.01 / 1.27 |
| harmonic, Paderborn / IMS / CX | 1.01 [1.00, 1.02] / 1.01 / 1.00 | 0.88 / 0.86 / 0.81 |
| tsa, Paderborn / IMS | 1.14 [1.13, 1.15] / 1.38 | 1.00 / 1.18 |

**Decomposition never wins against both** (the rule requires CI < 1 against both).

The harmonic predictor beats the scientific codecs only because simple quantization +
FLAC already does. The predictor itself adds nothing over FLAC's own linear prediction.

## H3: cascade and progressive access (CX earthquake windows; dev 2023, held-out 2024)

Envelope cross-correlation (XC) search over the same-station gallery, 1,427 held-out
queries:

| Method (chosen on dev) | Recall@10 vs exhaustive | Median \|Δd\| (km) | Bytes per query | Latency p50 / p90 (ms) |
|---|---|---|---|---|
| exhaustive, full FLAC decode | 1.000 | 11.43 | 1,141,549 | 40.1 / 45.7 |
| cascade: ST features → top-100 → FLAC decode → XC | 0.884 | 11.61 | 699,656 | 22.2 / 23.0 |
| progressive: ST → top-100 → first 6 of 7 dwt subbands → XC | 0.871 | 11.60 | 410,448 | 23.3 / 24.1 |
| *exploratory:* envelope index (precomputed envelopes stored as the retrieval representation) | **1.000** | **11.43** | **274,798** | **1.05 / 1.18** |

- Progressive / cascade bytes = **0.59**, which misses the ≤ 0.5 gate, so progressive
  access **does not help enough beyond a plain cascade.**
- Latency is dominated by envelope computation, not by I/O.
- The exploratory envelope index (not pre-registered) reproduces exhaustive results
  exactly at about 1/40 of the latency. **Storage and retrieval representations should
  differ.**
- The 2024 windows were the timing test's held-out set; H3 is a different question
  (disclosed).

## H6: triggered retention (CX held-out, 4 stations × 2 days)

- The STA/LTA trigger windows [on − 30 s, off + 60 s] cover **34%** of samples on these
  stations, which have many uncatalogued triggers (the seismic example measured 5–16
  unmatched episodes per hour).
- Kept raw (FLAC) plus ST features for everything = **0.58×** the bytes of full FLAC.
- **Catalogue coverage: 3/3** arrivals. That's too few to estimate evidence loss.
- Retention pays off only where triggers are rare. Its value depends on the workload and
  needs real condition-monitoring duty cycles.

## Costs (single thread, `costs.json`)

Million samples per second, encode / decode:

| Codec | Speed |
|---|---|
| FLAC | about 47–50 / 82–97 |
| quant + FLAC | about 50–53 / 88–91 |
| quant + zstd-19 | about 4–6 / 150–470 |
| SZ3 | 20–38 / 37–71 (includes the HDF5 container) |
| SPERR | 12–15 / 19–23 |
| dwt-q | 2.5–4.7 / 95–166 |
| zstd-19 lossless | about 2–4 / 110–220 |

zstd's encode cost is dominated by level 19. FLAC is cheap enough for synchronous
ingestion at any realistic condition-monitoring rate.

## Gate decisions (STEERING.md)

| Gate | Evidence | Decision |
|---|---|---|
| Stage 2: custom codec interface + wavelet codec | No decomposition or wavelet codec beats simple coding (H4) | **Do not build a wavelet codec.** Stage 2 becomes **"integrate a standard codec"**: store native integers (with scale and offset), FLAC or Parquet (DELTA/BSS + zstd) for lossless. Optionally a declared-ε scalar-quantization tier for older data. |
| Stage 3: progressive storage | 0.59× bytes vs cascade, gate ≤ 0.5×; the envelope index dominates | **Do not build progressive raw storage.** Instead, store a retrieval representation (e.g. envelopes or features) next to the compressed raw. |
| Stage 4: compaction planner | Stage 3 not reached | Not started. A retention policy (H6) is workload-dependent and needs real duty-cycle data first. |

## Recommendation per capability

| Capability | Recommendation |
|---|---|
| Integer sample storage + standard lossless codec | **Continue (highest value).** About 5× smaller than the current `float8[]` chunks (estimate), bit-exact, and cheap to encode. This is a storage-format change in the extension and needs its own reviewed PR. |
| Error-bounded lossy tier (scalar quantization) | **Narrow:** opt-in for aged data, with the declared ε and the original detector checks stored. It reached about 3× vs Parquet on bearings, but it can shift kurtosis alarms (IMS). |
| Wavelet / decomposition codecs | **Pause.** No size advantage after overhead. Not justified by these data. |
| Progressive reconstruction | **Pause.** Use a separate retrieval representation instead. |
| Triggered retention / compaction | **Pause until real duty-cycle data.** The estimate here is noisy and low-n. |
| Paderborn weak-fault detection under compression | **Open.** The frozen detectors are too weak to test it. It needs a validated detector chosen on dev bearings. |

## Limits

- One rig each for Paderborn and IMS.
- IMS ASCII rounding hides its ADC step.
- CX is seismic, not machine data.
- The detectors were chosen generically.
- The H6 event count is tiny.
- The comparison with current MorletDB storage spans reports.
- MGARD could not be installed.
- SPERR is a 2-D/3-D codec used on stacked 1-D rows.
- Timing was on one machine.
