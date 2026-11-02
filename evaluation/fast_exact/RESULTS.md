# E6: a fast in-database exact scorer against probe search — results

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at 5e8342f (2026-11-02), with one deviation recorded there: a
system sleep and an operator error, both handled by rerunning N3. Timed runs on 2026-11-02, 07:40–07:49 (N1,
N2) and 09:48–09:59 (N3).

- **Build:** `68594d7` plus `e6-scorer.patch`, library SHA-256 `e242b744…`, on E4's development galleries.
- **Queries:** E1's 100 development queries.
- **Raw results:** `results/{gate,run}_N*.json`. Summary: `results/summary.json` (`analyze_e6.py`).
- **Library:** the measured library `3c786684…` was restored after the study.

## Verdict

**Probe search stayed faster than the fastest in-database exact search we could build, at every size.**

- **The exact path got faster:** distances stayed bitwise identical, and in-database exact search sped up
  1.6–2.7× at p95 over the measured build.
- **Probes still won:** they were 5–8.5× faster at p95, and every 95% interval lies well above 1.

| Gallery | Windows | Config | p95 X-PG (exact) | p95 P-PG (probes) | R = X-PG / P-PG | 95% interval | Rule |
|---|---|---|---|---|---|---|---|
| N1 | 234,762 | w8 (primary) | 277 ms | 55 ms | 5.06 | [4.90, 5.26] | probes faster |
| N1 | | default | 346 ms | 64 ms | 5.44 | [4.87, 5.73] | probes faster |
| N2 | 504,650 | w8 (primary) | 575 ms | 68 ms | 8.47 | [8.10, 8.84] | probes faster |
| N2 | | default | 658 ms | 110 ms | 5.98 | [4.47, 7.22] | probes faster |
| N3 | 1,026,010 | w8 (primary) | 1,551 ms | 238 ms | 6.52 | [4.81, 8.15] | probes faster |
| N3 | | default | 1,522 ms | 200 ms | 7.60 | [4.61, 9.72] | probes faster |

The reviewer's concern was that probes might solve a problem a better exact scorer would remove. It doesn't
hold up to one million windows on this machine: the faster scorer narrowed the gap but did not close it.

## Correctness

At N1, N2 and N3 the gate passed:
- **E3's gate:** synthetic selection tests, scorer and decode checks, and matrix-arm lists identical to
  in-database exact search for 100/100 queries.
- **Identity with the measured build:** for **100/100 queries per gallery**, the in-database exact list on
  the E6 library equalled E4's stored list in recording, start and distance, bit for bit.
- **Timed runs:** no arm's list changed between rounds. X-MAT32R kept recall 1.0. Probe recall against exact
  search was 0.998, 0.990 and 0.993, the same as in E4.

## Latency, ms (p50 / p95 / p99, 200 timings per arm)

| Gallery | Config | X-PG | P-PG | X-MAT32R |
|---|---|---|---|---|
| N1 | default | 331 / 346 / 385 | 37 / 64 / 84 | 20 / 22 / 31 |
| N1 | w8 | 237 / 277 / 296 | 43 / 55 / 59 | 21 / 30 / 33 |
| N2 | default | 580 / 658 / 695 | 56 / 110 / 217 | 40 / 44 / 47 |
| N2 | w8 | 511 / 575 / 620 | 57 / 68 / 76 | 41 / 52 / 57 |
| N3 | default | 1,251 / 1,522 / 1,771 | 84 / 200 / 474 | 78 / 101 / 125 |
| N3 | w8 | 1,262 / 1,551 / 1,708 | 96 / 238 / 384 | 81 / 109 / 162 |

**Faster than the measured build** (descriptive, across sessions). E4's X-PG p95 was 735, 1,248 and
2,403 ms on the same galleries, so E6's exact path is:

| Gallery | default | w8 |
|---|---|---|
| N1 | 2.1× faster | 2.7× faster |
| N2 | 1.9× faster | 2.2× faster |
| N3 | 1.6× faster | 1.5× faster |

**Gap to the materialized scan:** X-PG / X-MAT32R at p95 is still 9–16×. It is not closed by the scorer:

- the matrix arm runs float32 GEMM on the processor's matrix unit over a matrix decoded once;
- the in-database path decodes float16 and z-scores every row on every query, in float64, to keep its
  results bitwise identical;
- it computes on the vector units of 4–9 processes.

## Observations

- **Eight workers help less at larger galleries.** At N1 they cut exact p95 by 20%; at N3 they did not help
  (1,551 against 1,522 ms). For a table this size PostgreSQL already plans more workers by itself, and the
  machine has 6 performance cores.
- **Load averages during timing were 3.4–10.4, part of it our own load.** The w8 configuration itself runs
  nine PostgreSQL processes. The default and w8 runs gave the same verdict at every size.
- **Probe p95 varied between configurations** (N2: 110 against 68 ms) at the same recall, in line with the
  run-to-run variation E3 saw for probes. The exact path and the matrix arm were more stable.

## Limitations

- One machine, warm caches, one client, unfiltered top ten, development data.
- The exact scorer was constrained to bitwise-identical float64 results. A float32 screen with a rigorous
  bound and float64 re-scoring, as X-MAT32R does outside the database, could be faster in the database too,
  but needs a two-stage SQL plan. It was not built.
- Storing decoded envelopes, or a column layout without per-row decoding, was out of scope.

## What follows (not changed here)

- **Paper:** the speed claim can be stated against a faster in-database exact path. It stays at 5–8.5× at p95
  up to one million windows, with "when exact, when probes" unchanged: exact for small or selective
  galleries, probes for larger ones.
- **Product:** the faster scorer is a pure gain with identical results (same PR). Whether
  `morlet_enable_envelopes` should set `parallel_workers` is left open: it helped at 0.23 M windows and not
  at 1 M.
