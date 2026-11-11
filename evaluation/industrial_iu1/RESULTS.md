# IU1 results: bounded-lag envelope search on CNC milling vibration

Protocol: [`PROTOCOL.md`](PROTOCOL.md), frozen before the full load (deviations 1 to 4 listed there, all before
any confirmation data was loaded). One run of every task on 2026-11-11, 09:21 to 09:48 UTC. Harness `iu1.py`,
decisions by `analyze.py`, raw results in `results/` (`summary.json` holds every number below).

Data: Bosch CNC Machining, 1,702 runs, 3 axes, 178.6 M samples per axis (535.8 M in total), 255,126 envelope
windows of 4,096 samples. Development M01 (519 runs, 34 bad), confirmation M02 and M03 (1,183 runs, 36 bad).
MorletDB 088f03b, SQL 0.3.8, envelope `xc-envelope-f16-v1`, pgvector 0.8.7, PostgreSQL 17.6 in Docker Desktop on
a Windows laptop (16 logical CPUs, 7.2 GB for Docker).

## Outcome (confirmation data)

| Task | Question | Result | Decision |
|---|---|---|---|
| T1 | Bad run as query: share of other bad runs in the top 10 (same machine, operation, axis) | BL 0.177, ZL 0.081, RMS 0.216, chance 0.026 | BL better than ZL (+0.096, 95% CI [0.069, 0.123]); BL against RMS no difference (−0.039, [−0.089, 0.008]) |
| T2 | Run score = distance to nearest good-history window; AUROC bad against good | BL 0.925 [0.878, 0.967], ZL 0.875 [0.804, 0.938], RMS-kNN 0.852 [0.779, 0.918] | BL better than ZL (+0.050, [0.020, 0.086]); BL against RMS no difference (+0.073, [−0.002, 0.158]) |
| T3 | Does the lag matter? | BL and ZL top 10 share 38% of hits; BL top-1 distance lower by median 0.030 (p90 0.143); 73% of queries gain more than 0.01 | descriptive |
| T4 | Probes against exact, unfiltered gallery of 85,042 windows (axis x) | recall@10 1.000 (min 1.0); p50 56 against 141 ms, p95 126 against 176 ms | **probes do not hold** by the frozen rule (p95 ratio 0.72, rule asks for at most 0.5) |
| T5 | Query stretched by s (feed override); own interval in top 10 | BL: 1.00 / 0.97 / 0.76 / 0.51 / 0.20 at s = 0 / 1 / 2 / 5 / 10%. ZL: 1.00 / 1.00 / 0.79 / 0.20 / 0.07 | descriptive |
| T6 | Same operation recognised on another machine (top 10, axis filter only) | BL 0.503, ZL 0.412, chance 0.076 | BL better than ZL (+0.091, [0.050, 0.132]) |

Development data (M01) agree in direction: T1 BL 0.301, ZL 0.181, RMS 0.281, chance 0.063; T2 AUROC BL 0.912,
ZL 0.854, RMS 0.893; BL better than ZL in both, no difference against RMS in both.

## Reading

- **Lag tolerance pays on machine data too.** On every relevance task the bounded-lag scorer beat zero lag
  (T1, T2, T6), on development and confirmation data. Only about a third of the top-10 hits are the same; a
  window grid does not align repeated machining events.
- **It does not beat a simple amplitude feature.** RMS retrieved bad runs as well as the envelope scorer (T1) and
  scored runs with an AUROC that is not significantly different (T2). The envelope is z-scored and therefore
  blind to level; RMS is blind to shape. For condition monitoring the two are complements, not rivals.
- **Run scoring against a good history works well on these labels.** An AUROC of 0.93 from nearest-neighbour
  distance to good runs of the same operation, without training, is the practical form of the question
  "have we seen this before?".
- **Time warping is the limit.** At 1% stretch the scorer still finds the source (0.97); at 2% it loses a quarter,
  at 10% four in five. Bounded lag tolerates about twice the stretch of zero lag (0.51 against 0.20 at 5%), but feed
  overrides of several percent need alignment (for example DTW) or segmentation by NC block before search.
- **Probes are exact here but not much faster.** At 85,000 windows the screened exact path already answers in
  141 ms (p50), so probes saved 60% at p50 and 28% at p95. This is consistent with the paper's guidance that exact
  search suits galleries below a few hundred thousand windows. Per-operation galleries in this task (about 2,000
  windows) are routed to exact search anyway.
- **Storage:** raw chunks took 9.93 bits per sample (integer counts stored losslessly; 4.29 GB as float64,
  0.66 GB in chunk tables), envelopes another 0.28 GB. Not part of the protocol, reported for context.

## Limits

One public dataset, labels per run (a bad run may contain normal windows), single axes searched separately,
synthetic stretching instead of real override data, one laptop under Docker Desktop. Latencies were measured with
six concurrent clients in T1/T2 and are not comparable with the paper's machine. No statement on diagnosis or
real-time sorting.

## Addendum A1: drift checks (2026-11-11, after the results above were seen)

Rules in [`PROTOCOL.md`](PROTOCOL.md), Addendum A1, written before these checks were computed. One run, 28,392
query windows, nearest good-history distance per half-year of the history. Harness `drift.py`, decisions by
`drift_analyze.py`, numbers in `results/drift_summary.json`. Not part of the frozen protocol.

Why: 20 of the 36 bad confirmation runs come from M02, Feb 2019, and the dataset gives only the half-year of a
run. The T2 AUROC could measure drift between half-years instead of "bad".

### AUROC of bad against good runs (95% run bootstrap)

