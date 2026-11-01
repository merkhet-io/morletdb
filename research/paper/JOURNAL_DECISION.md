# Journal decision (2026-11-01)

Three journals checked against their official pages on 2026-11-01, plus JOSS eligibility; nothing was
submitted, no account created, nobody contacted. Full notes with URLs and quotations:
[`related/journals_2026-11-01.md`](related/journals_2026-11-01.md).

## Decision table

| | **Datenbank-Spektrum** (Springer, GI) | SoftwareX (Elsevier) | JORS (Ubiquity) |
|---|---|---|---|
| Article type | **Fachbeitrag** (regular research article); "Schwerpunktbeitrag" only for themed issues | Original Software Publication | Software Metapaper |
| Fit | research paper on databases and information retrieval: **fits as is** | software note; method and evaluation only as support | metapapers "do not contain research results": would drop the evaluation |
| Contribution required | a scientific contribution (no specific criterion stated) | "Impact" is the main section; evidence of use (users, downloads, citations) expected — we have none yet | substantive, reusable software |
| Length, template, supplement | Springer Nature LaTeX template, two-column option `[iicol]`; abstract 150–250 words; editable sources mandatory; supplement as "Online Resource". **Length limit for a Fachbeitrag: OPEN** (themed calls state 8–10 two-column pages) | own template, at most 4,000 words and 6 figures, code metadata table | own template; length not stated (OPEN) |
| Software, licence, archive | code-availability statement | public GitHub repo with README.md, LICENSE.txt, OSI licence; forked into the journal's organisation | deposit with DOI (e.g. Zenodo), OSI licence |
| **Mandatory cost** | **none** (subscription route) | **USD 1,920** APC (excl. tax) | **GBP 824** APC (+ tax); waiver must be requested at submission |
| Optional open access | Open Choice EUR 2,790 / USD 3,290 / GBP 2,390 (+ VAT) | — (fully OA) | — (fully OA) |
| Deadlines | themed issues have deadlines (open call: cloud-native DBMS, 2027-02-01); **continuous submission of regular articles: OPEN** (regular Fachbeiträge do appear, e.g. 2026-10-26) | continuous | continuous |
| Presentation or attendance | none found | none found | none found |
| Changes needed | reformat to Springer template; abstract ≤ 250 words; declarations; supplement as Online Resource; possibly shorten (OPEN limit) | full rewrite as software note; impact evidence missing | full rewrite as metapaper without results; Zenodo deposit |

**JOSS:** "The repository must have been public for more than six months prior to submission, with active
development spanning that period." The repository was created on 2026-10-25 (first commit 2026-10-23), so
JOSS is not possible before about 2027-04-25. No history is to be back-dated.

## Decision

**Datenbank-Spektrum, Fachbeitrag**, subscription route (no mandatory cost; open access optional). It is
the only option that takes the work as a research article, has no fee, and requires no presentation.
SoftwareX remains the fallback for a software-oriented article, at a mandatory cost and once there is
evidence of use; JORS does not meet the goal of publishing the evaluation.

**Open before reformatting** (verifiable only through the submission system or the editors, both outside
this task): whether regular Fachbeiträge are accepted outside themed issues at any time, and their length
limit. Until then the research version stays the reference and the adaptation below is prepared without
committing to a page target.

## Adaptation plan (section mapping to a Springer two-column article)

| Springer article | From the current manuscript | Change |
|---|---|---|
| Title | unchanged | — |
| Abstract (150–250 words) | abstract (345 words) | replace by the journal abstract below |
| Keywords (4–6) | — | add: waveform retrieval; cross-correlation; vector search; PostgreSQL; HNSW; time series |
| 1 Introduction | §1 incl. name paragraph from §4 | keep question, six-step argument, three contributions |
| 2 Problem and search contract | §2 | unchanged |
| 3 Method | §3 incl. Algorithm 1 | unchanged |
| 4 Integration in PostgreSQL | §4 incl. Fig. 1 | unchanged |
| 5 Evaluation | §5–10 | keep E1 tables, ablation, Fig. 3, filters, storage, maintenance; Fig. 2 and Table 1 are candidates to cut if the limit requires |
| 6 Related work | §11 incl. Table 5 | Table 5 to Online Resource if the limit requires |
| 7 Limitations, 8 Conclusion | §12, §13 | unchanged |
| Declarations | — | add (below) |
| Online Resource 1 | supplement | as is |
| Online Resource 2 | Appendix A (claims and evidence) | move out of the main text |

## Journal abstract (249 words)

Analysts of waveform archives look for intervals that resemble a query up to a small time shift, and
need the original samples of each hit. We treat this as top-k search under a bounded-lag cross-correlation
scorer and ask how it can use a standard vector index while keeping the link to the recording, filters and
transactional updates. A zero-lag shortlist of 1000 from 235,000 seismic windows recovered
74% of the scorer's top ten, exactly or from an HNSW index: the candidate metric, not approximate
search, causes the loss. Because each lag term is an inner product with a shifted copy of the
query, we send a sparse grid of shifted probes to one unmodified inner-product HNSW index and re-rank the
union with the unchanged scorer. Query-side shifting and multi-query candidate generation are known; we
evaluate their combination in PostgreSQL. On held-out stations and a year, probe search recovered 97.7% of
the exact top ten at a p95 latency of 57 ms, against 740 ms for the fastest exact path; zero-lag candidates
from the same index recovered 64–66%. Exact candidates on the sparse grid recovered 99.9%, and float16
probes added no further observed loss, so most of the loss came from approximate candidate selection.
MorletDB returns hits as sample intervals of losslessly stored recordings, with one search contract for
both paths. Its costs are probe structures of about one times the raw data, routing
for selective filters, and write-ahead logging from index maintenance. All retrieval results are for
seismic recordings.

## Declarations (Springer Nature; drafts for the author)

- **Competing interests:** none (author to confirm).
- **Funding:** none (author to confirm).
- **Code availability:** source code under the Apache License 2.0 at
  https://github.com/merkhet-io/morletdb; measured state commit 68594d7; evaluation harnesses, protocols
  and results on the paper branch (commit to be fixed at submission); build and reproduction steps in
  `research/paper/ARTIFACT.md`.
- **Data availability:** CX network data (IPOC), DOI 10.14470/PK615318, retrieved from GEOFON; station/day
  lists, fetch scripts and SHA-256 sums in the repository; waveforms are not redistributed.
- **Use of AI tools:** Springer asks authors to document the use of large language models. Whether and how
  AI assistance in writing code, analyses or text is to be declared is **the author's decision**; it is not
  drafted here.
