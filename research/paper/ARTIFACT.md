# Artifact: build, data, checks and reproduction

For the technical report *MorletDB: Recordings, Not Rows — Lag-Tolerant Waveform Retrieval in PostgreSQL*,
in the version given in `REPORT_VERSION`. Released versions are tagged `paper-v<version>`; everything else
is identified by commit. Commands run from the
repository root of a fresh clone of `main` unless stated otherwise.

## 0. What was measured and what is offered

Two commits matter, and they are different:

| Item | Value |
|---|---|
| **Measured software (E1, D1, D2)** | `main` at **68594d7** (pg_morlet SQL 0.3.1). The consolidated comparison E1, the source of the main retrieval tables, records this build commit and library hash in its result files; the diagnostics D1 and D2 ran on the same build and server under a protocol that fixes it (`evaluation/consolidated/DIAGNOSTICS_PROTOCOL.md`), but their result files do not record the hash |
| Earlier studies | R1b, R2, R2-D, BL1, BL1-C, BL1-S, SS1, SS3, the pruning study and the load runs (C2–C4) were measured on earlier implementation states, several on a machine shared with an unrelated job; the report labels their numbers as such. Each study's commits are in [STUDIES.md](STUDIES.md) and its protocol; this document reproduces E1, D1 and D2 only |
| Measured library | `pg_morlet.dylib` SHA-256 `3c786684421f3132e7a97b5ff38801e802948bf3e014aaae656b16bb109de7b7` (recorded in `evaluation/consolidated/results/e1_*.json`) |
| Build reproducibility | a clean release build of 68594d7 from a fresh clone gave a different hash (`a6f7bce8…`): builds are not bit-identical across checkout paths. Behaviour was checked instead (sections 4 and 7) |
| SQL objects | the measured search functions in the evaluation databases are byte-identical to 68594d7 |
| **Report sources** | protocols, harnesses, stored results and report sources entered `main` with merge commit cdf8b4b; the commit each released version is built from is recorded in `RELEASE_MANIFEST.json` (`report_source_commit`) |
| Later fixes, not measured | fix commit 4a9c10c, merged into `main` at 14f7285; why they do not affect the measurements: section 9 |

## 1. Dependencies (versions used)

| Component | Version |
|---|---|
| Rust | 1.91.1 (`rust-toolchain.toml`) |
| cargo-pgrx | 0.16.1 |
| PostgreSQL | 17.11, built by pgrx |
| pgvector | 0.8.0, built into that PostgreSQL (≥ 0.8 is required for iterative index scans) |
| Python, evaluation and Figure 4 | 3.12.13 with numpy 2.2.6, psycopg 3.2.10, obspy 1.4.2, matplotlib 3.10.3 (`.venv-seismic`) |
| Python, Figures 2–3 | 3.14 with matplotlib 3.11.2 (`.venv-paper`) |
| Python, numbers | any Python ≥ 3.12, standard library only (`gen_numbers.py`, `e1_distribution.py`) |
| LaTeX | Tectonic 0.17.0 |
| Hardware used | Apple M5 Max, 18 cores, 128 GB, macOS; 8 GB RAM and 10 GB free disk suffice for the steps below |

## 2. Set-up

### 2.1 Python environments

```sh
python3.12 -m venv .venv-seismic
.venv-seismic/bin/pip install numpy==2.2.6 'psycopg[binary]==3.2.10' obspy==1.4.2 matplotlib==3.10.3
python3 -m venv .venv-paper
.venv-paper/bin/pip install matplotlib==3.11.2
```

Sections 5 and 6 need only `python3` and `.venv-paper`; sections 3, 7 and 8 need `.venv-seismic`.

### 2.2 Tectonic

Install Tectonic 0.17.0 from <https://tectonic-typesetting.github.io/> (for example `brew install tectonic`).
The first compile downloads the TeX support files it needs.

### 2.3 PostgreSQL, pg_morlet and pgvector

