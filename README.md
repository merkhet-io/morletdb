![MorletDB — Search your waveforms.](assets/brand/hero.svg)

# Find the signal you have seen before

[![Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-39C9DF.svg)](LICENSE)
[![Rust & Postgres checks](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml)

**MorletDB is a PostgreSQL extension for waveform archives and similarity search.**
Keep raw sensor recordings, select an interesting interval, and find related
intervals with their original samples and acquisition context. Built with Rust,
PostgreSQL 17 and Python. A [Merkhet](https://github.com/merkhet-io) project,
licensed under Apache 2.0.

Start with a vibration burst, an acoustic transient or a repeatable test signal.
Ask **“Where else does something like this occur?”** Then inspect the matches:
their waveforms, spectra, recording coordinates and metadata.

> **Developer alpha.** The recording workflow is implemented and tested. A
> separate offline wave-index PoC explores temporal retrieval. Real-world
> usefulness and production-scale performance remain to be established.

[Quickstart](#try-the-recording-workflow) · [Visual notebook](examples/notebooks/README.md) · [SQL guide](extensions/pg_morlet/README.md) · [Wave-index PoC](docs/wave-index-poc.md) · [Demo ideas](docs/demo-catalog.md) · [Architecture](docs/architecture.md)

## From recordings to evidence you can inspect

- **Preserve the source.** Append continuous chunks with global sample offsets.
  Search windows can cross chunk boundaries; raw samples remain available.
- **Search local structure.** Compare versioned spectral/statistical features,
  with compatible acquisition settings and metadata filters.
- **Follow a match back to the waveform.** Results identify a recording and
  half-open sample interval. Read the interval and inspect its acquisition context.
- **Resume ingestion.** Raw chunks, complete segment features and extraction
  checkpoints publish in one transaction. Identical retries are safe; changed
  retries, gaps and overlaps fail explicitly.

A recording is one uniformly sampled channel with a sample rate, units and
metadata. The first application is vibration investigation; the data model also
accepts compatible acoustic or electrical recordings. A similar feature vector
is a lead for investigation, not a diagnosis or confidence score.

## Try the recording workflow

```sh
docker compose run --rm walkthrough
```

This builds the current PostgreSQL 17 preview, imports synthetic Parquet, finds a
known interval and reconstructs its matches. The
[walkthrough](docs/recording-workflow.md) covers prerequisites, public demo
credentials, persistence, restart/replay checks and teardown. The verified
container target is Linux amd64.

The [visual notebook](examples/notebooks/README.md) plots query and match
waveforms beside their FFT magnitudes, distances and provenance. It includes a
polarity distractor: a signal can look different in time while retaining the
same spectral-energy features.

For tagged images and exact digests, see [releases](docs/releases.md). Releases
are snapshots; the offline wave-index experiment requires a current source checkout.

## Try it in Postgres

Install the extension using the [Rust/pgrx guide](extensions/pg_morlet/README.md#setup),
then run:

```sql
CREATE EXTENSION pg_morlet;
SELECT morlet_create_stream_collection('public', 'signals');
SELECT morlet_start_recording(
  'signals', 'motor-001', 4096, 'vibration', 'g', '{"asset":"motor-1"}'
);
SELECT morlet_append_chunk(
  'signals', 'motor-001', 0,
  ARRAY(SELECT sin(i::float8 / 8) FROM generate_series(0,2047) i)
);
SELECT * FROM morlet_search_streams(
  'signals',
  ARRAY(SELECT sin(i::float8 / 8) FROM generate_series(0,511) i),
  4096, 'vibration', 'g', '{"asset":"motor-1"}'
);
```

Use `morlet_search_interval` to query an interval already in a recording, with
source exclusion and overlap suppression. Use `morlet_read_interval` to reconstruct
a match across chunks. `morlet_stream_status` reports committed ingestion progress.
See the [complete recording API](docs/recording-workflow.md).

## Import waveform archives

The [Parquet importer](tools/ingest/README.md) accepts sample-array rows or individual
sample rows, explicit column mappings, bounded batches and validation without a
database.

```sh
python -m pip install -r tools/ingest/requirements.txt
# Configure DATABASE_URL or standard PostgreSQL connection variables.
python tools/ingest/parquet_import.py signals.parquet \
  --collection public.signals --rpc-chunks 16 --commit-chunks 32
```

Rerunning unchanged input verifies the committed prefix and avoids duplicates.
Sources are currently local immutable files; S3 discovery and direct row-group
resume are not implemented.

## Beyond pooled features: a wave-index experiment

The PostgreSQL search path compares twelve pooled Morlet energy bands plus AC
RMS, crest factor and kurtosis. These fifteen log-transformed features capture
spectral/statistical similarity but lose event order within a window.

The [offline PoC](docs/wave-index-poc.md) retains integer-coordinate localized
energy cells from the Rust extractor. A Python harness provides:

1. An exhaustive temporal scorer as the reference.
2. Quantized positional posting lists to select candidates.
3. Full temporal reranking, with explicit work budgets and measured recall.

In one deterministic synthetic experiment with **127 gallery windows**, reranking
**4 candidates** recovered the exhaustive temporal top-3. A burst and its reversal
had effectively identical pooled features but a temporal distance of **1.18**.

That same query visited **17,835 postings**: common quiet cells make candidate
lookup expensive. This demonstrates controlled temporal discrimination and a
candidate/recall tradeoff, not an archive-scale speedup or validated field relevance.

```sh
python -m pip install -r evaluation/requirements.txt
cargo build --locked --release -p morlet-core --example features
python evaluation/wave_index.py --size 128 --output wave-index-output/report.json
```

The [committed report](evaluation/reports/wave-index-synthetic-v1.json) records
parameters, source hashes, work counts and diagnostic timings. This is an
in-memory experiment, not a PostgreSQL index. Durable representation generations,
database integration and general ordered-motif queries remain in the
[advanced retrieval design](docs/advanced-retrieval.md).

## Next evaluation direction: defense engineering

We want to evaluate the recording/search workflow on defense-related engineering
data, starting with test-and-evaluation and equipment maintenance. These are
**candidate workloads**, not demonstrated deployments or customer results.

| Candidate workload | Investigation to validate |
|---|---|
| Platform and subsystem testing | Find repeated vibration or acoustic episodes across comparable test recordings |
| Vehicle and support-equipment maintenance | Retrieve previously reviewed motor, bearing or pump episodes for analyst comparison |
| Power-system testing | Compare voltage or current transients across compatible battery or power-supply test runs |

The first proposed slice is **a motor or pump test-bench investigation**:
select a reviewed vibration episode, retrieve related intervals from held-out
runs, and inspect the original samples. Success needs approved data, operating
context, difficult negatives and an engineer's relevance judgments.

This direction does not establish defense-specific accuracy or deployment
readiness. The existing single-channel model does not provide synchronized
cross-channel search, live acquisition control or automatic fault diagnosis.

## Demo ideas and evaluation backlog

The [demo catalog](docs/demo-catalog.md) collects fifteen proposed investigations:
industrial diagnostics, end-of-line quality, actuator/electrical traces, HDDs,
seismic monitoring and voice/audio retrieval. It records priorities, data sources,
baselines, acceptance criteria and missing capabilities. Start with one bounded
evaluation; these ideas are not claims of implemented diagnostic features.

## What has been measured

The [real-recording evaluator](evaluation/README.md) compares statistics, FFT,
pooled Morlet and an experimental temporal representation on a small run-separated
CWRU bearing dataset. **Morlet ties the statistical baseline in that slice.**
It establishes a reproducible baseline; differentiation remains unproven.

The [benchmark suite](benchmarks/README.md) separates ingestion, extraction and
retrieval, including storage-layout comparisons and concurrent-write diagnostics.
A 10,000-candidate feature-only CI smoke checks ranking against an independent
oracle. It is not evidence of raw-waveform ingestion capacity. Larger experiments
and continuous-recording sweeps are available; report their workload and limits.

## Current boundaries

| Area | Contract today |
|---|---|
| Storage | Append-only recordings, raw chunks, typed segments and restartable checkpoints |
| Input | 1..32768 finite samples/chunk; 128..200000 Hz; explicit channel and units |
| Search windows | 512..4096 samples; compatible rate/channel/unit/window/normalization/extractor |
| PostgreSQL retrieval | Exact scan; default 4096 eligible segments; explicit experiments up to 1,000,000, with no capacity guarantee |
| Consistency and access | Synchronous extraction; transactional publication; caller permissions and RLS |
| Wave-index PoC | Offline temporal scoring and approximate postings; no persistence or SQL integration |
| Lifecycle | Fresh alpha installations; no extension upgrade migration or stable API/schema guarantee |

No resampling, speed invariance, gap interpolation, background extraction worker
or synchronized multichannel retrieval is implemented. Use supported append APIs;
direct mutation of managed tables can violate cross-table invariants.
See [support boundaries](SUPPORT.md) and the [numerical contract](docs/morlet-extractor.md).

## Develop and contribute

| Path | Role |
|---|---|
| [crates/morlet-core](crates/morlet-core) | Portable Rust extraction, localized cells, chunk boundaries and checkpoints |
| [extensions/pg_morlet](extensions/pg_morlet) | PostgreSQL storage, ingestion, interval reads and exact search |
| [tools/ingest](tools/ingest) · [tools/client](tools/client) | Parquet import and thin Python recording client |
| [evaluation](evaluation) · [benchmarks](benchmarks) | Relevance, candidate recall and workload experiments |
| [examples/notebooks](examples/notebooks) | Visual waveform investigation |
| [tools/reference-go](tools/reference-go) | Frozen independent numerical reference; no product runtime |

```sh
make test       # Rust core and formatting; no Go toolchain required
make pg-test    # configured PostgreSQL/pgrx required
```

The Rust core is independent of PostgreSQL. Go is retained only for optional
reference checks (`make test-reference`) and deliberate fixture maintenance
(`make golden`). The legacy Go application has been removed.

[Contributing](CONTRIBUTING.md) · [Development](docs/development.md) · [Security](SECURITY.md) · [Roadmap](docs/product-roadmap.md) · [Brand assets](assets/brand/README.md)

## License

Copyright 2026 MorletDB contributors. Code, documentation and original brand
artwork are licensed under the [Apache License, Version 2.0](LICENSE).
