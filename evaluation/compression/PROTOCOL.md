# Compression benchmark protocol (stage 1 of STEERING.md)

Status: **FROZEN** (in the commit that adds this file), before any benchmark run on real
data. Parameters that are "chosen on dev" follow the mechanical rules below. Thresholds
marked **(provisional)** are researcher-selected.

## Data, independent units and splits

| Dataset | Signal | Native samples | Unit | Development | Held-out |
|---|---|---|---|---|---|
| Paderborn KAt (CC BY-NC 4.0) | housing vibration, 64 kHz | int16 ADC counts (exact grid 100/32768) | bearing | 8 dev bearings (PR #6 split) | 23 held-out bearings |
| IMS/NASA run-to-failure (acknowledgement requested; no licence stated) | housing acceleration, 20 kHz, 1 s snapshots every 10 min | ASCII decimals on a 0.001 grid → int16 (exact for the distributed files) | bearing channel within a test | set 1 (bearings 1–4, x-axis channel) | sets 2 and 3 (bearings 1–4) |
| IPOC CX (open, doi:10.14470/PK615318) | broadband seismometer HHZ, 100 Hz | int32 counts (Steim2 at source) | station × period | 2023-03-01..03, PB01/PB02/PB06/PSGCX | 2024-03-01..03, same stations |

- **Blocks:**
  - Paderborn: the first 62 × 4096 samples of each accepted file (PR #6's frozen rate
    rule). Rate–distortion uses the first 2 accepted measurements per bearing × setting;
    detection (H2) uses all accepted files.
  - IMS: one snapshot (20,480 samples). Rate–distortion uses every 10th snapshot;
    alarm-time (H2) uses all snapshots of the failing bearings.
  - CX: 65,536-sample blocks within contiguous segments. Partial blocks are excluded and
    counted.
- **Known facts and limits:**
  - IMS set 3 is delivered as `4th_test/txt` with 6,324 files ending 2004-04-18, while the
    readme says 4,448 files ending 2004-04-04. All delivered files are used, and this is
    recorded.
  - The IMS ADC step (about 0.0024) is obscured by 3-decimal ASCII rounding.
  - Paderborn and IMS each come from one rig.

## Codecs (identical samples; real bytes; every decode is checked against the original)

**Lossless:**
- optimized Parquet (pyarrow): native integer column, the best of encodings
  {PLAIN, DELTA_BINARY_PACKED, BYTE_STREAM_SPLIT} × zstd levels {3, 9, 19}, chosen on dev
  by total bytes, one file per recording;
- int + zstd-19 (byte-shuffled);
- delta + zstd-19;
- FLAC level 8 (LPC + Rice);
- reversible integer CDF 5/3 lifting (6 levels) + zstd-19 per subband.

References:
- CX: native Steim2 miniSEED size;
- Paderborn: a naive float64 Parquet (zstd, byte-stream split).

**Error-bounded lossy** (absolute bound ε in counts; max error checked empirically):
- quant-zstd: uniform step 2ε + zstd;
- quant-flac: step 2ε + FLAC; this is the simple predictive / residual codec;
- dwt-q: db4 periodized DWT, 6 levels, coefficient step c·ε with c ∈ {1, 2, 4} chosen on
  dev, plus sparse corrections that enforce ε;
- harmonic-residual: the K strongest spectral lines (K ∈ {4, 16} chosen on dev) as a
  predictor, residual quant-flac;
- tsa-residual: a shaft-synchronous template at the nominal speed (Paderborn 900/1500 rpm;
  IMS 2000 rpm), residual quant-flac; not applicable to CX;
- SZ3 (absolute);
- zfp (fixed accuracy);
- SPERR (absolute; 1-D blocks stacked as rows of 4096, labelled as such).

**Unavailable:** MGARD. `pip install pymgard` and `pip install mgard` both failed on
macOS arm64 with Python 3.12; not silently omitted.

**Accounting:**
- **Payload:** codec bytes.
- **Archive:** payload plus a 64-byte block header (codec id and version, length, rate,
  start time, step, ε, CRC32) for codecs. Parquet and miniSEED use their real file sizes.
- Metadata common to all codecs (per-recording JSON) is excluded from both.
- Reported three ways: vs the raw sample payload (native int bytes), vs optimized
  Parquet, and vs float64.

**ε grid:** ε = r·σ with r ∈ {1/256, 1/128, 1/64, 1/32, 1/16, 1/8, 1/4, 1/2}. σ is the
median block SD of the dataset's dev blocks, and ε is floored at 0.5 counts (= lossless on
integer data).

