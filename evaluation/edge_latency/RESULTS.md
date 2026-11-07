# Edge search latency: exact search on a Raspberry Pi 3B+

**Result.** Exact envelope search over N1's gallery now takes **1.5 s per query** on a Raspberry Pi 3B+, down from
15.1 s. On the desktop it is unchanged, at about 0.15 s. Results are the same: the Pi now takes a different code path,
and its full N1 rerun returns the desktop's ids and distance bits (below).

The cause was the size of prepared query matrices against the CPU cache, not the CPU itself.

## What was slow

N1 (`evaluation/nilm_n1`) found that a median query took 14.9 s on the Pi and 166 ms on the desktop. That is about 90×,
more than the CPU gap alone. `profile.py` loads the same 1,341 windows (16,384 samples at 30 kHz, envelopes of 1,639
values, max lag 122) and times the parts of one search for six fixed queries, three repetitions each, reporting medians:

| Part (median s) | Desktop, before | Pi, before | Pi, after | Desktop, after |
|---|---:|---:|---:|---:|
| `morlet_search_envelopes` as N1 called it | 0.167 | 15.09 | **1.52** | 0.148 |
| … with `top_k = 5` | 0.119 | 10.26 | 1.51 | 0.107 |
| … with the screen off | 0.182 | 15.70 | 1.52 | 0.154 |
| … with no parallel workers | 0.173 | 15.06 | 1.51 | 0.149 |
| exact distance of all 1,341 windows | 0.161 | 15.39 | **1.25** | 0.135 |
| screening aggregate over all windows | 0.076 | 7.82 | 5.47 ¹ | 0.065 |
| query envelope alone | 0.017 | 0.27 | 0.26 | 0.021 |
| sending the 16,384 query samples | 0.010 | 0.24 | 0.24 | 0.010 |

¹ No longer used on the Pi for this window size (see Fix); measured by calling the aggregate directly.

- **Scoring was the cost.** Exact scoring took 11.5 ms per window on the Pi against 0.12 ms on the desktop.
- **The prepared query was the reason.**
  - `LagQuery` prepares the query as a matrix of every shifted copy: 1,639 rows × 256 lags in float64, 3.36 MB.
  - Every stored window streams the whole matrix.
  - The desktop's 16 MB cache holds it. The Pi's 512 KB L2 does not, so every window read 3.36 MB from memory, about
    4.5 GB per query.
  - The screen's float32 matrix (1.68 MB) had the same problem: 5.8 ms per window, more than direct exact scoring.
- **Not the cause:** parallel workers (none are used for a gallery this small), the envelope transform, and sending the query.

The direct form (`envelope::distance`) computes the same lagged sums in the same order from two 13 KB arrays.
`examples/envelope_score_bench.rs` times both forms per stored window and asserts bitwise equality:

| Window | Desktop: prepared / direct | Pi 3B+: prepared / direct |
|---|---:|---:|
| 4,096 (matrix 0.21 MB) | 3.1 / 7.8 µs | 112.6 / 81.5 µs |
| 16,384 (matrix 3.36 MB) | 73.8 / 84.7 µs | 11,899 / 790 µs |

## Fix

- **One budget per machine.** Each backend reads its machine's cache size once:
  - Linux: the largest `/sys/devices/system/cpu/cpu0/cache/index*/size`.
  - macOS: `hw.perflevel0.l2cachesize`.
- **A prepared matrix is used only if it fits a quarter of that cache.**
  - Exact scoring (`morlet_envelope_distance`) prepares `LagQuery` only within the budget, else it scores directly.
  - Screening (`morlet_envelope_screen_supported`) applies only within the budget, else exact search scores every
    eligible window directly.
- **Fallback:** when the size cannot be read, as in Docker Desktop's VM, which shows cache levels without sizes, the
  budget is 4 MiB, the same as before.
- **Override:** `MORLET_SCORE_CACHE_BYTES` in the server environment.

| Machine | Cache read | Budget | 4,096 windows | 16,384 windows |
|---|---|---:|---|---|
| Raspberry Pi 3B+ | 512 KB (L2) | 128 KB | screened (NEON), direct scoring | not screened, direct scoring |
| Apple M (macOS) | 16 MB (P-cluster L2) | 4 MB | as before | as before |
| Docker Desktop VM | none | 4 MB (fallback) | as before | as before |

Only speed depends on the budget. Both exact forms give bitwise the same distance, which is tested in core
(`lag_query_distances_are_bitwise_identical`) and by the bench. Screened and full exact search return the same results,
which the search contract tests check. No SQL changes.

## Check on the device

N1's full search arm, all 1,393 queries, was rerun on the Pi with an image of this change: `results/pi_after_n1.json.gz`.
Its ids and distance bits are compared with the desktop's run from N1 (`evaluation/nilm_n1/results/m_desktop.json.gz`).

**Result: identical on all 1,393 queries**, with identical stored bytes.

- Latency on the Pi: median **1.54 s**, p95 **1.59 s**, against 14.9 s and 23.9 s in N1.
- Ingest took 700 s (657 s in N1), and the Pi ended at 66.6 °C with the soft temperature limit active (`0x80008`).

## Run

- **Images:** `morletdb:n1` (main 78811bd) for "before" and `morletdb:lat` (this change) for "after", both built with
  `deploy/Dockerfile`, linux/arm64.
- **Machines:**
  - Desktop: Apple M-series, client on the host, with a load average of 2.6–5.2.
  - Raspberry Pi 3B+: passively cooled at 61–65 °C with the soft temperature limit active (`throttled=0x80008`,
    1.2 GHz), the load being the run itself.
- **Files:** `results/{desktop,pi}_{before,after}.json`, with machine names replaced by generic labels.
- **Scope:** these are latencies, not a benchmark claim; there is one run per configuration.
