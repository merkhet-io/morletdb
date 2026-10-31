# MorletDB research manuscript

Working draft 0.1: **MorletDB: Lossless Waveform Storage and Provenance-Preserving Similarity Retrieval in PostgreSQL**.

This is an early manuscript, not a submission-ready paper. It synthesizes existing repository experiments; it does not report new experimental runs. Authorship, related-work coverage, dataset citations, and additional validation remain open and are marked in the manuscript.

## Build

From this directory, with a TeX Live installation including latexmk and the packages used in `morletdb.tex`:

```sh
latexmk -pdf -interaction=nonstopmode -halt-on-error morletdb.tex
```

The self-contained source includes its bibliography; BibTeX and external assets are not required. The build produces `morletdb.pdf`.

```sh
latexmk -C morletdb.tex
```

## Evidence and revision scope

The manuscript distinguishes bitwise sample recovery, agreement with a reference ranking, and domain relevance. Its appendix maps headline numbers to P2, R1b, P1, and S1. Historical timings are kept separate from the later same-run benchmark.

Before submission, audit the reported outputs and implementation revisions, complete primary references, strengthen compression baselines, measure retrieval at larger scales in a common environment, and add an independent confirmation workload. The manuscript's next-revision section describes these as planned work, not completed results.
