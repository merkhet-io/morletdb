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

## Experiments retained, not expanded

| Component | Status |
|---|---|
| Rust core and continuous recording SQL | Active prototype path |
| Go server, HTTP API and monitoring dashboard | Frozen reference demo; critical fixes only |
| Bounded-recording SQL, JSONB grids and typed composite arrays | Legacy experiments; no new product features |
| JSONB/typed/DuckDB comparison harness | Historical storage experiments, not a backend selection |

Existing APIs remain installable to avoid gratuitous breakage and preserve tests.
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

## Next delivery slices

1. Complete cross-chunk reads, stored-interval queries, source exclusion and
   overlap suppression, with one container environment and Python walkthrough.
2. Evaluate real approved recordings alongside synthetic controls. Compare basic
   statistics, FFT bands and Morlet representations using held-out recordings.
3. Measure larger exact-search populations, ingest/search resource use and
   recovery before choosing candidate indexes or external raw storage.

The first milestone is a useful repeatable investigation. Production support,
asynchronous extraction and approximate retrieval need separate evidence.
