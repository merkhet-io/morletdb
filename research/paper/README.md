# MorletDB research manuscript

Working draft 0.2: **Recordings, Not Rows: Lossless Waveform Storage and Lag-Tolerant Retrieval Inside PostgreSQL** (ACM `sigconf` layout).

Not yet submission-ready; see "Before submission" below.

## Build

```sh
.venv-paper/bin/python -I research/paper/figures.py      # fig/*.pdf from committed result files
cd research/paper && tectonic -X compile morletdb.tex    # or: latexmk -pdf morletdb.tex
```

`figures.py` reads only committed results (`evaluation/storage_baselines/results/b1.json`,
`evaluation/retrieval_scaling/results/{load,bench}.json`). The figure PDFs are committed so
the paper builds without Python. Tectonic fetches the TeX packages it needs on first use.

## What changed from draft 0.1

- **New evidence, each from a protocol frozen before measuring:**
  - **B1** (`evaluation/storage_baselines/`): the codec against ALP (DuckDB), Parquet,
    zstd, FLAC and DuckDB integers on identical samples, including two inputs new to the
    storage studies (Sleep-EDF, PTB-XL). None of its three pre-registered claims held;
    the paper reports the weaker results and the correction-stream diagnosis.
  - **R2** (`evaluation/retrieval_scaling/`): exact lag-tolerant search on fresh CX 2025
    data up to about 236k windows, against an HNSW prefilter with exact re-ranking.
- **Reframed contributions** around findings that hold beyond the prototype: calibrated
  floats as a blind spot of decimal-oriented float codecs, and lag tolerance as a change
  of query rather than of speed.
- Architecture figure, SQL example, B1 figure, R2 figure, studies table, expanded related
  work and references with full bibliographic data, X1 reported, F1/F2/CV1 declared out of
  scope, corrected compression citation.

## Before submission

- Authors, affiliations and venue formatting (the layout targets an ACM venue such as
  SSDBM; check the call's page limit).
- References were checked against Crossref/DOI, RFC Editor, NTRS, DataCite and PubMed
  records on 2026-10-31 (DOIs added). Not confirmable from a primary source: ALP's article
  number 230 (dblp only) and the IMS dataset citation (no recommended form or DOI; the
  commonly used form is given). Paderborn data is CC BY-NC 4.0 and asks for credit to the
  KAt-DataCenter.
- Independent read of all numbers against `evaluation/*/results/`; the evidence ledger in
  the appendix maps each headline number to its study.
- Open technical step the paper names but does not take: a grid that replays a source's
  calibration arithmetic exactly (closing the B1 correction gap). It would be a codec
  change with its own frozen evaluation.
- Cold-cache and concurrent-client measurements, if a reviewer asks for latency claims
  beyond warm, single-client runs.
