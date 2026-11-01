# Artifact: what was measured, what is offered, and what lies between

Paper draft 0.9 (submission candidate). No release tag has been created; artifacts are identified by
commit.

## 1. Measured state

| Item | Value |
|---|---|
| Extension source | `main` at **68594d7** (pg_morlet SQL 0.3.1, PRs up to #75) |
| Installed library | `pg_morlet.dylib` SHA-256 `3c786684421f3132e7a97b5ff38801e802948bf3e014aaae656b16bb109de7b7` (`cargo pgrx install --release`; recorded in `evaluation/consolidated/results/e1_*.json`) |
| SQL objects in the evaluation databases | the measured search functions (`morlet_search_envelope_probes`, `morlet_envelope_ranked`, `morlet_search_envelope_interval`) are byte-identical to 68594d7 (checked before E1, protocol deviation 3) |
| Dependencies | PostgreSQL 17.11 (pgrx build), pgvector 0.8.0 |
| Configuration | PostgreSQL defaults (`ALTER SYSTEM RESET ALL` after C4); see E1 results |
| Studies on this state | E1, D1, D2 (`evaluation/consolidated/`) |
| Earlier states | R2/R2-D, BL1/BL1-C, SS1, SS3, Stage 2 load runs (C2–C4): commits named in their own results |

## 2. Offered state

| Purpose | Commit |
|---|---|
| Paper, supplement, protocols, harnesses, results, figures | this branch (`docs/morletdb-research-paper-v01`), PR #61 |
| Extension to reproduce the main tables | **68594d7** (the measured state) |
| Pilot results C2p and C4 (merged) | `main` after PR #76 (ba3df69) |
| Audit fixes (not measured; see section 3) | PR #77 at 4a9c10c |

Build and run (macOS or Linux, PostgreSQL 17 via pgrx 0.16.1, Rust 1.91.1):

```sh
git checkout 68594d7
cd extensions/pg_morlet && cargo pgrx install --release --pg-config <pg17>/bin/pg_config
# pgvector 0.8.0 built into the same PostgreSQL; then load the evaluation databases:
#   development (R2):  evaluation/retrieval_scaling/ (load) -> morlet_r2, probes enabled
#   held-out (BL1-C):  evaluation/bounded_lag/confirm/{fetch,load}.py -> morlet_bl1c
git checkout <this branch>
.venv-seismic/bin/python -I evaluation/consolidated/e1.py morlet_r2 evaluation/bounded_lag/dev_queries.json out_dev.json <68594d7 checkout>
.venv-seismic/bin/python -I evaluation/consolidated/e1.py morlet_bl1c evaluation/bounded_lag/confirm/results/queries.json out_heldout.json <68594d7 checkout>
.venv-seismic/bin/python -I evaluation/consolidated/diag.py d1 morlet_r2 evaluation/bounded_lag/dev_queries.json d1.json
.venv-seismic/bin/python -I evaluation/consolidated/diag.py d2 morlet_r2 evaluation/bounded_lag/dev_queries.json d2.json
psql -d morlet_r2 -f evaluation/consolidated/universe_check.sql      # and morlet_bl1c
python3 evaluation/consolidated/e1_distribution.py evaluation/consolidated/results
.venv-paper/bin/python -I research/paper/figures.py && cd research/paper && tectonic -X compile morletdb.tex
```

Resources: about 2 GB of disk per evaluation database, 8 GB RAM is ample; E1 takes about 8 minutes
per database, D1 and D2 about 10 minutes together. Latencies depend on the machine and are not expected
to reproduce exactly; recall and candidate counts are deterministic for a given index build (an index
rebuild changes the HNSW graph and can change probe recall slightly).

Data: CX network (IPOC), GEOFON/FDSN, DOI 10.14470/PK615318; the DOI page states no licence ("Terms:
Unknown") and says data are freely distributed. The repository contains station/day identifiers,
checksums and fetch scripts, not waveforms.

## 3. Changes between measured and offered states (PR #77) and why the measurements stay valid

| Fix | Property affected | Influence on E1 / D1 / D2 | Artifact state needed | Check made |
|---|---|---|---|---|
| `morlet_enable_row_security` matched other collections' probe tables | row-level security set-up | none: no row security in E1/D1/D2; earlier RLS timings used collection `r2` and `pl` (no `_`) | either | code diff: only this function |
| router passed `max_candidates = exact_threshold` to the exact branch | error under concurrent commits when ≤ threshold windows are eligible | none: E1 unfiltered and D2 filters had 15,753–234,762 eligible windows (probe branch); no writes during the runs | either | code diff: the probe statements, re-rank and suppression are unchanged; only the exact-branch cap differs |
| build did not re-check for the probe table after waiting | probe rows missing after enabling probes during a build | none: all envelopes had probe rows during E1/D1/D2 | either | `universe_check.sql`: 0 envelopes without a probe row, in both databases |
| probe test compared exact with exact; test-isolation deadlocks | test coverage | none (tests only) | — | 87/87 tests, 10 consecutive runs |
| contract and docs (pgvector ≥ 0.8, probe candidates, snapshots) | documentation | none | either | — |

No re-measurement was needed: none of the fixes changes the statements that E1, D1 and D2 executed or
the data they searched.

## 4. Search universe in E1 (`results/universe_check.txt`)

In both databases, every stored 4096-sample envelope had its probe row (234,762 and 234,622), no other
window size existed, every recording's windows formed a gap-free prefix from sample 0 with no missing
tail window, and the envelope tables' write counters equal the initial load, so nothing was written
during E1, D1 or D2. Exact and probe paths therefore searched the same, complete, unchanging set of
windows.
