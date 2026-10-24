# From prototype to product

Status: reconciled with merged PRs #15, #16 and #17. This roadmap describes the
current prototype and remaining evidence; documenting a gate does not satisfy it.
The [architecture decision](architecture.md) defines the canonical Rust/continuous
recording path. The [README](../README.md) states the current contracts.

## Product promise and customer hypothesis

Import uniformly sampled recordings, select an interesting interval, find related
intervals within a compatible acquisition cohort, and inspect the original samples
and provenance. A developer embeds this workflow through SQL and a thin Python
client. Acquisition control and automatic diagnosis remain outside this promise.

The target customer hypothesis is a signal-data or ML infrastructure team that
reuses recorded sensor data for development, testing or investigation, including
defense technology companies. Industrial monitoring teams can validate shared
infrastructure needs. Industrial or laboratory results do not establish defense
requirements, adoption or relevance. Validate one recurring task with an external
engineer before expanding the product surface.

## Delivery status

| Milestone | Evidence now | Remaining gate |
|---|---|---|
| M1: runnable preview | Versioned PostgreSQL 17 container recipe, synthetic Parquet walkthrough, persistent-volume restart/replay CI | x86-64 Linux preview only; image tags/apt inputs are not content-addressed release artifacts |
| M2: interval retrieval and inspection | Cross-chunk reads, stored-interval search, source exclusion, overlap suppression, Python client and permission contracts | External developer usability and usefulness remain unvalidated |
| M3: validated pilot | Small run-separated CWRU relevance report and independent-oracle 10k-candidate CI smoke | Partner-approved tasks/data, harder negatives, useful retrieval and representative workload/recovery envelope |
| M4: operable release | Operational requirements identified below | Upgrade/install artifacts, backup/restore, observability and supported limits still required |

M1/M2 provide an implemented prototype workflow, not a supported production release.
See the [walkthrough](recording-workflow.md), [evaluation suite](../evaluation/README.md),
[real-data report](../evaluation/reports/cwru-007-cross-load-v1.json) and
[passed preview/scale run](https://github.com/merkhet-io/morletdb/actions/runs/40026868636).
Complete the remaining M3/M4 gates before claiming supported production readiness.

## Next evidence

1. Have an external engineer repeat the recording investigation workflow. Agree
   the question, current effort, acquisition context and useful-result criteria.
   Obtain permission for data access and identify one owner of the evaluation.
2. Extend relevance evaluation with transient events, similar-energy/different-order
   distractors, negative queries and independent acquisition runs or machines.
   The current CWRU slice ties simple statistics; it does not prove differentiation.
3. Run the manual 100k/1m exact-search experiments and representative ingestion/
   recovery workloads. Record failure cases and hardware/settings. Use the results
   to decide whether indexing, asynchronous extraction or external raw storage is
   justified, rather than adding all three speculatively.
4. Optionally validate modest-hardware deployment on the Raspberry Pi 3B+: establish
   an ARM64 build and recorded-data parity before live capture, then measure memory,
   processing/ingest rate, latency, storage and throttling. ARM deployment and live
   sensor capture are proposed experiments, not implemented or verified capabilities.

## Retrieval quality and scale gates

Use partner-approved recordings and reviewed relevance labels. Split by acquisition
run and, where possible, asset so adjacent windows cannot leak across evaluation
sets. Exclude the query source and collapse overlapping hits before scoring.
Include negative queries and changed operating conditions. Report precision@10,
recall@10 where labels are sufficiently complete, coverage and failures by cohort.
Compare statistics, FFT, current pooled Morlet features and temporal alternatives.
Agree usefulness thresholds before inspecting held-out results.

The committed CWRU slice contains six laboratory recordings, three fault classes
and different query/gallery loads. It has no healthy class or negative queries and
does not hold out independent bearings or machines. Morlet ties the statistical
baseline. Tests exercise negative-query behavior but do not calibrate a threshold.

Exact stream search defaults to 4096 eligible segments. Explicit budgets up to
1,000,000 permit experiments and still fail when exceeded; there is no approximate
candidate index. The passed 10,000-candidate smoke uses synthetic feature fixtures
and an independent ranking oracle. It is not raw-waveform ingestion or production
capacity evidence. Broader sweeps are available in the manual benchmark workflow;
100k/1m performance has not been established by that smoke.

For representative workloads, measure ingest samples/s, extraction CPU, query
latency distributions, bytes/sample, WAL growth, memory and recovery under writes.
Repeat trials on specified hardware, including data larger than RAM, and publish
provenance, extraction versions and raw reports. Keep exact search as the reference
for any future approximate retrieval and report its recall separately from relevance.

## Architecture boundaries

Keep the portable Rust core and continuous-recording PostgreSQL integration as the
active path. The Go app and bounded JSONB/typed storage variants are retained legacy
experiments, not parallel product implementations to extend.

Extraction remains synchronous. Introduce background processing only when measured
workloads justify durable jobs, received/indexed watermarks, retries, backpressure
and explicit visibility semantics. Separate recording identity from representation
specifications when implementing re-extraction or multiple representations; that
schema migration has not been supplied yet.

Object storage, other engines, Iceberg, streaming connectors, multi-node execution
and managed hosting follow demonstrated requirements. Resampling, gap handling,
multi-channel synchronization and automatic diagnosis are not implied by the
current single-channel uniformly sampled model.

## Operational release checklist

- [ ] Supported platform matrix and reproducible install artifacts with checksums and license notices.
- [ ] Extension SQL versioning and tested upgrade path; explicit representation migration/re-extraction policy.
- [ ] Backup/restore and interrupted-ingest recovery drills beyond current restart/replay coverage.
- [ ] Documented application roles and full permission/RLS matrix across parent, chunks and segments.
- [ ] Resource bounds, timeouts, error contracts, retention/deletion and observable failures.
- [ ] Client compatibility policy, troubleshooting, changelog and vulnerability-reporting route.
- [ ] Partner relevance/performance reports and a supported workload envelope with failures disclosed.
- [ ] Pilot onboarding and support ownership agreed with each partner.

Existing installed 0.1.0 extensions do not acquire new SQL APIs merely by replacing
the shared library. The walkthrough targets fresh disposable databases; no upgrade
path or production support contract is claimed.

## Working cadence

Each PR should advance a specific user workflow or resolve a measured uncertainty,
state acceptance evidence and remaining limits, and remain reviewable. Update gates
with evidence rather than estimates. Engineering owns tooling, correctness and
measurement; founder work secures participants, approved data, recurring tasks and
usefulness criteria. Outreach requires an explicit instruction and is not performed
by maintaining this roadmap.

Track time to a useful result, relevance, repeat usage and blocking failures. Narrow
the workflow or revise the representation/architecture when evidence warrants it.
