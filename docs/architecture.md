# One recording workflow

MorletDB's active development path is a portable Rust signal core, a PostgreSQL
17 integration, and Python ingestion and inspection tools. The first workflow is
**import a recording, query an interval, inspect related intervals and provenance**.
It is an early prototype, not a supported production database or diagnostic system.

## Canonical model

A recording is an append-only, uniformly sampled channel with an ID, sample rate,
unit, acquisition metadata and global half-open sample coordinates. Both short
files and long streams use the same recording/chunk/segment tables and append
contract. Chunks are transport/storage boundaries; search windows can cross them.

`crates/morlet-core` owns numerical semantics and chunk extraction.
`extensions/pg_morlet/sql/streams.sql` owns the active SQL recording API.
`tools/ingest` imports existing recordings. Python clients call these SQL APIs;
they must not implement another extractor or storage engine.

Raw samples remain authoritative. `morlet-energy-v1` is a baseline representation,
not a promise that spectral similarity equals the event similarity a user wants.
Its search features pool energy across each window; localized tokens are not a
temporal search index. Representation changes require versioning and evaluation.
See the [advanced retrieval proposal](advanced-retrieval.md) for a staged design
covering temporal energy grids, wave-token indexes, ordered motifs and ML datasets.
The [offline wave-index PoC](wave-index-poc.md) now experiments with localized
energy cells, exhaustive temporal scoring and approximate candidate postings.
Database generations, migrations and production retrieval remain proposed.

## Experiments retained, not expanded

| Component | Status |
|---|---|
| Rust core and continuous recording SQL | Active prototype path |
| Go extractor and fixture generator | Frozen numerical reference in tools/reference-go; no runtime |
| Bounded-recording SQL, JSONB grids and typed composite arrays | Legacy experiments; no new product features |
| JSONB/typed/DuckDB comparison harness | Historical storage experiments, not a backend selection |

Existing experimental SQL APIs remain installable to preserve tests. The legacy Go server, CLI and dashboard have been removed; their source remains in Git history.
New examples and features target the continuous model. The Go fixture generator
remains available for deliberate numerical-contract maintenance, not normal use.

## Decisions and boundaries

- PostgreSQL is the first integration, not an irreversible requirement to keep
  every raw archive in database arrays. Keep the core independent of pgrx.
- Keep extraction synchronous until measured partner workloads justify durable
  background jobs. Do not introduce a queue merely to anticipate scale.
- Keep bounded exact search as a reference. Broader search must expose its budget;
  never return a silently truncated candidate population.
- No new storage engine, hosted service, acquisition controller or automatic
  diagnosis is needed to evaluate the first workflow.
- Recording identity and extraction specifications should eventually be separate
  entities to support re-extraction. The current single checkpoint representation
  is an explicit limitation; this consolidation does not claim a schema migration.
- Approved source hashes, acquisition timestamps and calibration can be retained
  in metadata today. Formal multi-channel synchronization, gaps and calibration
  schemas require partner evidence before implementation.

## Delivery status and next work

Cross-chunk reads, stored-interval queries, source exclusion, overlap suppression
and the container/Python walkthrough are implemented. A small CWRU evaluation
and a 10k feature-only ranking smoke are implemented; neither establishes
differentiation or production capacity.

Next: independently exercise onboarding, evaluate harder partner-approved
recordings with negative queries, and measure sustained ingest/search resource
use before selecting indexes, asynchronous jobs or external raw storage.
See [support boundaries](../SUPPORT.md) and [evaluation](../evaluation/README.md).

## Toward waveform evidence for RAG

The [wave-RAG specification](wave-rag-spec.md) proposes interval annotations,
versioned evidence export and an optional grounded-answer application. These are
not implemented. Start with annotation/revision permissions and a deterministic
report over the existing exact SQL retrieval; generation stays outside PostgreSQL.
Multiple durable representations still require advanced-retrieval stage A1.
