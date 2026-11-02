# E7: screened exact envelope search — results

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at 45a7322 (2026-11-02), with one deviation recorded there: a
two-minute operator pause between two runs. Gates and timed runs on 2026-11-02, about 22:20–23:39.

- **Build:** `45a7322`, library SHA-256 `ccea56fd…`, on E4's development galleries, updated in place from
  SQL 0.3.1 to 0.3.3.
- **Queries:** E1's 100 development queries.
- **Raw results:** `results/{gate,run}_N*.json` and logs. Summary: `results/summary.json` (`analyze_e7.py`).

## Verdict

**Screened exact search is 3.2–3.7× faster than scoring every window at every gallery size, with
identical results.**

- **Correctness:** the screened path's results were bit-for-bit identical to the full path's and to the
  measured build's on 100/100 queries per gallery.
- **Product rule met:** the gate passed at N1–N3, and S(N) is "screened faster" in both configurations at
  every gallery. The screened path stays the default.
- **Against probes:** probe search is still faster at every size (secondary rule: "probes faster", all six
  runs). The gap is 1.6–2.3× at p95. Within this session it hardly grows with gallery size: 2.24 → 2.33 →
  2.35 with default workers.

| Gallery | Windows | Config | p95 X-PG (screened) | p95 X-FULL | p95 P-PG (probes) | S = X-FULL / X-PG [95% CI] | R = X-PG / P-PG [95% CI] |
|---|---|---|---|---|---|---|---|
| N1 | 234,762 | w8 (primary) | 138 ms | 516 ms | 88 ms | 3.73 [3.18, 4.31] | 1.57 [1.21, 1.90] |
| N1 | | default | 175 ms | 558 ms | 78 ms | 3.19 [2.92, 3.58] | 2.24 [2.01, 2.52] |
| N2 | 504,650 | w8 (primary) | 221 ms | 808 ms | 130 ms | 3.66 [3.33, 3.95] | 1.70 [1.33, 1.96] |
| N2 | | default | 279 ms | 922 ms | 119 ms | 3.31 [3.14, 3.55] | 2.33 [1.87, 2.72] |
| N3 | 1,026,010 | w8 (primary) | 540 ms | 1,928 ms | 237 ms | 3.57 [3.27, 3.77] | 2.27 [1.60, 2.99] |
| N3 | | default | 499 ms | 1,790 ms | 213 ms | 3.58 [3.52, 3.68] | 2.35 [1.66, 2.90] |

## Correctness

At N1, N2 and N3 the gate passed:

- **E3's gate:**
  - synthetic selection: 0 failures in 3,000 trials;
  - scorer and decode checks: largest distance difference 1.6 × 10⁻¹⁵;
  - matrix-arm lists: identical to in-database search, which is now screened, for 100/100 queries.
- **Identity with the measured build:** the screened list equalled E4's stored list in recording, start and
  distance, bit for bit, for **100/100 queries per gallery**.
- **Identity with the full path:** the full path's list in this build equalled the screened list, bit for
  bit, for **100/100 queries per gallery**.
- **Timed runs:**
  - no arm's list changed between rounds;
  - X-FULL's recall against X-PG was 1.0 on every query;
  - probe recall against exact search was 0.998, 0.990 and 0.993, the same as in E4 and E6.

## Latency, ms (p50 / p95 / p99, 200 timings per arm)

| Gallery | Config | X-PG | X-FULL | P-PG | X-MAT32R |
|---|---|---|---|---|---|
| N1 | default | 124 / 175 / 205 | 457 / 558 / 697 | 49 / 78 / 111 | 23 / 31 / 41 |
| N1 | w8 | 77 / 138 / 167 | 349 / 516 / 611 | 64 / 88 / 125 | 23 / 39 / 45 |
| N2 | default | 199 / 279 / 312 | 766 / 922 / 990 | 68 / 119 / 232 | 42 / 58 / 101 |
| N2 | w8 | 164 / 221 / 323 | 650 / 808 / 944 | 76 / 130 / 240 | 42 / 63 / 74 |
| N3 | default | 440 / 499 / 533 | 1,621 / 1,790 / 1,888 | 93 / 213 / 391 | 83 / 111 / 117 |
| N3 | w8 | 413 / 540 / 612 | 1,534 / 1,928 / 2,015 | 98 / 237 / 424 | 86 / 121 / 209 |

At N1 with eight workers, the median screened exact search (77 ms) is close to the median probe search
(64 ms).

The remaining gap to the materialized scan (X-PG / X-MAT32R at p95: 3.5–5.6×) is per-row work inside
PostgreSQL:
- reading the tuple;
- one aggregate call per row;
- each window's mean and spread, computed at query time.

## Across sessions (descriptive)

The machine was busier during this session than during E6. The desktop, a browser and a virtual machine
kept the 5-minute load average at 5–6 between runs. The same scorer and probe path were slower here:
- **full path (w8, p95):** X-FULL 516 / 808 / 1,928 ms, against E6's X-PG 277 / 575 / 1,551 ms;
- **probes (w8, p95):** P-PG 88 / 130 / 237 ms, against E6's 55 / 68 / 238 ms.

So only the within-session ratios above are comparisons. For reference, E7's screened p95 against E6's
exact p95 (w8) was 138 vs 277 ms at N1, 221 vs 575 ms at N2 and 540 vs 1,551 ms at N3. That is 2.0, 2.6
and 2.9× faster despite the busier machine.

## What follows

- **The default stays:** the screened path is the default in pg_morlet 0.3.3, and
  `morlet.exact_screen = off` keeps the full path.
- **Probes stay ahead:** exact search is now within about 2× of probe search up to one million windows, and
  probes remain the faster path at every measured size.
- **Separate decisions:** whether `morlet_search_similar` should use exact search on larger galleries (today
  above 5,000 eligible windows it uses probes when they are enabled), and whether `morlet_enable_envelopes`
  should set `parallel_workers`.
