# Technical report release package

| | |
|---|---|
| Title | MorletDB: Recordings, Not Rows — Lag-Tolerant Waveform Retrieval in PostgreSQL |
| Type | technical report / preprint (not peer reviewed); the title page shows only the version |
| Version | 0.12, 2026-11-01 (from `REPORT_VERSION`) |
| Author | Zin Nam (no affiliation stated) |
| Report sources | the commit recorded as `report_source_commit` in [RELEASE_MANIFEST.json](RELEASE_MANIFEST.json); the sources first entered `main` with merge commit cdf8b4b |
| Measured software | E1 (main retrieval tables) and diagnostics D1–D2: pg_morlet at `main` 68594d7, library SHA-256 `3c786684…`; earlier studies used earlier implementation states, labelled in the report and in [STUDIES.md](STUDIES.md) (see [ARTIFACT.md](ARTIFACT.md)) |
| Licence | report text and figures: CC BY 4.0 ([LICENSE.md](LICENSE.md)); code: Apache-2.0 (repository `LICENSE`) |
| Tag | `paper-v0.12`, on the commit that contains this manifest |
| Funding | none |
| Conflicts of interest | no statement (author's decision) |

## Contents

| File | What |
|---|---|
| `morletdb.pdf` | main report (11 pages) |
| `supplement.pdf` | supplement (4 pages): storage, building, access control, recovery and load, study overview, negative results |
| `RELEASE_MANIFEST.json` | version, date, commits, PDF SHA-256 sums, proposed tag; `release_manifest.py --check` verifies it |
| `STUDIES.md` | every study: question, comparison fixed before measuring (with commits), result, limitation, decision, kind of evidence |
| `ARTIFACT.md` | set-up from a fresh checkout, data with checksums, separate reproduction procedures |
| `../../evaluation/consolidated/ARTIFACT_CHECK.md` | result of a clean rebuild and check (2026-11-01) and what it did not cover |
| `generated/` | numbers and tables generated from stored results by `gen_numbers.py` |
| `cases/` | selection rule and selected cases for Figure 4 |
| `../../docs/morletdb.pdf`, `../../docs/morletdb-supplement.pdf` | website copies, byte-identical to the two PDFs (`sync_site.sh --check`) |

## Regenerating and reproducing

See [ARTIFACT.md](ARTIFACT.md): regenerate the numbers (section 5), compile the PDFs (section 6), check a
preserved index (section 7), rebuild databases and indexes (section 8). A rebuilt HNSW index is a new
graph: exact results must match, probe candidates and latencies may differ.

## Use of AI assistance and responsibility

Code, experiment harnesses, analyses and text were developed with Claude (Anthropic), an AI assistant,
working under the author's direction. The author reviewed the results and is responsible for the content.
The same statement appears at the end of the report.

## Freezing the package

1. Commit the final sources and PDFs (and the `docs/` copies).
2. Run `research/paper/release_manifest.py` to record that commit and the PDF hashes in
   `RELEASE_MANIFEST.json`, and commit the manifest on its own. The manifest therefore names the commit
   *before* its own.
3. `research/paper/release_manifest.py --check` verifies the hashes and that no report file changed since
   the recorded commit. CI runs it, so any later change under `research/paper/` or to the website PDFs
   needs a regenerated manifest (step 2).

## Author decisions (2026-11-01)

- licence of text and figures: CC BY 4.0 ([LICENSE.md](LICENSE.md)); code stays Apache-2.0;
- funding: none; no conflict-of-interest statement;
- AI statement: as printed at the end of the report;
- publication: GitHub release under tag `paper-v<version>` (0.11 released; 0.12 names the software version);
  no preprint server, DOI or journal submission yet.
