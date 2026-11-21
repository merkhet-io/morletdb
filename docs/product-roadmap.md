# Roadmap: from beta to 1.0

Status: updated 2026-11-21, at release 0.4.0-beta.3. The gates and their evidence are tracked in
[#201](https://github.com/merkhet-io/morletdb/issues/201); this page summarizes them. Documenting a gate does not
satisfy it. The [architecture](architecture.md) describes what exists, the [support boundaries](../SUPPORT.md) what
the beta supports.

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

## Where it stands

The server edition is in **beta** since 0.4.0-beta.1 (2026-11-19); 0.4.0-beta.3 is the current release. From the
beta on, the [API and on-disk promises](api-stability.md) apply: stable SQL functions keep their behaviour, stored
chunks stay readable, and every release upgrades in place, tested in CI from every earlier release. The embedded
SQLite edition is **experimental** ([embedded edition](embedded-edition.md)).

Beta gates passed (each issue holds its evidence):

| Gate | Issue |
|---|---|
| Power cut and crash consistency on real hardware (study P1a) | #190 |
| Replication, failover, backup and point-in-time restore drills | #192 |
| Integrity scrub, `morlet_verify`, for chunks and derived data | #193 |
| SQL API and on-disk format frozen for the beta, enforced in CI | #195 |
| Operating envelope published (study L1) | #318 |

What the beta also has: Debian packages for PostgreSQL 17, 18 and 19 and a release image with SBOM and build
provenance, on amd64 and arm64; runbooks; the steward for unattended upkeep; a read-only MCP server; the Python
SDK; and the frozen [evaluations](evaluations.md), including negative results.

## Open before 1.0

| Gate | Issue | State |
|---|---|---|
| Multi-day soak | #191 | Soak S1 (one week, a 2-core box with a hard disk) kept every sample but did not stay steady ([results](../evaluation/soak_s1/RESULTS.md)). 0.4.0-beta.3 fixes the two causes found (#527, #528); the confirmation soak S2 (#529) is drafted, not yet run |
| External security review of the extension and the MCP server | #194 | Open |
| Production packaging (RPMs, apt repository, PGXN, Helm) | #196 | Debian packages and images exist; the rest is open |
| Operations: metrics exporter, capacity planning | #197 | Runbooks and steward metrics exist; open items in the issue |
| Nightly performance regression benchmarks on fixed hardware | #198 | Open |
| Pilot deployments with real users, several weeks each | #199 | Open |
| Governance: second maintainer, release cadence, LTS branch | #200 | Open |

Also before 1.0 ([API stability](api-stability.md#still-to-do-before-10)): probe rows in the upgrade test, experimental
functions marked in the documentation pages, and the undocumented older functions that look public either documented
or deprecated.

**Exit criteria for 1.0** (from #201): the reliability drills pass (power cut, soak, failover and restore); the
external security review is done and its findings fixed; the operating envelope is published; the SQL API and
on-disk format are frozen; at least two pilots have run for weeks. Semantic versioning starts at 1.0.

## Embedded edition

The SQLite edition (#351) stores and reads recordings and moves them to and from the server losslessly; it has no
search. Criteria for moving it from experimental to beta have not been set yet.

## Retrieval evidence

Every evaluation fixes its protocol before reading the data, runs once and reports its result
([evaluations](evaluations.md)). What remains open is the part that needs other people: tasks and data from real
users, with relevance judged by them, negative queries and held-out machines. Results so far do not establish
diagnostic relevance or a general advantage of Morlet features over simple baselines. Exact search stays the
reference; approximate retrieval reports its recall against it, separately from relevance.

## Proposed, not scheduled

The [advanced retrieval proposal](advanced-retrieval.md) and the [wave-RAG specification](wave-rag-spec.md) are
designs for review. Object storage beyond S3 import and export, other engines, multi-node execution, managed
hosting, resampling, gap handling, multichannel synchronization and automatic diagnosis follow demonstrated
requirements; open requests are the `enhancement` issues.

## Working cadence

Each PR should advance a specific user workflow or resolve a measured uncertainty,
state acceptance evidence and remaining limits, and remain reviewable. Update gates
with evidence rather than estimates. Engineering owns tooling, correctness and
measurement; founder work secures participants, approved data, recurring tasks and
usefulness criteria. Outreach requires an explicit instruction and is not performed
by maintaining this roadmap.

Track time to a useful result, relevance, repeat usage and blocking failures. Narrow
the workflow or revise the representation/architecture when evidence warrants it.
