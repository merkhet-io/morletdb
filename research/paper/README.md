# MorletDB research manuscript

Working draft 0.5: **Recordings, Not Rows: Lossless Waveform Storage and Lag-Tolerant Retrieval Inside PostgreSQL** (ACM `sigconf` layout).

Not yet submission-ready; see "Before submission" below.

**Preview:** [morletdb.pdf](morletdb.pdf) (committed build of the current source; rebuild and recommit it when the source changes).

## Build

```sh
.venv-paper/bin/python -I research/paper/figures.py      # fig/*.pdf from committed result files
cd research/paper && tectonic -X compile morletdb.tex    # or: latexmk -pdf morletdb.tex
```

`figures.py` reads only committed results (`evaluation/storage_baselines/results/b1.json`,
`evaluation/retrieval_scaling/results/{load,bench}.json`). The figure PDFs are committed so
the paper builds without Python. Tectonic fetches the TeX packages it needs on first use.

## What changed in draft 0.5

New results since 0.4, all merged on `main`:
- **Faster exact scan (#64):** exact search now runs as one parallel statement, about 2.5× faster with identical results. Noted in the design section and next to R2's exact-scan figures, which describe the original implementation.
- **Filtered search (SS3, #66 and #69), new Section 6.5 and Table 6:**
  - post-filtered probes lost up to half the top ten under selective filters;
  - routing plus filter-aware probes keeps recall at 0.977–1.0, with no filter or RLS violations;
  - the latency verdict was narrowly missed (0.8 ms at the unfiltered level).
- **One stored representation (SS1, #65), new Section 6.6:** exact and saves about 250 MB per 235k windows, but costs 23% exact or 27–32% probe latency. The two-copy layout stays the default.
- **Building representations (CW1, #67 and #68), new Section 6.7:** external workers are exact and crash-safe and move 90% of database CPU. Batching the in-database builder gave a 4.2× cut in database CPU per window, which is most of that benefit.
- **Kept in step:** abstract, design, studies table, limitations, conclusion, evidence ledger and references.

## What changed in draft 0.4

- **Shifted-probe search (BL1, merged in #62/#63)** becomes the paper's constructive
  retrieval contribution: the identity between lag terms and inner products with shifted
  queries; development result; held-out confirmation on CX stations and a year no study had
  read (recall 0.976, p95 131 ms vs 2,854 ms); the product function on the same queries
  (0.977, p95 50 ms); maintenance during replayed ingestion (BL1-S).
- New Section 6.4, Table 5 and Figure 4 (recall by best lag, from committed results);
  probe search added to the design section, studies table, related work (k-Shape, MASS,
  FreshDiskANN, VStream; no novelty claimed for multi-probe search in general),
  limitations, conclusion and evidence ledger.
- References added from the BL1 related-work matrix use only details verified there;
  first names of the VStream and FreshDiskANN authors still need checking.

## What changed in draft 0.3 (review of 0.2)

- **R2-D diagnostic** (`evaluation/retrieval_scaling/diagnostic/`, protocol frozen before
  measuring; consumed data, not a confirmation). Exact zero-lag shortlists (E-K, plan-checked
  sequential scans) against HNSW shortlists, both re-ranked exactly.
  - Found that **R2's HNSW arms never used the index** (subquery query vector; planner ran an
    exact scan). Logged in R2's protocol and report; an amendment added H\*-K with the
    index actually used.
  - Result: H\*-1000 recall 0.744 vs E-1000 0.743 at 235k windows; shortlist agreement
    0.98–1.00; coverage = recall. The loss is the zero-lag candidate metric, not ANN.
- **Storage overhead:** 12.9% (R1b) vs 60.2% (R2) of raw now in the abstract and Section 6,
  with a measured decomposition (stride ×2.0, raw compressibility ×2.46, overheads ×0.95)
  and which structures coexist or could replace each other.
- **Narrowed claims:** compression findings tied to the tested DuckDB ALP implementation,
  the nominal quantum and the fixed Parquet encoding; the correction stream explains the
  extra cost over MorletDB's integer path, not the whole FLAC gap; calibration replay is an
  untested hypothesis; linear scaling and the ~8,000-window mark qualified as measured range
  and interpolation; freshness stated per data slice.
- **Corrections to earlier reports:** R2's slice overlaps 0.1% of samples read by R1;
  B1's Sleep-EDF input had been measured for MorletDB storage by the offset-grid check.

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

- Venue formatting (the layout targets an ACM venue such as SSDBM; check the call's
  page limit and whether it is double-blind, which would need the author line removed).
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
