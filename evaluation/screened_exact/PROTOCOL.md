# E7: screened exact envelope search — protocol (FROZEN)

Frozen 2026-11-02, before any timed E7 run. Development pilots on a copy of `morlet_r2` (listed below) were
run while building the screen; they are not results. Deviations are appended below.

## Question

E6 made the in-database exact scorer as fast as it could be while scoring every window. Probe search stayed
5–8.5× faster at p95. E3's materialized scan showed that a float32 screen with a rigorous error bound, followed
by exact re-scoring of the few windows that could still be among the nearest, does the same work much faster.
**Inside PostgreSQL, with the same results, is screened exact search faster than the full exact path, and how
far is it from probe search, on galleries of 0.23, 0.50 and 1.03 M windows?**

## The change under test (pg_morlet 0.3.3, branch `perf/block-envelopes`)

- `morlet_core::envelope_screen` screens each stored envelope. It decodes float16 to float32 exactly. It takes
  one fused-multiply-add pass of the raw values against the float32 shifted query for all 61 lags. It then
  corrects for the window's mean and standard deviation in float64.
- **The bound.** Each screened distance carries a rigorous bound
  `δ = (γₙ+u)·‖q‖·‖g‖/(sd·n) + κ-terms + 1e-9`. Here `u = 2⁻²⁴` and `γₙ = n·u/(1−n·u)`. The κ-terms
  (κ = 1e-12) cover the screen's moments differing from `decode`'s. Windows with a tiny standard deviation
  get an infinite bound and are always re-scored.
- **What is kept.** The kept set is every window whose lower bound is at most the m-th smallest upper bound.
  It is a superset of the m nearest windows, ties included.
- **The SQL.** A parallel aggregate `morlet_envelope_screen` (with combine, serial and deserial functions) runs
  over the envelope table's rows, under the same eligibility and exclusion predicates as the full path.
  `morlet_envelope_ranked` re-scores the kept windows with `morlet_envelope_distance`. It sorts by
  `(distance, recording_id, start_sample)`, cuts at m, suppresses overlaps and widens m ×4 when needed,
  unchanged.
- **The candidate count.** The same scan counts eligible windows for the `max_candidates` check, which raises
  the same error before any row is returned.
- **The switch.** `SET morlet.exact_screen = off` selects the full path (E6's scorer over every row) in the
  same build.
- **Unit tests.**
  - Exact distances are bitwise equal to `envelope::distance`, and the bound holds, at lags 0, 5 and 30.
  - The paired and single kernels agree.
  - The bound holds for degenerate and offset windows, and degenerate windows are always kept.
  - Split and merged screeners keep the m nearest.
  - Hardware and table float16 conversion agree on every finite code.

Build: the commit that freezes this protocol, installed with `cargo pgrx install --release`. The galleries'
extension is updated in place from 0.3.1 to 0.3.3 (`ALTER EXTENSION pg_morlet UPDATE`), which adds functions and
replaces `morlet_envelope_ranked`. No data, index or table is rebuilt. The library SHA-256 is recorded in
the results.

## Data and queries

E4's three nested development galleries: N1 `morlet_r2` (234,762 windows), N2 `morlet_e4_30` (504,650) and
N3 `morlet_e4_61` (1,026,010). E1's 100 development queries. No held-out data: the screen has no fitted
parameters (its constants are analytic), so there is nothing to tune and nothing to confirm on held-out data.

## Arms (E3's harness via E6's runner, `run_e7.py`)

| Arm | What |
|---|---|
| X-PG | product exact search (`morlet_search_envelope_interval`), screened (the 0.3.3 default) |
| X-FULL | the same with `morlet.exact_screen = off` (E6's path in this build) |
| P-PG | product probe search (13 probes × 50, HNSW, re-ranked with the same scorer) |
| X-MAT32R | E3's materialized float32-screen / float64 re-score scan, outside the database (reference) |

All in-database arms use `max_parallel_workers_per_gather = 8`, as in E1–E6. There are two configurations
per gallery, each a separate process: **default** (the planner's worker count for the table) and **w8** (the
envelope table's `parallel_workers` set to 8 for the run, then reset). N3 uses `max_candidates = 2000000`,
as in E4 and E6.

## Correctness gate (per gallery, before its timing)

1. E3's gate (synthetic selection, scorer and decode checks). X-MAT64 and X-MAT32R lists must be identical
   to in-database exact search, which is now the screened path, for 100/100 queries.
2. **Identity with the measured build.** For all 100 queries, the screened list equals E4's stored list
   (`evaluation/scaling_crossover/results/gate_N*.json`) in recording, start and distance, bit for bit.
3. **Identity with the full path.** For all 100 queries, the full path's list in this build equals the
   screened list, bit for bit.

A failure stops that gallery's timing until it is explained. A failure of 2 or 3 means the screened path
does not ship as the default.

## Procedure

Run order: N1 default, N1 w8, N2 default, N2 w8, N3 default, N3 w8. Each runs directly after its gallery's
gate.

Conditions for every run:
- AC power, under `caffeinate -ims`, from a detached process;
- no load, index build or other study running;
- each run starts once the 5-minute load average is below 5.

Timing uses E3's warm-up and two interleaved rounds per run.

## Decision rules

1. **Primary, per gallery (w8 run):** S(N) = p95(X-FULL) / p95(X-PG), with a 95% bootstrap interval over
   queries (10,000 resamples, seed 20261101). The rule is "screened faster" if the whole interval is > 1,
   "screened slower" if it is < 1, and "tie" otherwise.
2. **Product rule:** the screened path stays the default only if the gate passes at N1–N3 and S(N) is
   "screened faster" in both configurations at every gallery. Otherwise the default becomes off and the
   switch stays.
3. **Secondary:** R(N) = p95(X-PG) / p95(P-PG), with the same interval and E6's rule ("exact faster",
   "probes faster" or "tie"), in both configurations. Also p95(X-PG) / p95(X-MAT32R).
4. **Descriptive:** E6's X-PG p95 (measured 2026-11-02, same machine, harness and galleries) against E7's
   X-FULL and X-PG, labelled as across sessions.

## What follows

- **If screened exact search ties with or beats probes at some N,** the docs and paper state that exact
  search is competitive there.
- **Otherwise,** they state the measured gap.
- **Either way,** the product PR carries this study's result. Whether `morlet_enable_envelopes` should set
  `parallel_workers` is a separate decision.

## Budget and stop

About two hours of measurement, no new data, no disk beyond results. Stop after E7.

## Development pilots (before freezing; copy of N1, 20 random windows, one warm timing each)

These are not results. The full path is the same build with the switch off.

| Workers | Screened p50 | Full p50 | Probes p50 | Identical |
|---|---|---|---|---|
| default (3) | 173 ms | 598 ms | — | 20/20 |
| 8 | 100 ms | 359 ms | 32 ms | 20/20 |

## Deviations

1. **Operator pause between N3 default and N3 w8 (no measurement affected).** The operator asked to pause so
   the machine could rest. The runner was stopped (SIGSTOP) at 23:24:01, after N3 default had finished and
   while it waited for the load condition. It was resumed (SIGCONT) at 23:26:01. The machine did not sleep;
   the power log shows no sleep or wake event. N3 w8 then started under the protocol's load condition as
   usual.
