# SIGMOD 2027 Industrial submission readiness

Target: [SIGMOD 2027 Industrial Track](https://2027.sigmod.org/calls_industrial_track_papers.shtml).
Deadline: **24 November 2026, 23:59 AoE** (25 November, 12:59 CET/Berlin).
The call allows 12 pages plus references, requires the latest standard ACM layout,
named authors and at least one non-academic affiliation. It prohibits concurrent
submission of substantially overlapping work. A supplementary artifact does not
extend the main-text page limit.

## Authors and scope

Zin Nam: no institutional affiliation; Germany.
Semir Maslo: Sulzer-BDiM; Germany.
The call does not require e-mail addresses or cities in the PDF; contact details are
entered in the submission system only, not in the repository.
Both authors must approve the scientific claims and confirm that the industrial
requirements accurately describe the collaboration. No customer deployment,
production diagnostic accuracy or acceptance is claimed.

The strongest argument is an industrial data-management workflow: preserve original
waveforms and context, return verifiable intervals, share eligibility and transaction
semantics between search paths, and quantify the costs. The public machining studies
are valuable precisely because shape retrieval alone is insufficient: level, duration,
history drift and speed variation change what an engineer should trust. The abstract,
introduction and conclusions now foreground those requirements and limitations.

The submission is `sigmod.tex` / `sigmod.pdf`; the technical report remains
`morletdb.tex` / `morletdb.pdf`, with its longer evidence ledger. Both use the same
body. The submission omits the report-only licence and appendix, without reducing
margins or type size. ACM 2.21 (2026-11-01) was the current CTAN class checked on
16 November. `build_submission.py` pins that source; `check_submission.py` checks
PDF metadata, author names, placeholder headers and reference-page boundary.
This automated check cannot replace visual review or scientific author approval.

## Benchmark readiness

| Evidence | What is available | What remains / permitted claim |
|---|---|---|
| E1, 68594d7 | Archived held-out results, environment and generated tables | Historical 97.7% recall, 57 ms probes vs 740 ms exact; not a current-main speedup |
| E7 screened exact | Archived later development results, within-session comparison | Separate 1.6–2.3× p95 advantage on development galleries; do not combine it with E1's held-out result |
| IU1 T1/T2, A1/A2, 088f03b | Offline replay from committed compressed per-query observations | Run-label evidence; half-year resolution cannot establish causal online history; duration is a strong baseline |
| IU1 T6 | Fixed eligibility/denominator harness, separate rerun file, original observations preserved | **Claim withdrawn.** Run the corrected eligible-gallery experiment before reinstating it; archived corrected-denominator values are not that experiment |
| IU2 W1/W2/W3, 088f03b | Offline replay and raw-window/archived-cycle consistency checks | W3 now BL 1/7, vibration 6/7, RMS 7/7 strictly earlier warnings; small seven-tool confirmation set |
| IU2 external features | Archived per-cycle values are available | Independent regeneration needs the external feature CSV; replay does not prove feature extraction |
| IU1-E | Historical container-limit measurements | Corrected mounts/commands; no fresh database or timing rerun in this PR; desktop cores are not an industrial edge deployment |
| B3/B6 storage and codec | Historical reported versions and paired comparisons retained | Later codec fixes/defaults have not been rebenchmarked here; do not advertise old measurements as current-build results |

## Submission gates

1. Obtain both authors' approval of the final manuscript. Author names and affiliations are complete;
   contact details go into the submission system.
2. Keep the T6 claim withdrawn unless the corrected database experiment is completed,
   checked, and documented as a post-correction rerun. Do not substitute archived
   truncated-list metrics. Examine repeated-query dependence before claiming significance.
3. Choose the performance scope explicitly: this draft reports historical builds.
   A claim about today's system needs a matched fresh run of probes and screened exact,
   including environment, worker settings, query set, filters, recall, latency distribution,
   storage and failures. A current-build held-out result would strengthen the submission,
   but it cannot be fabricated from the existing E1/E7 combination.
4. Run the offline replay, regression tests, number generation, PDF build and packaging
   checks; inspect every page, especially figures/tables and the main-text/reference boundary.
5. Keep the negative results and disclose the public-data/deployment limitations. Semir Maslo
   approved the introduction's statement that the collaboration with Sulzer-BDiM motivates the four
   requirements (2026-11-16, relayed by Zin Nam).
6. Verify the call again, check no concurrent overlapping submission, and upload only
   the approved conference PDF. The technical report 0.26 is tagged `paper-v0.26` on the merge
   commit that brings it to `main`; the conference PDF is not uploaded by that tag.

## Corrections tracked

#509: separate ACM submission and page checks; author fields completed.
#510: T6 harness/denominator corrected and claim withdrawn; database rerun pending.
#511: final-cycle alerts separated from advance warning.
#512: compressed-input/offline replay fixed, base-image recipe and container mounts
made explicit; fresh end-to-end container execution remains unvalidated.
#513: historical build boundaries and screened-exact comparison made explicit.
#514: full-lag top-k lemma corrected; budget effects require the stated interactions.
#515: average-rank Spearman with undefined constant-series behavior.

## Validation of this correction (16 November 2026)

- All four offline analyses replayed and matched the corrected committed summaries:
  IU1, drift, lead-in and IU2, with 10,000 resamples where the original protocol specifies them.
  Local environment: Python 3.11.15, NumPy 2.4.6; CI is configured for Python 3.12 / NumPy 2.1.3.
- Five regression tests passed (ties/constants, gzip/missing inputs, preservation of
  published output on failure, archived T6 diagnostics, failure-cycle classification).
- All three PDFs built with ACM 2.21: submission 14 pages with references beginning
  on page 12; report 16 pages; supplement 8 pages. Submission pages visually inspected.
  Minor TeX overfull/underfull warnings remain; no missing references or build failures.
- Generated numbers, E1 distribution, website PDF equality, Compose configuration and
  Python compilation checked. No database, Docker image build, or new timing run.