```sh
cargo install cargo-pgrx --version 0.16.1 --locked
cargo pgrx init --pg17 download                     # builds PostgreSQL 17.x under ~/.pgrx
PGC=~/.pgrx/17.11/pgrx-install/bin/pg_config        # adjust if pgrx installed a different 17.x
export PG_CONFIG="$PGC"                             # read by evaluation/consolidated/prepare_db.sh
P="$("$PGC" --bindir)/psql"

git worktree add ../morletdb-68594d7 68594d7        # the measured state, next to this clone
(cd ../morletdb-68594d7/extensions/pg_morlet && cargo pgrx install --release --pg-config "$PGC")

git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git ../pgvector
(cd ../pgvector && make OPTFLAGS="" PG_CONFIG="$PGC" && make install PG_CONFIG="$PGC")

(cd extensions/pg_morlet && cargo pgrx start pg17)  # listens on port 28817
```

Install the extension from the 68594d7 worktree, not from `main`; keep `main` checked out for the harnesses
and results. Run the later sections in the same shell, so that `PGC`, `PG_CONFIG` and `P` point at the
installation chosen here. The server must listen on port 28817: `e1.py`, `diag.py`, `repeat_check.py`,
`cases.py` and the load scripts connect there. `cargo pgrx init` may download a PostgreSQL 17 minor release newer than 17.11, which the
measurements used. See also [`docs/installation-and-queries.md`](../../docs/installation-and-queries.md) and
[`docs/compatibility.md`](../../docs/compatibility.md).

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
$P -h localhost -p 28817 -d postgres -c "CREATE DATABASE morlet_smoke"
$P -h localhost -p 28817 -v ON_ERROR_STOP=1 -d morlet_smoke -f evaluation/consolidated/smoke_check.sql   # prints PASS
$P -h localhost -p 28817 -d postgres -c "DROP DATABASE morlet_smoke"
```

## 5. Regenerate the numbers (no database, no benchmark)

The report's numbers, tables and macros are generated from the stored results:

```sh
python3 research/paper/gen_numbers.py --check        # fails if any generated file is stale
python3 evaluation/consolidated/e1_distribution.py evaluation/consolidated/results >/dev/null
git diff --exit-code -- evaluation/consolidated/results/e1_distribution.json
```

Without `--check`, `gen_numbers.py` rewrites `research/paper/generated/` and the generated block in the
README. CI runs the same checks (`.github/workflows/paper-numbers.yml`).

## 6. Compile the PDFs

```sh
.venv-paper/bin/python -I research/paper/figures.py           # Figures 2 and 3, deterministic PDFs
(cd research/paper && tectonic -X compile morletdb.tex && tectonic -X compile supplement.tex)
research/paper/sync_site.sh                                   # copy both PDFs to docs/ for the website
research/paper/sync_site.sh --check                           # byte-equality check (also in CI)
```

`research/paper/regenerate.sh` runs sections 5 and 6 in one go (it regenerates rather than checks).
Figure 4 (`fig/cases.pdf`) is committed and not regenerated here: it needs the held-out database
(section 8.2). PDF bytes depend on the Tectonic and TeX support-file versions; the hashes in
`RELEASE_MANIFEST.json` identify the released files, not every rebuild.

## 7. Check a preserved index

"Preserved" means the same, unchanged HNSW graph that produced the baseline results. `repeat_check.py`
works on the development database only (100 dev queries). It reads `e1_dev.json` and `d1_dev.json` from the
directory given as its second argument and compares, per query, exact lists with E1's exact lists, probe
lists with D1's probe hits (same graph) and probe lists with E1's probe lists (the graph before E1's
rebuild). It writes nothing to the database; run it on a copy anyway:

```sh
$P -h localhost -p 28817 -d postgres -c "CREATE DATABASE morlet_r2_check TEMPLATE morlet_r2"
.venv-seismic/bin/python -I evaluation/consolidated/repeat_check.py morlet_r2_check evaluation/consolidated/results out.json
$P -h localhost -p 28817 -d postgres -c "DROP DATABASE morlet_r2_check"
```

**This example checks the author's original database** against its recorded results in
`evaluation/consolidated/results/` (2026-11-01: 100/100 equal in all three comparisons,
`results/repeat_check_dev.json`). That database is not distributed. On any other database, only the exact
comparison is meaningful against these files: exact lists must match, but probe lists from an
independently built HNSW graph can differ from the stored ones, and that is not a failure.

To check **your own** preserved index, first produce a baseline on it with section 8.2 (`e1.py` on
`morlet_r2`, which rebuilds the probe index at its end, then `diag.py d1` on the rebuilt index), save the
two outputs as `mine/e1_dev.json` and `mine/d1_dev.json` as written there, leave the database unchanged,
and pass `mine` instead of `evaluation/consolidated/results`. Probe lists are then expected to equal your D1 hits
(same graph); equality with your E1 probe lists is not expected, because E1 searched the graph before its
rebuild.

## 8. Rebuild databases and indexes

### 8.1 Build the evaluation databases

```sh
evaluation/consolidated/prepare_db.sh dev     morlet_r2   data/cx-scale
evaluation/consolidated/prepare_db.sh heldout morlet_bl1c data/cx-confirm-2021
$P -h localhost -p 28817 -d morlet_r2   -f evaluation/consolidated/universe_check.sql
$P -h localhost -p 28817 -d morlet_bl1c -f evaluation/consolidated/universe_check.sql
```

`prepare_db.sh` uses `PG_CONFIG` from section 2.3 (otherwise it defaults to the 17.11 path). Each loads
recordings, chunks and envelopes with the study's load script and enables product probe search
(`morlet_enable_envelope_probes`); the original databases' probe tables were created with the same call.
About 15 minutes and 2–3 GB per database.

### 8.2 Run E1, D1, D2 and Figure 4

```sh
B=../morletdb-68594d7    # the measured checkout from section 2.3; e1.py records its library hash
mkdir -p mine            # your own results; keep them apart from the stored ones
.venv-seismic/bin/python -I evaluation/consolidated/e1.py morlet_r2   evaluation/bounded_lag/dev_queries.json              mine/e1_dev.json     $B
.venv-seismic/bin/python -I evaluation/consolidated/e1.py morlet_bl1c evaluation/bounded_lag/confirm/results/queries.json mine/e1_heldout.json $B
.venv-seismic/bin/python -I evaluation/consolidated/diag.py d1 morlet_r2 evaluation/bounded_lag/dev_queries.json mine/d1_dev.json
.venv-seismic/bin/python -I evaluation/consolidated/diag.py d2 morlet_r2 evaluation/bounded_lag/dev_queries.json mine/d2_dev.json
.venv-seismic/bin/python -I research/paper/cases/cases.py figure morlet_bl1c
```

Add `smoke` as the fifth argument of `e1.py` for a three-query check without the index rebuild.

**What to expect.** Each new database has a newly built HNSW graph. Exact results must match the stored
ones. Probe candidates, and therefore per-query probe recall, **may differ**, because each HNSW build is a
new graph. Latencies depend on the machine and its load and are not expected to match. The held-out data
were already used by BL1-C, SS3 and E1: rerunning them repeats a measurement, it is not a new confirmation,
and they must not be used to tune parameters.

## 9. Changes between measured and offered states and why the measurements stay valid

Fix commit 4a9c10c, merged into `main` at 14f7285:

| Fix | Property affected | Influence on E1 / D1 / D2 | Check |
|---|---|---|---|
| row-security set-up matched other collections' probe tables | access control set-up | none: no row security in E1/D1/D2 | code diff: only that function |
| router passed `max_candidates = exact_threshold` to the exact branch | error under concurrent commits with ≤ threshold eligible windows | none: every measured search took the probe branch (≥ 15,753 eligible windows), databases static | code diff: probe statements, re-rank and suppression unchanged |
| build did not re-check for the probe table after waiting | probe rows missing if probes are enabled during a build | none: no envelope lacked a probe row | `universe_check.sql` |
| tests (probe path, isolation) and documentation | tests, docs | none | 87/87 tests in 10 runs; CI green |

## 10. What has and has not been checked

The clean check of 2026-11-01 is recorded in
[`evaluation/consolidated/ARTIFACT_CHECK.md`](../../evaluation/consolidated/ARTIFACT_CHECK.md). It covered a
fresh clone and build of 68594d7, the functional check, data checksums, a rebuild of the held-out database,
an E1 smoke run, list comparisons and the preserved-index check. It did **not** cover a fresh
`cargo pgrx init` of PostgreSQL and pgvector, a fetch of the data from GEOFON, a rebuild of the development
database, the Python environment set-up of section 2.1 from scratch, the pgvector build commands as written
in section 2.3, or latency.
