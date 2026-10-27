# Alignment-aware retrieval: evidence and gated experiment

Status: proposed experiment; not a product commitment or frozen evaluation protocol.
Date: 2026-10-27.

## Decision

Investigate bounded temporal comparison as a generic waveform retrieval capability.
Do not commit to DTW, a new index, reconstruction, or a CNC-specific API.
Implement an offline reference experiment before changing the extension schema.
This proposal supplements [advanced retrieval](advanced-retrieval.md); it does not
replace its representation and lifecycle requirements or expand its implementation
stages automatically.

## Evidence and corrections

The [timing evaluation](../evaluation/decision/results/timing-012bb3d/RESULTS.md)
supports only a specific result: same-station earthquake retrieval by similarity
of catalogue hypocentral distance benefited from temporal representations.

- Time-binned spectrogram vs pooled FFT/statistics reduced distance error by
  21.0 km [19.6,22.7].
- Temporal Morlet improved on the time-binned spectrogram by 1.6 km, below the
  predeclared 5 km margin.
- Envelope cross-correlation outperformed temporal Morlet by 6.0 km.
- Reported XC search time was 8.8 ms/query on that gallery, versus <=0.01 ms for
  vector methods. This is not a PostgreSQL end-to-end latency or scale guarantee.

This does NOT demonstrate DTW benefit, waveform semantic equivalence, general
industrial utility, a novel algorithm, or the need for a dedicated database.
The comparison also used unequal temporal resolution: XC at 10 Hz versus four
time bins for the temporal features. Catalogue-predicted window placement helped
alignment-sensitive methods. Cross-correlation permits a global time shift;
DTW permits local temporal deformation. These are different hypotheses.

The 2024 set has already been examined. Reusing it is regression/exploratory
analysis, never a new untouched confirmation.

## Questions, tested separately

H1: Does bounded offset alignment improve useful retrieval over unaligned
temporal comparison?
H2: Does constrained local warping improve it over bounded offset alignment?
H3: Can a cheap shortlist preserve exhaustive aligned retrieval quality while
reducing end-to-end work?
H4: Is the resulting quality/cost tradeoff useful in two distinct workloads?

A negative H2 does not invalidate H1. A positive H1/H2 does not establish H3.
Two positive workloads establish evidence on those workloads, not universality.

## Experiment stages

### E0: implementation and regression

Reproduce the existing seismic XC ranking and metrics with its original
preprocessing. Investigate discrepancies before adding methods. Pin source
revision, dependencies, hardware, threads and input hashes.

Use small synthetic fixtures for shift recovery, ordering, amplitude changes,
duration changes, silence, repeated patterns and boundary behavior. Synthetic
success proves numerical behavior, not usefulness.

### E1: exhaustive offline comparison

Use identical eligible galleries, source exclusions, overlap suppression and
query intervals for all methods. Compare:

1. Current pooled statistics/FFT/Morlet baselines.
2. Unaligned temporal envelopes.
3. Bounded envelope cross-correlation.
4. Constrained DTW on the same envelopes.

Include a time-binned spectrogram with temporal resolution comparable to the
envelope, reporting its extra dimensionality and cost. Preserve the original
four-bin baseline separately. Select one primary configuration on development
data; report alternatives as exploratory.

Specify envelope extraction, smoothing, decimation/anti-alias policy, amplitude
normalization, overlap at shifted boundaries and minimum coverage. Fix lag range,
DTW band in physical time and samples, step constraints, endpoint rules, local
cost and path-length normalization. Prevent tiny-overlap matches. Do not bridge
gaps or invent samples. Constants and unsupported inputs return explicit status.

Keep raw amplitude and duration available beside normalized shape scores.
Return signed lag for XC; for DTW retain path, maximum displacement and
stretch/compression diagnostics. Similarity is not confidence or diagnosis.

### E2: independent confirmation in two workloads

