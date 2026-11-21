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

**Customer hypothesis: MorletDB should be strong at investigation and explanation.** Teams that keep recorded
sensor data, from machines, test benches or laboratories, need to find out why something happened: where a signal
like this occurred before, under which operating conditions, and what was found then. MorletDB answers with
recordings and sample intervals that read back the original samples, their metadata and context, so that an
engineer or an AI agent can explain a finding with evidence that anyone can check. Detection, diagnosis and the
decision stay with the user's application. Validate one recurring investigation with an external engineer before
expanding the product surface.

## Direction: from a similar waveform to a comparison an engineer can defend

MorletDB already returns samples and provenance. The next step is to make the reasoning around those results
explicit, so that an investigation ends in a comparison an engineer can defend, not in a nearest match. The useful
answer reads like this:

> This transient resembles three previous events after a small time alignment. Two occurred during tool changes and
> one during a confirmed bearing fault. The current operating phase matches the tool-change examples; waveform
> similarity alone does not distinguish the causes.

That is more useful than "nearest match: bearing fault". Seven capabilities lead there:

| | Capability | What exists today | What is missing |
|---|---|---|---|
| 1 | **Search within comparable operating conditions:** the same machine, sensor position, tool, program phase, speed or load, and which conditions match and which differ. A similar event under a different load may be a poor precedent | Metadata filters on every search; context segments and values, and comparison with the same context in a machine's own history ([context workflow](context-workflow.md)); a first investigation snapshot of matches, context comparison and coverage (`Client.investigate`, MCP `investigate_interval`; #576, #577) | Search restricted to matching context, and a per-match report of which conditions match and which differ (#572, #574) |
| 2 | **Explain what made each interval similar:** the aligned waveforms, the selected time shift, the amplitude difference, the regions that contribute to the match; envelope shape distinguished from frequency content. Computed from the scorer, never an LLM's guess | The exact bounded-lag score; searches return a distance only | The winning lag, scorer inputs and per-position contributions of the score (#573), and their display in the viewer (#574) |
| 3 | **Retrieve contrasting evidence:** normal runs, similar signals with different outcomes, known faults that look different. "Does this pattern also occur during successful operation?" | Labels on intervals; search that excludes labelled intervals (`morlet_search_similar_excluding`) | Outcome-aware retrieval: the nearest matches per label or outcome, including reviewed normal runs, side by side (partly #572, #574) |
| 4 | **Make archive coverage visible:** what was searched, which filters and exclusions applied, whether candidate selection was approximate, whether missing representations or retained-data limits restricted it. "No useful match in this search" stays distinguishable from "this has never happened" | `morlet_coverage` per recording; a coverage summary and `evidence_states` with every investigation (#576); the MCP server's `scope` per search (the searchable windows and the periods not searched; it does not say which search path ran or how many windows probes actually scored); the [search contract](search-contract.md) defines budgets, exclusions and the probe path | The effective filters, exclusions, the search path taken and the windows actually scored, with every SDK search (part of #572) |
| 5 | **Turn investigated events into reviewed cases:** observations, operating context, maintenance findings, conclusions and supporting intervals, with who made each claim and whether it is suspected, confirmed, disputed or superseded; an observed fault distinguished from an inferred cause | Labels with author, time and retraction; context records | Cases with claims, their status and their supporting intervals (#216, #217, #337) |
| 6 | **Produce a reproducible evidence report:** the query interval, search settings, representation version, searched scope, results and annotations, every factual claim linked to its interval or external record, repeatable where the data remains | Recording IDs and sample intervals that read back the original samples | The report itself: versioned manifests and self-contained reports (#575; stage M2 of the [wave-RAG specification](wave-rag-spec.md)) |
| 7 | **Constrain AI explanations to the evidence:** an assistant drafts observations, possible interpretations and unresolved questions; signal claims need interval references; it surfaces contradictions and missing evidence and can say "the retrieved examples do not distinguish these explanations" | The read-only MCP server and its tools | Explanation drafts in that structure, checked against the report's intervals |

**Order.** Contextual comparison (1), scorer explanations (2) and reproducible reports (6) first, built on the
existing context lanes, labels and viewer; then contrasting evidence, coverage, cases and constrained AI
explanations.

**Evaluation.** Each capability is evaluated on held-out investigations, under a protocol frozen before the data
are read: time to a supported conclusion, correctness, unsupported claims, and whether insufficient evidence is
recognized as such. Better-looking explanations alone do not count as improvement.

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
