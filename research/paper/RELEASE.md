# Paper release package (prepared, not published)

| | |
|---|---|
| Title | MorletDB: Recordings, Not Rows — Lag-Tolerant Waveform Retrieval in PostgreSQL |
| Type | paper and supplement |
| Version | 0.10, 2026-11-01 |
| Author | Zin Nam |
| Report sources | branch `docs/morletdb-research-paper-v01` (PR #61); the exact commit is recorded in the PR description when the package is frozen |
| Measured software | pg_morlet at `main` 68594d7 (see [ARTIFACT.md](ARTIFACT.md)) |
| Licence | code Apache-2.0 (repository `LICENSE`); report text and figures: to be chosen by the author before publication |

## Contents

| File | What |
|---|---|
| `morletdb.pdf` | main report (11 pages) |
| `supplement.pdf` | supplement: storage, building, access control, recovery and load, study overview, negative results |
| `STUDIES.md` | every study: question, comparison fixed before measuring (with commits), result, limitation, decision |
| `ARTIFACT.md` | dependencies, build, data with checksums, databases, three levels of reproduction |
| `../../evaluation/consolidated/ARTIFACT_CHECK.md` | result of a clean rebuild and check |
| `generated/` | numbers and tables generated from stored results by `gen_numbers.py` |
| `cases/` | selection rule and selected cases for Figure 4 |

## Regenerating and reproducing

- **Reconstruct the published numbers, tables and Figures 2–3 from stored results** (no database, no
  benchmark): `research/paper/regenerate.sh`. CI checks that the generated files are current
  (`.github/workflows/paper-numbers.yml`).
- **Re-search a preserved index** (same HNSW graph): `evaluation/consolidated/repeat_check.py` on a copy of a
  database; exact and probe results are expected to match the stored ones.
- **Fresh benchmark** with newly built databases and HNSW indexes: ARTIFACT.md step 6.3. Exact results must
  match; probe candidates may differ because each HNSW build is a new graph; latencies depend on the machine.
- Figure 4 needs the held-out database: `research/paper/cases/cases.py figure morlet_bl1c`.

## Use of AI assistance and responsibility

Code, experiment harnesses, analyses and text were developed with Claude (Anthropic), an AI assistant,
working under the author's direction. The author reviewed the results and is responsible for the content.

## Before publishing (author decisions)

- confirm the AI statement wording and the report licence (for example CC BY 4.0);
- freeze the package at one commit and record it in the PR description and on the report's title page;
- choose where to publish the paper (repository release, institutional or preprint server).
