# Studies behind the paper: question → fixed comparison → result → limitation → decision

"Fixed before measuring" means: the study's protocol (questions, data, arms, metrics, decision rule) was
committed to this repository before its first result was committed. These are internal plans, not
registrations with a third party, and commit times are set by the author's machine; they are not
independently verified. Times are CET/CEST. Labels:

- **independent confirmation**: first use of data that no earlier study had read;
- **reuse**: data already read or evaluated by an earlier study;
- **exploratory / post hoc**: no decision rule fixed in advance, or analysis decided after seeing results;
- **corrected**: a later study or amendment corrected an earlier result or its analysis;
- **failed / aborted**: a pre-set criterion was not met, or the run stopped before its planned end.

| Study | Question | Fixed before measuring | Result | Limitation | Decision | Label |
|---|---|---|---|---|---|---|
| R1b | Is float16 precision enough for stored envelopes? | protocol bfdf6fe (2026-10-26 17:41), results db63d58 (19:16) | recall@10 0.9999 at 12.9% of raw | 2022 event windows; fidelity, not relevance | store float16 envelopes | independent confirmation |
| R2 | How does exact search scale; can a zero-lag ANN shortlist serve as first stage? | 12cad82 (10-31 14:20), results c07396e (14:55) | exact linear (≈12 µs/window); shortlist recall 0.74 at K = 1000 | its "HNSW" arm silently ran as an exact scan | diagnose (R2-D) | independent confirmation (99.9% unread data); **corrected** by R2-D |
| R2-D | Is the zero-lag loss due to ANN or to the candidate metric? | 7a7b276 (15:57), results 246c91a (16:24) | HNSW and exact shortlists agree (0.744 vs 0.743) | two budgets, one HNSW setting | look for lag-aware candidates | reuse (R2 data) |
| BL1 | Do shifted inner-product probes recover the bounded-lag top ten? | a5381d7 (16:44), results e831e51 (17:29) | all four grid/budget configurations passed; 13 × 50 frozen | development data | confirm once on held-out data | reuse (R2 data), development |
| BL1-C | Does the frozen configuration hold on unread stations and year? | cc99d09 (17:31), results 635ebd4 (18:01) | recall 0.976, p95 131 vs 713 ms (harness arms) | earlier build, shared machine | adopt probe search | **independent confirmation** |
| BL1-S | Can index rows be maintained at commit during ingestion? | ceb63ad (17:33), results 0d5df8b (18:19) | searchable at commit; 919 k samples/s | one day, four channels | maintain in the append transaction | reuse, development |
| SS1 | Is one stored copy of the envelopes enough? | 1942b83 (19:20), results 6a34b63 (20:03) | exact, −250 MB, but +23% or +27–32% p95 | both variants missed their latency bound | keep two copies | reuse (R2 and BL1-C data) |
| SS3 | Do filters break probe search, and does routing fix it? | 71fb9ae (20:21), results 136c81a (20:54) | post-filter 0.515 at 1.8% eligible; routed 0.977–1.0 | router missed its latency bound by 0.8 ms (after a corrected count, SS3b) | route by eligible-window count | reuse (held-out data already used by BL1-C); **corrected** (SS3b) |
| Pruning | Could exact pruning with lag-uniform bounds give ≥ 2×? | none separate (README and results in 9aea409, 10-30) | at most 63% pruned; modelled ≤ 2.3× | idealized feasibility study | pursue approximate candidates | **exploratory**; stopped at its gate |
| Grid analysis | Why does spacing five suffice? | none | envelope autocorrelation 0.973 at 2, 0.895 at 5 | explains, does not guarantee | describe as explanation | **post hoc**, development data |
| Stage 2 C2 / C3 | Does ingest at 409,600 samples/s hold with search load; what at 2×? | 5ad1f12 (11-01 09:32), results 5fadadb (10:56) | C2 passed (15 min); C3 failed (capacity 1.3–1.4×) | shared machine; WAL 18× growth | try a tuned configuration (Amendment 1) | C3 **failed** |
| Stage 2 C2p | Does a tuned configuration contain WAL? | Amendment 1, 2412a77 (11-01 10:57:49), before C2p's first sample (10:58:07); on `main` via #76 | WAL 2.9× growth; passed | four settings and background load changed together | run C4 | amendment after C3 |
| Stage 2 C4 | Does the target hold for two hours? | same plan | target met for 111 min, then an unexplained pause; stopped at 112.5 min | cause unknown; lag rule stricter than worded | report as incomplete | **aborted** |
| E1 | All arms on one build and configuration | 34904f6 (11-01 12:12), deviations 1–3 before the run, results 9c2b6b0 (13:26) | held-out probes 0.977, p95 57 vs 740 ms; grid 0.999; float16 0.999 | re-measurement of data used by BL1-C and SS3 | source of the main tables | **reuse** (consolidated re-measurement, not a new independent confirmation) |
| D1, D2 | Where does probe time go; do correlated filters matter? | fa367ed (13:02), results e335217 (13:32) | index statements dominate; correlation changes latency, not recall | development data; synthetic filter choice | name next engineering steps | **exploratory** diagnostics with fixed protocol |

Later corrections to the paper's wording (not to data): draft 0.9 bounded the attribution of loss to the
evaluated configuration; draft 0.10 states that the sparse grid misses one of 1,000 held-out reference
intervals, which an earlier draft had called "no measurable loss".
