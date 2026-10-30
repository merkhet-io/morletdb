![MorletDB — Search your waveforms.](assets/brand/hero.svg)

# Find the signal you have seen before

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-39C9DF?labelColor=0B1523)](LICENSE)
[![Rust & Postgres checks](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/rust.yml)
![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-718DFF?labelColor=0B1523&logo=postgresql&logoColor=F1F7FA)
![Rust + pgrx](https://img.shields.io/badge/Rust-pgrx_0.16-70F0CA?labelColor=0B1523&logo=rust&logoColor=F1F7FA)
![Status: developer alpha](https://img.shields.io/badge/status-developer_alpha-F1F7FA?labelColor=0B1523)

**MorletDB is a PostgreSQL extension for waveform archives and similarity search.**
Keep raw sensor recordings in Postgres, select an interesting interval, and ask
**“Where else does something like this occur?”** Every match comes back as a
recording and sample interval you can read, plot and trace to its acquisition
context. Built with Rust, PostgreSQL 17 and Python. A
[Merkhet](https://github.com/merkhet-io) project, licensed under Apache 2.0.

> [!NOTE]
> **Developer alpha.** The recording workflow is implemented and tested, and it
> has been exercised on real public seismic data. Real-world usefulness and
> production-scale performance remain to be established. A similar feature
> vector is a lead for investigation, not a diagnosis or a confidence score.

[Quickstart](#quickstart) · [Viewer](#see-it-the-viewer) · [On real data](#on-real-data-seismic-replay) · [What has been measured](#what-has-been-measured) · [SQL guide](extensions/pg_morlet/README.md) · [Visual notebook](examples/notebooks/README.md) · [Architecture](docs/architecture.md) · [Demo ideas](docs/demo-catalog.md)

## How it works

![From recording to evidence: a recording is appended as raw chunks into PostgreSQL (pg_morlet), Rust Morlet extraction writes window features in the same transaction, a query interval is searched exactly against compatible recordings, and ranked matches are read back as original samples with their context.](assets/brand/pipeline.svg)

- **Preserve the source.** Append continuous chunks with global sample offsets.
  Raw samples stay authoritative, and search windows can cross chunk boundaries.
- **Search local structure.** Compare versioned spectral and statistical features
  (twelve Morlet energy bands plus RMS, crest factor and kurtosis). Only
  recordings with compatible rate, channel, unit, window and extractor are
  compared, with optional metadata filters.
- **Follow a match back to the waveform.** Results are half-open sample intervals
  with their metadata. Source exclusion and overlap suppression run before top-k.
- **Resume ingestion safely.** Raw chunks, window features and extraction
  checkpoints commit in one transaction. Identical retries are verified, not
  duplicated; changed retries, gaps and overlaps fail explicitly.
- **Compare with the machine's own history.** The [context workflow](docs/context-workflow.md)
  compares a segment with earlier segments of the same machine under the same
  operating context (`morlet_compare_summary`).
  - A **pinned baseline** is a metadata tag plus `metadata_filter`; no extra API.
  - **Level track** (`morlet_level_track`, SQL 0.3.1, unreleased, opt-in) adds a
    query-time Kalman level with uncertainty.
  - Each is computed on demand with the caller's permissions; nothing is stored as
    tracker state.
- **Works next to pgvector.** Morlet features can be stored as a generated
  `vector(15)` column and searched with an HNSW index. That is a tested pattern
  ([VR1](evaluation/vector/RESULTS.md)), not a dependency.

A recording is one uniformly sampled channel. MorletDB started with vibration
investigation, and the same model accepts acoustic, electrical and seismic
recordings.

## Quickstart

```sh
docker compose run --rm walkthrough
```

This builds the PostgreSQL 17 preview, imports synthetic Parquet, finds a known
interval and reconstructs its matches. The [walkthrough](docs/recording-workflow.md)
covers prerequisites, the public demo credentials, persistence, restart/replay
checks and teardown. The verified container target is Linux amd64. Tagged images
and exact digests are listed in [releases](docs/releases.md).

Or install the extension with the [Rust/pgrx guide](extensions/pg_morlet/README.md#setup)
and use it directly:

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

Use `morlet_search_interval` to query an interval already stored in a recording,
`morlet_read_interval` to reconstruct any match, and `morlet_stream_status` to
check committed ingestion progress. See the [recording API](docs/recording-workflow.md).

> [!IMPORTANT]
> Rebuilding the image does not update an existing database or volume.
> `ALTER EXTENSION pg_morlet UPDATE` is tested in CI from SQL 0.1.0 (as released in
> 0.1.0-alpha.3) and SQL 0.2.0 (0.2.0-alpha.1) to the current version; 0.1.0-alpha.1
> needs a fresh install. See
> [existing installations](extensions/pg_morlet/README.md#existing-installations)
> and [upgrading](docs/durable-ingestion.md#upgrading).

## See it: the viewer

![MorletDB viewer on the synthetic demo: part 6's waveform with block, part, program and tool context lanes, segments and an air-cut label; the selected N30 segment is compared with the same block on other parts and deviates by 77.6% in vibration level (std).](assets/screenshots/viewer-demo.jpg)

A local, read-only web page for recordings, their context and comparisons:

```sh
python -m pip install -r tools/viewer/requirements.txt
DATABASE_URL=postgresql://... python tools/viewer/demo.py     # optional: synthetic demo collection
DATABASE_URL=postgresql://... python tools/viewer/viewer.py   # http://127.0.0.1:8765
```

- **Waveform** with zoom, plus lanes for context (program, block, tool, part),
  segments, events and labels.
- **Click a segment** to compare it with the same context on other parts:
  - the evaluated score and which value moved;
  - its rank against history;
  - per-value deviations.
- **Select a window** to find similar windows across recordings, using the
  materialized envelopes.
- **Channels together:** sibling channels of an acquisition (for example
  vibration and spindle current) are drawn on the same time axis and compared side
  by side.
- **Labels from the page:** with `--allow-labels`, label air cuts and other events
  directly. These are the only writes, and they are token-guarded.

The server binds to 127.0.0.1, every session is read-only, and the page loads
nothing from the network. Details: [tools/viewer](tools/viewer/README.md). The
screenshot uses the synthetic demo, so it is an illustration, not evidence.

## On real data: seismic replay

![Seismic example: a held-out M5.0 earthquake at station PB01, with its waveform and spectrogram](examples/seismic/docs/02-query-pb01-m5.jpg)

The [seismic example](examples/seismic/README.md) replays about 900 station-hours
of public broadband recordings from northern Chile (GFZ GEOFON, IPOC network)
through MorletDB. It splits recordings at real data gaps, verifies duplicate and
replayed data, proposes candidate events with an ObsPy STA/LTA baseline, and
retrieves similar historical intervals from the same station in a local Streamlit UI.
USGS catalog events are shown as a separate reference overlay.

What it showed, reported with its limits in the [investigation summary](docs/seismic-investigation.md):

- Durable ingestion held up on real discontinuities: 238 data gaps, retransmitted
  records, and restart or stop-and-resume runs that reproduced results exactly.
- The STA/LTA baseline found all 77 monitored regional catalog arrivals.
  Catalog labels are an incomplete proxy, since most small local events are
  uncataloged.
- Pooled Morlet features retrieved cataloged-event windows far above the base
  rate, and **outperformed or tied** the experimental temporal representation.

It is an investigation tool, not an earthquake classifier or warning service.
Seismic rates use the explicit `morlet-energy-lowrate-v1` representation.

## On real data: sleep staging by example

![MorletDB sleep example: a held-out night's hypnogram, expert scoring above and staging by the 40 nearest examples below; the selected epoch's EEG with its MorletDB interval; how the 40 examples voted (24 N2, 16 N3, against the expert's N3); and the nearest expert-scored epochs from other people with their waveforms.](assets/screenshots/sleep-demo.jpg)

The [sleep example](examples/sleep/README.md) stores 153 Sleep-EDF nights (EEG
Fpz-Cz) as raw MorletDB recordings and indexes every 30-second epoch in pgvector.
In a local page you click an epoch of a held-out night and see the 40 most similar
expert-scored epochs from other people: their stages, waveforms and the interval
each is read back from. It reproduces the [S1](evaluation/sleep/RESULTS.md) staging:
kappa 0.583, with 3 of 98,159 epochs staged differently. A research demo,
not a medical device.

## Import waveform archives

The [Parquet importer](tools/ingest/README.md) accepts sample-array rows or individual
sample rows, explicit column mappings and bounded batches, and can validate
without a database.

```sh
python -m pip install -r tools/ingest/requirements.txt
# Configure DATABASE_URL or standard PostgreSQL connection variables.
python tools/ingest/parquet_import.py signals.parquet \
  --collection public.signals --rpc-chunks 16 --commit-chunks 32
```

Rerunning unchanged input verifies the committed prefix and avoids duplicates.
Sources are local immutable files; S3 discovery and direct row-group resume are
not implemented.

## What has been measured

Every result names its data, baselines and limits. Each frozen evaluation fixes
its protocol before the data is read, runs once, and reports the result, including
negative ones.

- **Morlet features have not beaten simple baselines** in frozen, held-out tests:
  FFT band powers did as well on bearings, ECG and sleep EEG.
- **Comparing a machine with its own history has not warned earlier than a plain
  RMS alarm** on run-to-failure bearings.
- **What has held up** is the storage and retrieval machinery around raw recordings:
  - exact search;
  - comparison under the same operating context;
  - retrieval of similar labelled examples as an explanation;
  - access control.

| Evaluation | Result |
|---|---|
| [Paderborn bearings](evaluation/decision/results/e6bf274/RESULTS.md), frozen plan, held-out bearings | Morlet vs FFT bands + statistics: Δ P@10 −0.032 [−0.098, +0.036]. **Not supported.** |
| [Seismic timing test](evaluation/decision/results/timing-012bb3d/RESULTS.md), frozen plan, 2024 held out | Time-resolved features help (+21 km). Temporal Morlet beats a time-binned spectrogram by only +1.6 km [+0.9, +2.3], below the 5 km margin. **Envelope cross-correlation beats temporal Morlet by 6.0 km.** |
| [PTB-XL ECG pilots](research/multiscale_memory/) | Retrieved context does not improve a classifier, with fixed or learned retrievers. Closed. |
| [Raw storage](evaluation/compression/README.md) | Lossless encoded chunks are 1.7–2.9× smaller than the previous `float8[]` chunks (4096-sample chunks), comparable to optimized Parquet (codec alone: 2% larger to 13% smaller). |
| [Context-conditioned comparison (C1)](evaluation/context/results/RESULTS.md), frozen plan, NASA milling, held-out insert sets | Deviation from same-setting history tracks flank wear far better than deviation from all history: ρ 0.91 vs 0.60, Δ +0.31 [+0.08, +0.54]. Time in use alone reaches 0.79. Available as the [context workflow](docs/context-workflow.md). |
| [Own-history early warning (C5)](evaluation/context/c5/RESULTS.md), frozen plan, Paderborn run-to-failure, 14 bearings | Same-context history vs a plain RMS alarm: Δ stable lead −0.047 [−0.195, +0.050], **not supported**. Same-context vs all history: +0.074 [+0.001, +0.181], **supported** (narrowly). |
| [Cross-condition resemblance (C6)](evaluation/context/c6/RESULTS.md), frozen plan, same bearings | Late-life recordings resemble other operating conditions more often: +0.120 [+0.039, +0.193], **supported**, but only at the minimum of 5 bearings that passed the gate, and inconclusive on the second sensor. |
| [Pinned healthy baseline (C7)](evaluation/context/c7/RESULTS.md), frozen plan, XJTU-SY, 15 bearings | Q1, pinned vs all history: +0.049 [−0.059, +0.196], inconclusive. Q2, vs RMS: +0.100 [+0.005, +0.242], **supported by the rule, but driven by alarms on vibration settling after run-in**. Counting only rising alarms (post hoc): +0.001. Fixes history absorbing a lasting rise on Paderborn B09 (exploratory). |
| [Level track (L1)](evaluation/level/RESULTS.md), frozen plan, synthetic | On evenly spaced data it equals an exponential moving average. It adds uncertainty and 39% lower error after 48 h gaps. Intervals are overconfident with correlated noise (coverage 0.85). **Unclear**; kept as opt-in. |
| [pgvector integration (VR1)](evaluation/vector/RESULTS.md), frozen plan, PTB-XL, 17,418 ECGs | Morlet features in pgvector rank like pg_morlet's own scorer (overlap 1.000). HNSW recall@10 is 0.9995–1.0 at about 9× the speed of a scan, and row-level security holds. A stand-in text vector missed the recall bar (0.90–0.92). No relevance claim. |
| [Sleep staging by retrieval (S1)](evaluation/sleep/RESULTS.md), frozen plan, Sleep-EDF, 39 held-out subjects | Morlet vs FFT bands with the same layout: Δ kappa −0.006 [−0.015, +0.002], **not supported**. Staging by the 40 most similar expert-scored epochs vs logistic regression: +0.042 [+0.016, +0.068], **supported**: explanation by example costs no accuracy. Kappa is about 0.58 from one EEG channel. |
| [Materialized envelopes (R1)](evaluation/materialized/results/eval-2025/RESULTS.md), frozen plan, 2025 held out | Stored envelopes return the same top-10 as decoding raw at 0.10× the p95 latency, but cost 27.9% of raw storage (bound 25%): **narrow**. The follow-up R1b (half precision, 2022 held out) gave the same results at 12.9% of raw storage: **continue**. Available as [materialized envelopes](docs/durable-ingestion.md#materialized-envelopes). |
| [CWRU bearings](evaluation/README.md), run-separated, early slice | Pooled Morlet **ties** the statistical baseline. A reproducible baseline, not a win. |
| [Seismic replay](evaluation/reports/seismic-cx-north-chile-2024-v2.md), 3 stations, early slice | Real ingestion of about 324 M samples with gaps, duplicates and resume. No simple-feature baseline was run, so it says nothing about Morlet vs simple features. |
| [Wave-index PoC](docs/wave-index-poc.md), synthetic | Temporal cells separate a burst from its time reversal, which pooled features cannot (distance 1.18 vs ≈0). Posting lists traverse most of the index. |
| [Benchmarks](benchmarks/README.md) | Ingestion, extraction and retrieval are measured separately. A 10,000-candidate CI smoke checks ranking against an independent oracle; it is not a capacity claim. |

Reports record parameters and source hashes so they can be rerun; the seismic
report also records the code revision and database it ran on. The wave-index
experiment is in memory, not a PostgreSQL index, and needs a source checkout
rather than a release image; its next steps are in the
[advanced retrieval design](docs/advanced-retrieval.md).

## Where it comes from and where it is going

MorletDB grew out of **condition monitoring with high-volume, high-frequency CNC
spindle data**: vibration across three axes, correlated with temperature and
current. In that work, a vibration episode was most useful when an engineer could
see what the spindle was doing, compare the axes and relate the event to its
operating context. The motivating question was **“Have we seen this pattern
before, and under what conditions?”** MorletDB now groups channels measured
together, such as vibration axes and spindle current, into acquisitions on one
time axis. It compares a cut with the same cut on earlier parts, channel by
channel. Evidence on real shop-floor data with NC-block context is still missing.

Next evaluations, all **candidate workloads** rather than demonstrated results:

- **CNC spindle episodes.** A [pilot experiment spec](docs/cnc-spindle-experiment.md)
  with whole-tool holdouts, time-aligned context and reviewed relevance labels.
- **Defense engineering.** Test-and-evaluation and maintenance data: platform and
  subsystem testing, vehicle and support-equipment maintenance, and power-system
  testing. The first proposed slice is a motor or pump test-bench investigation
  with approved data, operating context, difficult negatives and an engineer's
  relevance judgments. None of this establishes defense-specific accuracy or
  deployment readiness.
- **Demo catalog.** Fifteen bounded [investigation ideas](docs/demo-catalog.md),
  covering industrial diagnostics, quality, electrical traces, HDDs, seismic and
  audio, each with data sources, baselines and acceptance criteria.

## Current boundaries

| Area | Contract today |
|---|---|
| Storage | Append-only recordings, raw chunks, typed segments and restartable checkpoints |
| Input | 1..32768 finite samples/chunk; 128..200000 Hz (`morlet-energy-v1`), opt-in [1,128) Hz (`morlet-energy-lowrate-v1`), or any rate up to 200000 Hz raw-only (`none`: no segments, not feature-searchable); explicit channel and units |
| Search windows | 512..4096 samples; compatible rate/channel/unit/window/normalization/extractor |
| PostgreSQL retrieval | Exact scan; default 4096 eligible segments; explicit experiments up to 1,000,000, with no capacity guarantee |
| Consistency and access | Synchronous extraction; transactional publication; caller permissions and RLS |
| Wave-index PoC | Offline temporal scoring and approximate postings; no persistence or SQL integration |
| Lifecycle | Alpha. `ALTER EXTENSION pg_morlet UPDATE` to the current version is tested in CI from 0.1.0-alpha.3 (SQL 0.1.0) and 0.2.0-alpha.1 (SQL 0.2.0); no stable API/schema guarantee |

No resampling, speed invariance, gap interpolation, background extraction worker,
clock-drift model or cross-channel search is implemented. Multichannel acquisitions
align channels by declared offsets; they do not estimate them. Use the supported append
APIs; mutating managed tables directly can violate cross-table invariants.
See [support boundaries](SUPPORT.md) and the [numerical contract](docs/morlet-extractor.md).

## Repository

| Path | Role |
|---|---|
| [crates/morlet-core](crates/morlet-core) | Portable Rust extraction, localized cells, chunk boundaries and checkpoints |
| [extensions/pg_morlet](extensions/pg_morlet) | PostgreSQL storage, ingestion, interval reads and exact search |
| [tools/ingest](tools/ingest) · [tools/client](tools/client) | Parquet import and thin Python recording client |
| [tools/viewer](tools/viewer) | Local, read-only web viewer: waveform, context lanes, compare-to-history, similar windows |
| [examples/seismic](examples/seismic) | Reference application: seismic replay investigation on public data |
| [examples/notebooks](examples/notebooks) | Visual waveform and match inspection |
| [evaluation](evaluation) · [benchmarks](benchmarks) | Relevance, candidate recall and workload experiments |
| [tools/reference-go](tools/reference-go) | Frozen independent numerical reference; no product runtime |

```sh
make test       # Rust core and formatting; no Go toolchain required
make pg-test    # configured PostgreSQL/pgrx required
```

The Rust core is independent of PostgreSQL. Go is kept only for optional
reference checks (`make test-reference`) and deliberate fixture maintenance
(`make golden`).

[Contributing](CONTRIBUTING.md) · [Development](docs/development.md) · [Support](SUPPORT.md) · [Security](SECURITY.md) · [Roadmap](docs/product-roadmap.md) · [Brand assets](assets/brand/README.md)

## License

Copyright 2026 MorletDB contributors. Code, documentation and original brand
artwork are licensed under the [Apache License, Version 2.0](LICENSE).

<p align="center"><img src="assets/brand/mark.svg" alt="MorletDB mark" width="56"></p>
