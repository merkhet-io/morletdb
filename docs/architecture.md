# Architecture

Status: describes release 0.4.0-beta.3 and `main`. What each part promises is in the maturity table below;
the [support boundaries](../SUPPORT.md) say what the beta supports, and the
[roadmap](product-roadmap.md) what remains before 1.0. When this page and the code disagree, the code and the
[README](../README.md) win; please open an issue.

## Editions and maturity

MorletDB has two editions that store the same samples bit for bit, with the same codec:

- the **server edition**, PostgreSQL with the pg_morlet extension. This is the product, in beta;
- the **embedded edition**, a recording in one SQLite file inside a program. It is experimental.

| Part | Path | Maturity |
|---|---|---|
| PostgreSQL extension | `extensions/pg_morlet` | **Beta.** Every SQL function is classified stable, experimental or internal ([API stability](api-stability.md)); stable functions keep their signature and behaviour, experimental ones may change between minor releases |
| Chunk formats (Grizzbit) | `crates/grizzbit` | **Frozen for reading:** chunk format versions 2, 3, 5 and 6 stay readable by every later release of the same major version ([chunk format](chunk-format.md)) |
| Portable core | `crates/morlet-core` | Internal library of both editions: envelopes, features, summaries, checkpoints; no PostgreSQL dependency |
| Python SDK | `sdk/python` | **Beta** (0.1.0b2), for the server edition ([Python SDK](python-sdk.md)) |
| Embedded edition | `crates/morlet-sqlite`, `morletdb.open` in the SDK | **Experimental** (#351): one writer per file, no search yet; file format 1 frozen with the release that first ships it ([embedded edition](embedded-edition.md), [file format](file-format.md)) |
| C decoder | `crates/morlet-ffi` | For clients of compressed transfer, which is an experimental SQL feature ([compressed transfer](compressed-transfer.md)) |
| Steward | `tools/steward` | Ships with the beta: unattended upkeep on edge machines without a DBA (phase 1 of #287; [design](steward-design.md)) |
| MCP server | `tools/mcp` | Ships with the beta; read-only ([MCP](mcp.md)) |
| Import, export, shipping | `tools/ingest` | Ships with the beta: file importers, the edge-to-central chunk shipper ([chunk shipping](chunk-shipping.md)), S3 import and export |
| Recording viewer | `tools/viewer` | Local, read-only page |
| WinCC OA integration | `integrations/winccoa` | **Experimental**, awaiting native acceptance in WinCC OA (#474) |
| Numerical reference | `tools/reference-go` | Frozen; used only to maintain the numerical contract, not run |

The promises of the beta (API stability, readable data, upgrades) cover the SQL API and the on-disk formats.
The tools follow the documented behaviour on their pages and carry no separate API promise.

## Recording model

A recording is one append-only, uniformly sampled channel: an ID, a sample rate, a channel name, a unit, a sample
quantum and its acquisition metadata, with zero-based, half-open sample intervals. Short files and long streams use
the same recording, chunk and segment tables and the same append contract. Appends must be contiguous; an
identical retry is a no-op and a conflicting one fails. Chunks (1 to 32,768 samples) are storage and transfer
boundaries; reads and search windows can cross them.

Raw samples are authoritative. Grizzbit codes every chunk losslessly, bit for bit, with a CRC-32C, and the append
decodes it again and compares it before storing it (`morlet.verify_appends`, on by default). The derived
representations (chunk summaries, envelopes, probe rows, roll-ups, context values) are computed from the chunks and
can be rebuilt from them. Recording metadata, context segments and labels are not derived: they are stored on their
own and need their own backups.

## Server edition: data flow

1. **Append.** `morlet_append_chunk` and its variants write coded chunks at their global sample offset.
2. **Derive.** Builders write chunk summaries, amplitude envelopes, probe rows (with pgvector), roll-ups and context
   values; `morlet_coverage` reports, when called, which ranges are searchable. An application can call them in the append transaction, so new windows are searchable at
   commit; on edge machines the steward runs them in one budgeted loop. The extension itself starts no background
   worker.
3. **Search.** Exact search scores every eligible stored window (the windows on the envelope grid, not every sample
   offset) with a bounded-lag score. Probe search takes candidates
   from a pgvector HNSW index and re-ranks them with the same exact score: it is approximate in which windows it
   considers, never in the distances it returns ([search contract](search-contract.md)). Budgets are explicit: an
   exact search that would exceed `max_candidates` fails rather than truncating silently, and probe search can
   return fewer than `top_k` when its per-probe budget leaves too few candidates.
4. **Read back.** Every match is a recording and a sample interval, with the recording's metadata, that reads back
   the original samples; its context and labels are separate reads.

Around this core, opt-in per collection: context segments and labels ([context model](context-model.md)),
memory levels that compact old episodes while keeping their summaries, with a ledger ([memory
levels](memory-design.md)), time anchors for a source's own timestamps ([clock drift](clock-drift.md)), the integrity
check `morlet_verify`, notifications, synchronous groups, and row-level security on every companion table
([access control](access-control.md)). The ingest hold is not per collection: it is one switch for the database
that every append checks. Which of these are stable and which experimental is listed in
[API stability](api-stability.md).

Operations stay with PostgreSQL: roles, backups, point-in-time recovery and replicas
([runbooks](runbooks.md), [operations](operations.md)). The steward adds upkeep, disk watching with the ingest hold,
rolling verification, and plans for destructive moves that another person approves.

## Embedded edition

`crates/morlet-sqlite` stores recordings in one SQLite file through `morlet-core`, so it stores the same samples bit
for bit and computes the same interval statistics as the server. Its file format 1 holds chunk format versions 2 and
3 only, while the server may write versions 5 or 6: a transfer re-encodes on the receiving side and verifies the
decoded samples, every bit, so the chunk bytes of the two sides need not match. It has one writer per file, the file's permissions as
access control, and no search yet. The Python SDK opens such a file with `morletdb.open`, and recordings move
between the two editions with the SDK's `import_file` and `export_file`, verified on arrival
([embedded edition](embedded-edition.md#use)).

## Decisions and boundaries

- **The core stays portable.** `morlet-core` and `grizzbit` have no PostgreSQL dependency; both editions use them.
- **Exact search is the reference.** Approximate retrieval reports its recall against it, separately from relevance.
- **No silent changes to samples.** Unsupported input is refused, never rounded, resampled or interpolated.
  Compaction keeps the ledger and coverage reporting, and the chunk summaries through level 3 (level 4 removes
  them too); missing evidence is distinguishable from a negative search result.
- **Upkeep outside the extension.** Derived data is built by explicit builder calls, by the application or the
  steward, not by a worker inside PostgreSQL. This keeps the work visible and budgeted on small machines.
- **Not provided:** automatic diagnosis, resampling, gap interpolation, cross-channel search or synchronized
  multichannel matching. Search distances are not confidence scores.
- **Known limitation:** a recording has one extraction specification (its checkpoint). Separating recording
  identity from representation specifications, for re-extraction or several representations, is not done.

## Proposals, not built

The [advanced retrieval proposal](advanced-retrieval.md), with its [offline wave-index PoC](wave-index-poc.md),
and the [wave-RAG specification](wave-rag-spec.md) are designs for review. They are not part of the beta and make
no promise.
