# Remaining evidence for the paper

**Status (draft 0.8):** E1 was run (`evaluation/consolidated/`, H1 and H2 hold on both databases). E2 is not started.

## Original plan: two experiments

After the literature and evidence review for draft 0.7, two gaps decide whether the paper's claims
hold as stated. Each needs a frozen protocol committed before any measurement, and the owner's go.
Existing data stay development data; consumed holdouts are not used to tune anything.

## E1. Consolidated comparison on one frozen implementation

**Gap.** Development, confirmation, product checks, filtered search and load were measured on
successive implementation states (Limitations, "One implementation state per study"). Ratios across
studies mix states, and the speed-up is quoted against the fastest exact path of the frozen BL1-C
comparison (harness statement, 713 ms p95), not against the current product exact path.

**Hypothesis.** On one frozen commit, the product probe function keeps recall@10 ≥ 0.95 against the
product exact function and has p95 ≤ 1/3 of the product exact function's p95, unfiltered, on a gallery
of about 235,000 windows; and the ordering of arms in Table 2 (PX ≥ probes ≫ zero-lag at equal count)
is reproduced.

**Arms (all on the same commit, same queries, same session settings).** Product exact (one parallel
statement); product probe function (routing disabled, unfiltered); probes with exact candidates (PX);
zero-lag HNSW shortlist of equal count; full-lag exact candidates (PX-61). Report mean, minimum,
queries below 0.9, recall by best-lag band, p50/p95/p99, and machine load average.

**Data.** Development data (R2's database) for the full comparison. The held-out BL1-C database was
consumed by the confirmation and by SS3; it may be **re-measured** with the frozen configuration
(no parameter changes) to report product-state numbers, labelled as a re-measurement, not a new
confirmation.

**Budget and stop rule.** At most 2 hours of measurement, on a machine without the unrelated
background job if available (otherwise record load and repeat each arm in two interleaved rounds).
One run per arm and round; stop after one full run. If the hypothesis fails, report the failure and
the measured ratio; do not tune.

## E2. A second signal domain

**Gap.** All retrieval evidence is seismic envelopes. The 13-probe grid is explained by envelope
smoothness on that data; whether it transfers is the main open question for a claim about
"waveforms".

**Hypothesis.** On vibration recordings, with the same extractor and the probe configuration frozen
before measurement, (a) a zero-lag shortlist of equal count recovers clearly less of the bounded-lag
top ten than probes (difference ≥ 0.2), and (b) probes reach recall@10 ≥ 0.95 at p95 ≤ 1/3 of the
exact path. Additionally, the grid-spacing analysis (envelope autocorrelation at offsets 2 and 5)
predicts whether spacing 5 suffices, checked against exact-candidate controls.

**Data.** Development: an existing, already-read vibration collection (Paderborn or the Bosch CNC
data), used only to choose window size, stride and gallery construction. Confirmation: a vibration
dataset never read by any study, chosen from documentation before download (download needs the
owner's approval), loaded to about 200,000 windows.

**Budget and stop rule.** One development pass and one confirmation pass, at most one working day of
measurement and 50 GB of disk. If the development pass shows that the envelope representation is
not meaningful for the data (for example, near-constant envelopes), stop and report; do not change
the extractor for the confirmation.

## Not planned

Long-term stability (the two-hour load run C4 is running separately as product hardening) and task
relevance studies are outside the paper's claims and are not proposed here.