Seismic:
- Keep catalogue-distance relevance as an explicitly limited physical proxy,
  not a label of the same earthquake mechanism.
- Examine realistic detector-onset uncertainty on development data.
- Use a genuinely unexamined period or independently sourced cohort for
  confirmation after its availability and independent event count are checked.
- Split and resample by event, excluding closely related events consistently;
  never split correlated station windows independently.
- Preserve the existing no-match task, with catalogue incompleteness disclosed.
- Local warping may erase meaningful P-S timing. It is allowed to lose.

Repeated physical operations:
- Use machine operations only if accessible recordings contain repeated
  episodes, trustworthy operation identity and independent relevance evidence.
- Match same physical operation under a predeclared range of legitimate timing
  variation; include different operations, omitted stages and abnormal duration
  as hard negatives.
- Controller commands alone do not confirm physical execution.
- Hold out whole runs/parts and sessions; use tool/asset holdouts where available.
- If suitable data or reviewers are unavailable, mark this workload blocked.
  Do not substitute synthetic evidence or claim cross-domain validation.

Use independently judged relevance for a blinded subset, with a second reviewer
and disagreement reporting. Hide scores, method identity and neighboring labels.
Record whether labels are observed, inferred or unknown.

### E3: shortlist and database experiment, only after E1/E2 warrant it

Rank the exhaustive aligned reference first. Then test multiple candidate
budgets using cheap envelopes or feature representations. A pooled feature
shortlist can discard exactly the ordered matches this experiment seeks.

Measure both recall@10 against exhaustive ranking (with explicit tie handling)
and domain relevance loss. Include candidate generation, filtering, raw/derived
reads, reranking and result assembly in latency. Report eligible/scored counts,
bytes read, memory, representation storage and preprocessing cost.

Only then test a bounded PostgreSQL adapter on frozen source extents. No new
general planner or persisted index is authorized by this document.

## Protocol freeze and promotion gates

Before accessing fresh confirmation data, commit a workload-specific manifest:
sources and permissions, hashes, independent units, splits, relevance definition,
query sampling, exclusions, primary method/metric, practical improvement margin,
no-match tolerance, latency/work/memory/storage budgets and hardware.

This document deliberately does not invent those budgets without workload
requirements. Until they are numerical and frozen, results are exploratory and
cannot trigger promotion.

Use paired uncertainty intervals clustered by the independent unit. Report all
queries, failures and subgroup results, not only successful matches.

- Quality: exceed the frozen practical margin over the strongest simple baseline,
  with the paired interval lower bound above zero; respect the false-match limit.
- Cost: satisfy frozen end-to-end budgets at stated gallery sizes.
- Approximation: meet frozen candidate recall and relevance-loss bounds.
- Generality: both real workloads must pass before claiming evidence in two domains.

Decisions:
- XC wins, DTW does not: pursue bounded correlation only if cost warrants it.
- DTW helps only one workload: expose an opt-in task-specific metric.
- Shortlisting loses essential matches: revise candidate generation or retain a
  small exact-search scope; do not disguise partial retrieval as exact.
- No meaningful gain: retain simple retrieval and publish the negative finding.
- Quality improves but cost fails: retain research code; defer database promotion.

## Future database contract, conditional

A comparison result must identify source intervals, representation/preprocessing
version, metric parameters, alignment diagnostics, coverage, candidate/scored
counts, snapshot and exhaustive/approximate/incomplete status. Raw observations
remain immutable. Alignment neither repairs data nor proves physical equivalence.
Any later reconstruction is a separate derived recording with provenance.

## First deliverable

One offline benchmark PR containing baseline reproduction, independent metric
checks, synthetic alignment fixtures, development-only ablations, a timing and
memory report, and a proposed frozen confirmation protocol. It must state
continue/narrow/stop and identify missing data. No DTW SQL API, automatic label
propagation, raw-data correction or new storage engine in that PR.
