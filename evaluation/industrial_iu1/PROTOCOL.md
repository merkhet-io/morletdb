# IU1: bounded-lag envelope search on CNC milling vibration (FROZEN 2026-11-11)

Frozen before any MorletDB data was loaded. Written by the assistant for Semir Maslo (Sulzer-BDiM). Deviations are appended
at the end, never edited in place. Harness: `iu1.py` in this folder. One run per task. No tuning on M02 or M03.

## Data

Bosch CNC Machining (Tnani, Feil, Diepold 2022; github.com/boschresearch/CNC_Machining, data CC BY 4.0),
clone of 2026-11-11. 1,702 runs (one h5 file each), three machines M01 to M03, operations OP00 to OP14, label
good (1,632) or bad (70) per run, tri-axial acceleration in integer counts at 2 kHz, about 24.8 h per axis.

- **Development:** M01 only (519 runs). Used to check that the harness runs and to fix nothing else; the window
  size, lag and every decision rule below are fixed now.
- **Confirmation:** M02 and M03 (1,183 runs, 36 bad).

Each run and axis is one MorletDB recording: rate 2000 Hz, channel `acc_x|acc_y|acc_z`, unit `counts`,
extractor `none`, metadata `{"machine","op","period","label","axis","run"}`. Envelopes at window 4096 samples
(2.05 s), stride 2048, the product defaults. Bounded lag is the product default `max_lag_fraction = 0.075`
(BL); zero lag is the same scorer with `max_lag_fraction = 0` (ZL).

## Query windows

Per run and axis, up to 8 query windows of 4096 samples at envelope-grid starts (multiples of 2048), evenly
spaced over the run, chosen deterministically (no randomness). Hits from the query's own run are removed
client-side (the product only excludes overlapping windows of the source); the search asks for top 40 and
keeps the first 10 hits from other runs.

## Tasks and decision rules

**T1 Retrieval relevance (F1).** Query windows of every bad run. Gallery: same machine, operation and axis
(metadata filter). Relevant hit: a window from another bad run. Score per run: mean P@10 over its query
windows and axes. Arms: BL, ZL, RMS (nearest windows by absolute RMS difference, computed client-side from
`morlet_interval_stats`), chance (share of bad-run windows in the gallery). Decision on confirmation data:
"BL better than ZL" if the 95% run-bootstrap interval (10,000 resamples, seed 20261111) of the mean
difference lies above 0, "worse" if below, else "no difference". Same rule for BL against RMS.
Reported for both, with chance level.

**T2 Run scoring against good history.** Every run of the confirmation machines.
Gallery: good runs of the same machine, operation and axis, without the query's own run (filter adds
`"label":"good"`). Run score: median over query windows and axes of the nearest-hit distance. AUROC of bad
against good per arm (BL, ZL, RMS-kNN). Decision: "BL better than ZL" if the 95% run-bootstrap interval of the
AUROC difference lies above 0, analogous otherwise. AUROC above 0.5 with its interval is reported per arm.

**T3 Lag use (F2).** For T1 queries: overlap of BL and ZL top 10 (share of identical hits) and the
distribution of BL distance improvement over ZL for the BL top 1. Descriptive, no decision.

**T4 Probes against exact (F3).** Axis `acc_x`, all three machines, one unfiltered gallery (about 87,000
windows). pgvector enabled, `morlet_enable_envelope_probes(4096)`, product defaults (13 probes, 50 per
probe). 100 query windows drawn with seed 20261111 from all windows. Recall@10 of probe search against
exact search and p50/p95 latency of both, two interleaved rounds, warm cache. Decision: "probes hold" if mean
recall@10 >= 0.95 and probe p95 <= exact p95 / 2. Reported with the gallery size; this gallery is smaller than
the seismic ones in the paper.

**T5 Time warping (F4).** 100 good query windows from M02 and M03 (seed 20261111). Each query interval is
resampled (linear interpolation) so that 4096·(1+s) original samples become 4096 query samples, for
s in {0, 0.01, 0.02, 0.05, 0.10}, and searched by samples with `morlet_search_envelopes` in the gallery of
the same machine, operation and axis. Target: the query's own source interval (any hit overlapping it by at
least half). Reported per s and arm: share of queries with the target in the top 10, median target distance.
Descriptive; the expected outcome is that BL tolerates small stretches and fails for larger ones.

**T6 Cross-machine process recognition (F5).** 200 good query windows from M02 and M03 (seed 20261111),
axis-only filter, hits from the query's own machine removed client-side (top 100 requested). Score: share of
the top 10 from the same operation on another machine. Chance: share of that operation's windows among the
other machines' windows. Arms BL and ZL; decision as T1 with a query bootstrap.

