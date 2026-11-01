# Artifact: build, data, checks and reproduction

For the paper *MorletDB: Recordings, Not Rows — Lag-Tolerant Waveform Retrieval in PostgreSQL*
(draft 0.10). No release tag has been created; artifacts are identified by commit. Commands run from the
repository root unless stated otherwise.

## 0. What was measured and what is offered

| Item | Value |
|---|---|
| Measured extension | `main` at **68594d7** (pg_morlet SQL 0.3.1) |
| Measured library | `pg_morlet.dylib` SHA-256 `3c786684421f3132e7a97b5ff38801e802948bf3e014aaae656b16bb109de7b7` (recorded in `evaluation/consolidated/results/e1_*.json`) |
| Build reproducibility | a clean release build of 68594d7 from a fresh clone gave a different hash (`a6f7bce8…`): builds are not bit-identical across checkout paths. Behaviour was checked instead (steps 4 and 6) |
| SQL objects | the measured search functions in the evaluation databases are byte-identical to 68594d7 |
| Paper, protocols, harnesses, stored results | branch `docs/morletdb-research-paper-v01` (PR #61) |
| Later fixes, not measured | PR #77 at 4a9c10c; why they do not affect the measurements: section 8 |

## 1. Dependencies (versions used)

| Component | Version |
|---|---|
| Rust | 1.91.1 (`rust-toolchain.toml`) |
| cargo-pgrx | 0.16.1 (`cargo install cargo-pgrx --version 0.16.1 --locked`) |
| PostgreSQL | 17.11, built by pgrx (`cargo pgrx init --pg17 download`) |
| pgvector | 0.8.0, built into that PostgreSQL (`make OPTFLAGS="" && make install PG_CONFIG=<pg17>/bin/pg_config`; ≥ 0.8 is required for iterative index scans) |
| Python, evaluation | 3.12.13 with numpy 2.2.6, psycopg 3.2.10, obspy 1.4.2 (`.venv-seismic`) |
| Python, figures | 3.14 with matplotlib 3.11.2, pymupdf 2.0 (`.venv-paper`) |
| LaTeX | Tectonic 0.17.0 |
| Hardware used | Apple M5 Max, 18 cores, 128 GB, macOS; 8 GB RAM and 10 GB free disk suffice for the steps below |

## 2. Build and install

```sh
git clone https://github.com/merkhet-io/morletdb.git && cd morletdb
git checkout 68594d7                     # the measured state
cd extensions/pg_morlet
cargo pgrx install --release --pg-config ~/.pgrx/17.11/pgrx-install/bin/pg_config
cargo pgrx start pg17                    # port 28817
cd ../.. && git checkout docs/morletdb-research-paper-v01   # harnesses and results (do not reinstall)
```

## 3. Data: provenance and checksums

CX network (IPOC; GFZ and CNRS-INSU), served by GEOFON (FDSN), DOI 10.14470/PK615318. The DOI page lists
the licence as unknown and states that data are freely distributed. The repository holds only station/day
identifiers, fetch scripts and SHA-256 sums, not waveforms.

```sh
.venv-seismic/bin/python -I evaluation/retrieval_scaling/fetch.py data/cx-scale              # dev: PB01 PB02 PB06 PSGCX, 2025-03-01..14 (56 files, 422 MB miniSEED)
.venv-seismic/bin/python -I evaluation/bounded_lag/confirm/fetch.py data/cx-confirm-2021     # held-out: PB05 PB11 PB14 PB18, 2021-03-01..14 (56 files)
(cd data/cx-scale && shasum -a 256 -c ../../evaluation/retrieval_scaling/results/SHA256SUMS.mseed)
(cd data/cx-confirm-2021 && shasum -a 256 -c ../../evaluation/bounded_lag/confirm/results/SHA256SUMS.mseed)
```

A data centre may re-pack miniSEED records over time; if a checksum differs, the samples may still be
identical, but the run is then not a verified reproduction of the same files.

## 4. Functional check (synthetic data, no download)

```sh
psql -h localhost -p 28817 -d postgres -c "CREATE DATABASE morlet_smoke"
psql -h localhost -p 28817 -v ON_ERROR_STOP=1 -d morlet_smoke -f evaluation/consolidated/smoke_check.sql   # prints PASS
psql -h localhost -p 28817 -d postgres -c "DROP DATABASE morlet_smoke"
```

## 5. Evaluation databases

```sh
evaluation/consolidated/prepare_db.sh dev     morlet_r2   data/cx-scale
evaluation/consolidated/prepare_db.sh heldout morlet_bl1c data/cx-confirm-2021
```

Each loads recordings, chunks and envelopes with the study's load script and enables product probe search
(`morlet_enable_envelope_probes`); the original databases' probe tables were created with the same call.
About 15 minutes and 2–3 GB per database. Then check the search universe:

```sh
psql -h localhost -p 28817 -d morlet_r2 -f evaluation/consolidated/universe_check.sql   # and morlet_bl1c
```

## 6. Reproduction, three levels

1. **Tables and figures from the stored results** (no database needed):
   ```sh
   python3 evaluation/consolidated/e1_distribution.py evaluation/consolidated/results
   .venv-paper/bin/python -I research/paper/figures.py
   (cd research/paper && tectonic -X compile morletdb.tex && tectonic -X compile supplement.tex)
   ```
2. **Re-search on a preserved index.** Run against a copy, because `e1.py` rebuilds the probe index at
   its end (for the build-time measurement), which changes the index for later runs:
   ```sh
   psql -h localhost -p 28817 -d postgres -c "CREATE DATABASE morlet_r2_check TEMPLATE morlet_r2"
   .venv-seismic/bin/python -I evaluation/consolidated/repeat_check.py morlet_r2_check evaluation/consolidated/results out.json
   psql -h localhost -p 28817 -d postgres -c "DROP DATABASE morlet_r2_check"
   ```
3. **Full E1, D1, D2 on newly built databases** (each builds its own HNSW graph, so probe candidates can
   differ from the stored ones; exact results must not):
   ```sh
   .venv-seismic/bin/python -I evaluation/consolidated/e1.py morlet_r2 evaluation/bounded_lag/dev_queries.json e1_dev.json <68594d7 checkout>
   .venv-seismic/bin/python -I evaluation/consolidated/e1.py morlet_bl1c evaluation/bounded_lag/confirm/results/queries.json e1_heldout.json <68594d7 checkout>
   .venv-seismic/bin/python -I evaluation/consolidated/diag.py d1 morlet_r2 evaluation/bounded_lag/dev_queries.json d1.json
   .venv-seismic/bin/python -I evaluation/consolidated/diag.py d2 morlet_r2 evaluation/bounded_lag/dev_queries.json d2.json
   ```
   Add `smoke` as the fifth argument of `e1.py` for a three-query check without the index rebuild.
   Latencies depend on the machine and its load and are not expected to match.

## 7. Results of the clean check (2026-11-01)

See [`evaluation/consolidated/ARTIFACT_CHECK.md`](../../evaluation/consolidated/ARTIFACT_CHECK.md).

## 8. Changes between measured and offered states (PR #77) and why the measurements stay valid

| Fix | Property affected | Influence on E1 / D1 / D2 | Check |
|---|---|---|---|
| row-security set-up matched other collections' probe tables | access control set-up | none: no row security in E1/D1/D2 | code diff: only that function |
| router passed `max_candidates = exact_threshold` to the exact branch | error under concurrent commits with ≤ threshold eligible windows | none: every measured search took the probe branch (≥ 15,753 eligible windows), databases static | code diff: probe statements, re-rank and suppression unchanged |
| build did not re-check for the probe table after waiting | probe rows missing if probes are enabled during a build | none: no envelope lacked a probe row | `universe_check.sql` |
| tests (probe path, isolation) and documentation | tests, docs | none | 87/87 tests in 10 runs; CI green |