| History | Bad / good runs | Bounded lag | Zero lag | RMS | Run length |
|---|---|---|---|---|---|
| All half-years (frozen T2) | 36 / 1,147 | 0.925 [0.878, 0.967] | 0.875 [0.803, 0.938] | 0.852 [0.777, 0.917] | 0.982 [0.960, 0.995] |
| Same half-year only | 35 / 1,138 | 0.933 [0.895, 0.965] | 0.880 [0.813, 0.934] | 0.910 [0.840, 0.966] | 0.984 [0.964, 0.996] |
| Pairs inside machine and half-year, same-half-year history | 35 / 825 | 0.905 [0.853, 0.950] | 0.790 [0.712, 0.880] | 0.859 [0.724, 0.954] | 0.988 [0.980, 0.996] |
| Without M02 Feb 2019 | 16 / 1,121 | 0.854 [0.768, 0.932] | 0.752 [0.626, 0.872] | 0.784 [0.659, 0.894] | 0.968 [0.922, 0.994] |
| Own and earlier half-years | 36 / 1,139 | 0.924 [0.872, 0.968] | 0.862 [0.778, 0.935] | 0.882 [0.805, 0.948] | 0.981 [0.960, 0.995] |
| **Earlier half-years only** | 13 / 847 | **0.603 [0.419, 0.780]** | 0.512 [0.329, 0.697] | 0.550 [0.384, 0.714] | 0.974 [0.947, 0.992] |

Run length is the absolute deviation of a run's length from the median length of its good history.

### Decisions by the rule fixed in the addendum

- **"Separates bad from good runs" stands.** Pairs inside the same machine and half-year, with history from that
  half-year only: BL 0.905, interval above 0.5.
- **"BL better than ZL" stands.** Difference under the same condition +0.116 [0.047, 0.184].
- BL against RMS: +0.047 [−0.068, 0.215], no difference, as before.

### What the checks change

1. **The separation is not drift between half-years.** It holds inside a half-year (0.933, pooled within groups
   0.905) and drops to 0.854 without M02 Feb 2019.
2. **A history from earlier half-years only does not work.** AUROC 0.603 with an interval that includes 0.5, on
   13 bad runs. The signal changes between half-years by more than a bad run differs from a good one, so the
   history has to be recent. Inside a half-year the order of runs is unknown, so "same half-year" may contain runs
   recorded after the query; a strictly causal test at finer resolution is not possible with this dataset.
3. **Run length alone separates better than any signal score:** 0.982 against 0.925, and inside groups
   0.988 against 0.905 (BL minus run length −0.083 [−0.133, −0.039]). In this dataset a bad run is mostly a run of
   unusual length. Example: the two bad OP10 runs of M02 Feb 2019 last 42.0 s, the good one 49.2 s.
4. **T1 was inflated by the half-year.** With the gallery and the chance level restricted to the query's own
   half-year: BL 0.254, ZL 0.212, RMS 0.377, chance 0.183 (was BL 0.177 against chance 0.026). BL is still above
   ZL (+0.043 [0.025, 0.063]) and above chance (+0.071 [0.043, 0.101]), but RMS is better than BL here
   (BL minus RMS −0.123 [−0.167, −0.080]). 24 of the queries returned fewer than ten hits.
5. **History size.** M02 Feb 2019 has a median of 2 good history runs per operation inside its half-year (0 to 3),
   M02 Aug 2020 and Aug 2021 have 3; Aug 2019 has 17 to 19. Over all good runs, score and history size are not
   related (Spearman 0.00), but the half-years with thin histories are also the ones with high distances
   (median good-run score 0.055 to 0.057 against 0.027 to 0.043 elsewhere on M02).

Per machine and half-year (BL, same-half-year history): M02 Aug 2019 1.000 (4 bad), M02 Feb 2019 0.958 (20),
M02 Feb 2021 0.992 (1), M02 Aug 2021 0.767 (1), M03 Aug 2019 0.862 (3), M03 Feb 2021 0.603 (4),
M03 Aug 2021 0.643 (2). The M03 groups with two to four bad runs are weak and their intervals are wide.

### Example figures

The single-case figures now compare the bad run with a good run of the same machine, operation and half-year
(M02, OP10, Feb 2019). The band-power difference is not drift: 26,824 against 1,092 counts² in 125 to 250 Hz.
The bad run lasts 42.8 s, the good one 49.2 s.

## Addendum A2: lead-in check (2026-11-11, after A1 was seen)

Rule in [`PROTOCOL.md`](PROTOCOL.md), Addendum A2. Recomputed from the per-window distances of A1
(`leadin.py`, `results/leadin_summary.json`). The lead-in is removed on the query side only, and "the first 2 s"
is an assumption; the dataset does not mark it.

| Query windows | History | Bounded lag | Zero lag | RMS |
|---|---|---|---|---|
| All (as T2) | all half-years | 0.925 [0.878, 0.967] | 0.875 | 0.852 |
| Without the first | all half-years | 0.900 [0.836, 0.954] | 0.851 | 0.847 |
| Without the first and last | all half-years | 0.895 [0.833, 0.949] | 0.863 | 0.853 |
| Without the first | same half-year | 0.902 [0.844, 0.951] | 0.878 | 0.911 |
| Without the first | pairs inside machine and half-year | 0.876 [0.816, 0.926] | 0.796 | 0.839 |

The separation does not come from the lead-in: without each run's first window the AUROC falls from 0.925 to 0.900
and stays well above 0.5 in every variant. The gap between BL and ZL narrows (0.900 against 0.851).
