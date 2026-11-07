# N1 results: appliance identification from turn-on transients (PLAID, leave one location out)

**Results.** H1 PASS: MorletDB's exact envelope search names the appliance about as well as a current-feature
1-NN on the same split. H2 FAIL: the distance to the nearest stored event does not flag unknown appliances.
H3 PASS: a Raspberry Pi 3B+ returns exactly the desktop's results, bit for bit, on all 1,393 queries.

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen before any computation, with Amendment 1 (storage hint, before
any result). Issue #293.

## Run

- **Data:** PLAID 2018 (figshare 10084619 v2, CC BY 4.0), hashes as pinned in `fetch_plaid.py`, through
  `n1.py windows`. The windows file (`windows.npz`, float64) has SHA-256
  `814457a2d87d5ea59c0cc12f36e83dcbb215a90c5877369fd600697727f284ca`. Every machine searched this file.
  - 1341 primary records (11 types, 56 locations) and 52 unknown records (5 types, `CMU lab`).
  - 59 records have no clear onset (m − b < 0.05 A) and were kept with their window at sample 0, as the
    protocol says: Laptop 25, Fridge 16, Incandescent 6, Hairdryer 3, AC 2, CFL 2, Heater 2,
    Soldering Iron 2, Washing Machine 1.
- **Code:** harness at `10d4ef6` (this change). The only change since the freeze is the cleanup of sibling tables:
  a smoke run had left `n1_segments`, and the first desktop attempt stopped there, before any search.
- **Image:** `morletdb:n1`, built with `deploy/Dockerfile` from main `78811bd` (pg_morlet 0.3.6,
  PostgreSQL 17.6, linux/arm64). Its 17 layer digests are identical on both machines.
- **Desktop:** Apple M-series. The client ran on the macOS host, the database in Docker. The load average
  was 3.7–4.9 during the run, so latencies are an upper bound.
- **Pi:** Raspberry Pi 3B+ (the X2 device), passively cooled; 58 °C at the start (its normal operating
  state). It ended at 63.4 °C with `throttled=0x80008`: the soft temperature limit was active, so the ARM clock ran
  at 1.2 GHz instead of 1.4 GHz. That is also its normal passively cooled state. The load average was 0.4 before and 1.0
  during the run, which was the run itself. Swap was nearly full (93 of 99 MB) towards the end. Ingest took 656.6 s,
  against 44.6 s on the desktop. Stored bytes were identical: chunks 23,370,370 and envelopes 4,397,139.

## Identification (H1)

Macro F1 over the 11 types, all 1341 predictions pooled. The 95% CI is a bootstrap over the 56 locations
(2000 resamples, seed 7).

| Method | Macro F1 [95% CI] | Accuracy |
|---|---|---:|
| **M: MorletDB exact envelope search, current only** | **0.733** [0.692, 0.770] | 80.4% |
| B1: 8 current features + 1-NN | 0.741 [0.695, 0.784] | 80.8% |
| B2: B1 + voltage features (descriptive) | 0.816 [0.763, 0.856] | 85.5% |

- **Δ = M − B1 = −0.008 [−0.069, 0.053] → H1 PASS.** The point estimate is within 0.05, and the CI lower
  bound is above −0.10.
- **k = 5, descriptive:**

  | Method | Macro F1 | Accuracy |
  |---|---:|---:|
  | M | 0.723 | 79.9% |
  | B1 | 0.712 | 79.5% |
  | B2 | 0.793 | 84.0% |
- **Published numbers on PLAID, for context only:** they use other protocols, and in part PLAID 2017.
  - De Baets et al. 2018: V–I images with a CNN, macro F1 0.776, leave one house out.
  - 1-NN on current features: 75.98% accuracy, leave one house out (review table, arXiv 2102.04808).

  M's 0.733 / 80.4% is in the same range, without any training.

**Per type (F1):**