## Environment

MorletDB main at 088f03b (2026-11-10, SQL 0.3.6, release 0.3.10-alpha.1) built from `compose.yaml` with
pgvector added; PostgreSQL 17.6 in Docker Desktop (WSL2) on a Windows 11 laptop, 16 logical CPUs and 7.2 GB
memory for Docker, port bound to 127.0.0.1. Machine load is recorded. Latencies here are not comparable with
the paper's Apple M5 Max numbers.

## Not claimed

Diagnosis, fault causes, real-time sorting, synchronized multichannel search, any domain beyond this dataset.
Bad labels are per run; a bad run may contain normal windows, which lowers every arm's P@10 alike.

## Deviations

1. 2026-11-11, before the full load. The built extension reports SQL version 0.3.8 (not 0.3.6 as written above),
   envelope version `xc-envelope-f16-v1`; the commit is unchanged (088f03b).
2. 2026-11-11, before the full load. File numbers restart per label folder, so 61 file names exist both as good
   and as bad runs. The run identifier is the file name plus the label (`M01_Aug_2019_OP05_000_bad`).
3. 2026-11-11, before the full load. `morlet_append_chunks_raw` accepts at most 262,144 samples per call; the
   loader appends in batches of that size.
4. A smoke run on 12 runs of M01 OP05 checked the harness. Its numbers are discarded and the database was
   recreated before the full load.

## Addendum A1: drift checks (written 2026-11-11, after IU1 results were seen)

Not part of the frozen protocol. Requested by Z. Nam after the half-year figure: 20 of the 36 bad confirmation
runs come from M02, Feb 2019, where distances are highest, and M02 rises in Aug 2020 without bad runs. The T2
AUROC may partly measure drift between half-years instead of "bad". Known before running, from file counts only:
M02 Feb 2019 has 26 good runs, 1 to 3 per operation; its two bad OP10 runs last 42.0 s against 49.2 s for the
good one. The rules below were written before any of these checks was computed. Harness `drift.py`, decisions by
`drift_analyze.py`. The dataset gives only the half-year of a run; the order inside a half-year is not documented.

Per query window (same windows as T2) and arm (BL, ZL, RMS): nearest distance to good runs of the same machine,
operation and axis, own run removed, **separately for every half-year of the history**. From these:

- **H-all:** minimum over all half-years (must reproduce T2).
- **H-same:** history from the query's own half-year only. Removes drift between half-years.
- **H-past:** history from earlier half-years only. Strictly causal at the resolution the data allows; runs of a
  machine's first half-year have no history and drop out.
- **H-notfuture:** history from the own and earlier half-years.

Run score as in T2 (median over windows and axes). Reported with 95% run-bootstrap intervals (10,000 resamples,
seed 20261111, stratified by label):

1. AUROC for each history, all confirmation runs.
2. AUROC without the runs of M02 Feb 2019 (and, for H-all, also without that half-year in the history).
3. AUROC inside each machine and half-year that has at least one bad and one good run, and a pooled
   within-group AUROC (pairs of one bad and one good run from the same machine and half-year only).
4. History size: good runs per machine, operation and half-year; run score against history size for good runs.
5. Duration baseline: absolute deviation of run length from the median length of the good history
   (H-all and H-same), as a score. Added because duration is visible in the file sizes.
6. T1 with the gallery and the chance level restricted to the query's own half-year.

Reading rule, fixed now: the T2 claim "separates bad from good runs" stands only if the pooled within-group
AUROC of BL under H-same has a 95% interval above 0.5. "BL better than ZL" stands only if the interval of the
difference under H-same lies above 0. Otherwise the section reports that the original AUROC is confounded with
the half-year.

## Addendum A2: lead-in check (written 2026-11-11, after A1 was seen)

Report version 0.23 announces "a check ... that excludes each run's lead-in". Rule, written before computing:
the run score of T2 is recomputed from the stored per-window distances of A1 without each run's first query
window (samples 0 to 4,096, the first 2 s) and, as a second variant, without its first and last query window.
The good history is unchanged and still contains lead-ins, so this removes the lead-in from the query side only.
The dataset does not mark where a lead-in ends; two seconds is an assumption. Reported: AUROC under H-all and
H-same and the pooled within-group AUROC, BL, ZL and RMS, with the same bootstrap. Harness `leadin.py`.

Note (2026-11-11): the author line at the top was changed from "for BDiM (S. Maslo)" to "for Semir Maslo
(Sulzer-BDiM)". Nothing else in the frozen part was edited.

Note (2026-11-11, at deposit in the MorletDB repository): the parenthetical after the T2 heading, which named a
partner's product, was removed on the repository owner's request. The task and its rule are unchanged.
