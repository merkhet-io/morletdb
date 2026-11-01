# Technical report release package (prepared, not published)

| | |
|---|---|
| Title | MorletDB: Recordings, Not Rows — Lag-Tolerant Waveform Retrieval in PostgreSQL |
| Type | technical report / preprint (not peer reviewed); the title page shows only the version |
| Version | 0.11, 2026-11-01 |
| Author | Zin Nam (no affiliation stated) |
| Report sources | the commit recorded as `report_source_commit` in [RELEASE_MANIFEST.json](RELEASE_MANIFEST.json); the sources first entered `main` with merge commit cdf8b4b |
| Measured software | pg_morlet at `main` 68594d7, library SHA-256 `3c786684…` (see [ARTIFACT.md](ARTIFACT.md)) |
| Licence | code: Apache-2.0 (repository `LICENSE`); report text and figures: **not yet chosen**, CC BY 4.0 proposed in [LICENSE-REPORT-PROPOSAL.md](LICENSE-REPORT-PROPOSAL.md), not applied |
| Proposed tag | `paper-v0.11` (not created) |
| Funding, conflicts of interest | none stated; to be confirmed by the author before publication |

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
   the recorded commit.

## Before publishing (author decisions)

- confirm the AI statement wording;
- choose the report licence (CC BY 4.0 proposed; apply it by adding the notice to the title page, README and
  this file);
- confirm that no funding or conflict-of-interest statement is needed;
- decide whether and where to publish (repository release under tag `paper-v0.11`, a preprint server, or
  both); creating the tag, a release, a DOI or a submission has not been done.