| Type | M | B1 | B2 |
|---|---:|---:|---:|
| Compact Fluorescent Lamp | 0.98 | 0.95 | 0.97 |
| Vacuum | 0.98 | 0.92 | 0.95 |
| Microwave | 0.95 | 0.93 | 0.96 |
| Incandescent Light Bulb | 0.92 | 0.83 | 0.84 |
| Laptop | 0.90 | 0.90 | 0.93 |
| Hairdryer | 0.75 | 0.86 | 0.91 |
| Fridge | 0.66 | 0.63 | 0.70 |
| Washing Machine | 0.64 | 0.44 | 0.74 |
| Fan | 0.57 | 0.68 | 0.66 |
| Heater | 0.40 | 0.37 | 0.64 |
| Air Conditioner | 0.31 | 0.65 | 0.68 |

**Where M loses.** Its most frequent confusions are:

| Confusion | Count |
|---|---:|
| Fan → AC | 28 |
| AC → Fan | 20 |
| Heater → Hairdryer | 15 |
| Fan → Hairdryer | 14 |
| AC → Hairdryer | 13 |
| Fan → Heater | 12 |
| Washing machine → Fridge | 11 |

These are pairs whose turn-on envelopes have a similar shape but a different level: motors of
different size, and resistive heaters. M z-scores the envelope, so it **ignores the absolute current**.
The protocol named this limitation in advance. M does best where the shape of the inrush is
characteristic: lamps, vacuum, microwave, washing machine. That makes M and the level features
complementary. Combining them is a separate question and was not tested here.

## Unknown appliances (H2)

- **AUROC = 0.326 [0.247, 0.409] → H2 FAIL.** The score is the nearest distance, unknown = positive; the
  bootstrap is over records.
- **Unknowns are closer to a stored event than known appliances are.** An AUROC below 0.5 means the
  rule works backwards on this data.
  - **Likely reason, not tested:** the unknowns (kettle, coffee maker, hair iron, soldering iron,
    blender) are mostly resistive loads. A resistive load's z-scored turn-on envelope is close to a clean
    step, like a heater, an incandescent bulb or a hairdryer.
  - **Limitation stated in the protocol:** all unknowns come from one lab.
- **Conclusion:** distance to the nearest z-scored envelope is **not** a usable novelty score for
  appliances. Detecting unknown appliances needs features that carry level, or a learned space.

## Storage and speed (descriptive)

- **Storage of the 1341 current windows** (21.97M samples, float64 values off any fixed grid):

  | | Size | Per sample | vs float32 |
  |---|---:|---:|---:|
  | MorletDB chunks, bitwise lossless | 23.4 MB | 1.06 B | 27% (87.9 MB) |
  | Envelopes | 4.4 MB | – | – |
- **Ingest:** import, envelope build and bitwise read-back of every record took 44.6 s on the desktop.
- **Query latency** over the 1341 stored windows, including the client sending 16,384 samples:

  | Machine | Median | p95 |
  |---|---:|---:|
  | Desktop | 166 ms | 263 ms |
  | Pi 3B+ | 14.9 s | 23.9 s |

## Edge (H3)

**H3 PASS.**
- Every query's returned recording ids and the bits of every distance (`results/m_pi.json.gz`) are identical to the desktop's
  (`results/m_desktop.json.gz`). That covers all 1,393 queries: 1,341 known and 52 unknown.
- The Pi used the same windows file (SHA-256 above) and an image with the same 17 layer digests.
- So the macro F1, Δ and AUROC above hold unchanged on the edge device.

**Latency on the Pi is a finding, not a pass/fail.** A query takes about 90× longer than on the desktop (median 14.9 s
against 166 ms). That is more than the CPU difference alone explains. The search scores 1,341 windows of 16,384 samples
exactly, single-threaded. Profiling this is the next step: the screened path, swap, and the cost per candidate.

## What this decides (as the protocol stated)

- **H1 PASS:** z-scored envelope search is a credible retrieval method for turn-on transients. It
  matches a feature 1-NN without training and without voltage.
- **Next step:** the V–I arm on synchronous channels (#292).
- **Already clear from B2 and the confusions:** level information (current magnitude, real power) adds
  about 8 points of macro F1. A V–I arm should keep it.
- **H2 FAIL:** MorletDB should not claim unknown-appliance detection from envelope distance.

Files: `results/results.json` (all metrics), `results/m_desktop.json.gz` and `results/m_pi.json.gz` (every
query's returned ids and distance bits).
