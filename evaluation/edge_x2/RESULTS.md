# X2 results: MorletDB on a Raspberry Pi 3B+

Protocol frozen in [PROTOCOL.md](PROTOCOL.md) (commit `b927fa0`) before any benchmark-set measurement.
Deviations 1 (round 1 ran with a fan) and 2 (cool-down gate 55 → 60 °C) were recorded before the affected
results were read. Device: Raspberry Pi 3 Model B Plus (4 × Cortex-A53, 1 GB, 32 GB microSD, no active
cooling), Debian 11 64-bit, Docker 20.10.5, client on the same device. Container limits of X1's `e512`
(2 CPUs, 512 MB). Data: B3's benchmark set, 101,586,735 samples, 868 channels. Nine runs: five searchable,
four raw (`results/x2_e512_r*.json`, `results/x2_raw_e512_r*.json`), 2026-11-03 23:27 to 2026-11-04 09:00.

## Pass criteria (frozen; every run, including the excluded ones)

| # | Criterion | searchable (5 runs) | raw (4 runs) |
|---|---|---|---|
| 1 | no OOM kill, no restart, no failed statement | pass (Docker flag and cgroup `oom_kill` 0, 0 restarts, 0 errors) | pass |
| 2 | all 868 channels read back with their SHA-256 | pass (0 mismatches) | pass (0 mismatches) |
| 3 | search lists identical to X1's (`83bcd505…`) | pass (`83bcd505…` in all five) | — |

**Passed: both arms, every run.**

## Measurements

Median of the three fanless runs [range]: searchable rounds 3–5, raw rounds 2–4 (deviation 1). Query
latencies are p50 / p95 in ms.

| Measure | searchable | raw |
|---|---:|---:|
| Ingestion (durable) | **78.3k samples/s** [77.6k–80.0k] | **258k samples/s** [247k–259k] |
| Ack per acquisition, p50 | 9.5 s | 2.7 s |
| Envelope build (48,525 windows) | 315 s [292–329] | — |
| R1, 4,096 samples | 6.4 / 8.2 | 6.2 / 7.9 |
| R2, 128,000 samples | 36.1 / 46.1 | 35.2 / 46.9 |
| Statistics, 128,000 samples | 6.4 / 7.9 (chunk summaries) | 30.3 / 36.2 (decoded) |
| Overview of a whole channel | 5.8 / 7.3 | 55.8 / 65.0 (decoded) |
| Similarity search, top 10 | 613 / 637 | — |
| `docker stats` peak / `anon` at the end | 121 / 15.4 MiB | 115 / 15.1 MiB |
| cgroup `memory.peak` (includes page cache) | 302 MiB [300–385] | 241 MiB [183–256] |
| Database size | 345 MiB | 162 MiB |
| Device: max temperature; ARM clock median | 70.9 °C; 1.2 GHz | 68.8 °C; 1.2 GHz |
| Throttle flags (OR over all phases) | `0x80008` | `0x80008` |

Throttle flags: `0x80008` is the soft temperature limit, active (bit 3) and having occurred (bit 19). No
undervoltage (bits 0 and 16) and no frequency cap or hard throttling (bits 1, 2, 17, 18) in any run. The
clock ran at the soft limit's 1.2 GHz for the whole of every fanless run; single samples down to 900 MHz
come from the frequency governor.

Excluded from the medians, reported as recorded:

| Run | Cooling | Ingestion | Envelope build | Search p50 | Max temperature, clock median |
|---|---|---:|---:|---:|---|
| searchable r1 | fan | 93.8k samples/s | 267 s | 529 ms | 41.3 °C, 1.4 GHz |
| searchable r2 | mixed (fan off at start) | 80.4k | 334 s | 609 ms | 67.7 °C, 1.2 GHz |
| raw r1 | fan | 260k | — | — | 40.2 °C, 1.4 GHz |

## Reading

- **What fits:** the whole workflow (durable ingestion with search features and chunk summaries, envelope
  build, bit-exact read-back of all 868 channels, reads, statistics, overviews, similarity search) ran on a
  1 GB Raspberry Pi 3B+ with a microSD card and no fan, inside 512 MB, with the client on the same board.
  Server anonymous memory stayed at about 15 MiB; the rest of the cgroup's peak is page cache.
- **How fast, in sensor terms:** 78k samples/s searchable is about 3 channels sampled continuously at
  25.6 kHz; 258k samples/s as plain lossless storage is about 10. Periodic acquisitions (a few seconds every
  few minutes, as common in condition monitoring) need a fraction of that.
- **Searchable costs 3.3× the ingestion rate of raw** on this device: the feature and summary computation is
  CPU-bound at 1.2 GHz. In return, statistics over 128,000 samples are 4.8× faster and whole-channel
  overviews 9.6× faster, because they read chunk summaries instead of decoding samples.
- **Compared with X1 on Apple M5 cores** (same image and limits, X1's `e512` medians): searchable ingestion
  20× slower (1.60M → 78.3k samples/s), envelope build 24× (13.1 → 315 s), similarity search 21×
  (29.4 → 613 ms), R1 reads 10× (0.62 → 6.4 ms). X1 had no raw arm.
- **Search gave bitwise-identical result lists on Cortex-A53 and Apple M5** (`83bcd505…`).
- **Cooling matters, within limits:** with the fan, searchable ingestion was 20% faster (93.8k) and search
  14% faster; raw ingestion barely changed (260k vs 258k).
- **Database size:** 345 MiB on the Pi against 411 MiB reported by X1 for the same data and image. Size was
  not a criterion and the difference was not investigated; it is not used for any claim.
- **Field note:** pyarrow 21's arm64 wheel does not run on Cortex-A53 (ARMv8.0); the client used pyarrow 17.

## Claim supported

"Runs the full workflow on a Raspberry Pi 3B+ (1 GB RAM, microSD, no fan) within 512 MB, every sample
returned bit for bit: 78k samples/s with search features, 258k samples/s as plain lossless storage. Search
returns the same results as on a desktop." Stated for this device, OS and cooling. No statement about other
devices, long-term operation, power loss or industrial use.