## Fidelity metrics and named detectors (fixed now)

- **Waveform:** max |error|, NRMSE, Welch log-spectral distance (dB), envelope-spectrum
  log distance (dB), relative change of kurtosis and crest factor.
- **D-KURT:** kurtosis of the band-passed signal. Bands: Paderborn 2–20 kHz, IMS
  2–9 kHz, 4th-order Butterworth, zero-phase.
- **D-ENV:** max/median of the envelope spectrum (Hilbert of the same band) over the
  fault range. Paderborn 10–500 Hz, IMS 10–1000 Hz.
- **CX detector:** the seismic example's STA/LTA (2–10 Hz, STA 1 s, LTA 30 s, on 4,
  off 1.5). Trigger onsets are matched within ±1 s.

## Operating point (development data only)

For each lossy codec and dataset, the operating point is the largest ε on the grid such
that, over dev blocks:
- Paderborn and IMS: the 95th percentile of |relative change| of D-KURT and of D-ENV is
  each ≤ 0.05 **(provisional)**;
- CX: STA/LTA trigger precision and recall vs the original are both ≥ 0.98
  **(provisional)**.

If no grid value qualifies, the codec has no operating point (reported).

## Primary comparisons (held-out; bootstrap over units, 2000 resamples, seed 7)

- **H1 lossless:** the best lossless codec's total bytes / optimized Parquet bytes, per
  unit.
- **H1 lossy:** each lossy codec at its operating point vs optimized lossless Parquet.
- **Verdicts for H1:**
  - "Material" if the CI lower bound of the size reduction is ≥ 2× **(provisional, per
    STEERING.md)**;
  - "some gain" if > 1×;
  - "no gain" otherwise.
- **H2 Paderborn:** the change in detection sensitivity (decoded − original) at a 5% false
  alarm rate.
  - Thresholds are set on the dev healthy bearings' files of the *same* representation.
  - Sensitivity is the share of a held-out damaged bearing's files above the threshold,
    averaged over the 19 damaged held-out bearings.
  - Non-inferior if the CI lower bound is > −0.05 **(provisional)**, for both detectors.
  - Also reported: the weak-fault subset (extent 1), and false-alarm drift on the healthy
    held-out bearings under the original threshold.
- **H2 IMS:** alarm time for each failing bearing, as **cases (no CI)**.
  - Dev: set 1 bearings 3 and 4. Held-out: set 2 bearing 1 and set 3 bearing 3.
  - Threshold: the 99th percentile of the detector over the first 25% of the bearing's
    snapshots. The alarm is the first of 3 consecutive exceedances after that baseline.
  - Non-inferior if the decoded alarm is at most 6 snapshots (about 1 h) later than the
    original.
- **H2 CX:** trigger precision and recall on held-out days (descriptive).
- **H4:** at the dataset's common ε (the operating point of quant-flac), the bytes of
  dwt-q, harmonic-residual and tsa-residual relative to:
  (a) the best of quant-zstd/quant-flac;
  (b) the best of SZ3/zfp/SPERR.
  Decomposition "wins" if the ratio's CI upper bound is < 1.0 against both.
- **H3 cascade and progressive search** (CX event windows from the timing test; 2023
  dev, 2024 held-out):
  - Methods:
    - exhaustive envelope cross-correlation (XC) over the same-station gallery, with full
      lossless decode;
    - cascade: standardized ST features → top-N → full decode → XC → top-10;
    - progressive: the same, but decoding only the first k dwt-q subbands.
  - N ∈ {20, 50, 100} and k ∈ {2..7} are chosen on 2023 as the cheapest setting with
    recall@10 vs exhaustive ≥ 0.9 and median |Δd| within 1 km of exhaustive
    **(provisional)**.
  - Reported on 2024: bytes fetched/decoded per query, latency p50/p90, recall@10 vs
    exhaustive, median |Δd|.
  - Progressive "helps beyond cascade" if its bytes are ≤ 0.5× the cascade's at that
    quality.
  - Note: the 2024 windows were the held-out set of the timing test. That test compared
    representations; H3 asks a different question, which is disclosed.
- **H6 retention (CX held-out):**
  - The STA/LTA trigger windows [on − 30 s, off + 60 s] are kept as raw FLAC; ST features
    are kept for everything (51 float32 per 20 s).
  - Reported: the fraction of raw kept, and the coverage of ComCat M ≥ 2.5 events within
    250 km whose predicted P falls in the period.

## Costs

Encode and decode wall time per million samples, on one thread, on the stated hardware.

## Changelog

- 2026-10-26: frozen before any run.
