# Seismic replay investigation: results and decisions

Status: **repository example and evaluation workload** (local research
application). Historical replay works on real public data. Live SeedLink input
is implemented but **unvalidated**: it has never been run against a server.
Application and launch instructions: [`examples/seismic`](../examples/seismic/README.md).
Current numbers: [`evaluation/reports/seismic-cx-north-chile-2024-v2.md`](../evaluation/reports/seismic-cx-north-chile-2024-v2.md)
(JSON beside it; run reports in `seismic-cx-north-chile-2024-v2-runs/`). The v1
report is kept as historical.

This is an investigation tool. Waveform similarity is not an earthquake
classifier, explosion or nuclear-test detector, or warning service, and no
result here validates one.

## What was built

1. **Dataset.** GEOFON FDSN waveforms and StationXML for IPOC CX.PB01, PB02 and
   PB08 `HHZ` (100 Hz STS-2) above the persistent ~100 km deep source region near
   Pica, northern Chile, plus USGS ComCat reference events. Two whole periods:
   a reference week (2024-03-04 to 03-11) and a held-out 5.5 days
   (2024-05-01 to 05-06 12:00). 39 day files, 311 MB, checksummed in a committed
   manifest; replay, detection, retrieval, evaluation and the UI then run offline.
2. **Representation.** `morlet-energy-lowrate-v1`: the v1 transform over the
   disjoint domain [1,128) Hz, opt-in at recording creation, recorded in the
   checkpoint and enforced at every boundary (Rust core, SQL, importer, thin
   client, wave-index spec, example). v1 defaults and frozen fixtures are
   unchanged. Window 2048 samples (20.48 s), stride 512, per-window z-score,
   bands 1.56-40 Hz.
3. **Ingestion.** One `Chunk` interface for replay and SeedLink; one ingestor
   writing MorletDB recordings via the existing durable append API. Gaps and
   conflicting overlaps split recordings; conflicting versions get
   content-addressed identities; identical overlaps are verified duplicates;
   samples, extraction checkpoints, progress and discontinuity events commit together.
4. **Detection.** ObsPy recursive STA/LTA baseline on a 2-10 Hz copy of
   committed samples, in fixed blocks so results are independent of chunking,
   restarts and operator stop/resume.
5. **Retrieval.** MorletDB exact pooled search over all reference windows;
   pooled, exhaustive-temporal and approximate-temporal modes of the wave-index
   PoC over a bounded in-memory gallery reconstructed from MorletDB samples.
6. **UI.** Streamlit on 127.0.0.1: replay controls and input-mode indicator,
   UTC timelines of recordings/triggers/catalog arrivals, discontinuity log,
   waveform and spectrogram, ranked matches with waveform/spectrum comparison
   and provenance, separate catalog annotations.

![Overview: PB08 recordings split at real gaps](../examples/seismic/docs/01-overview-pb08-gaps.jpg)
![Query: held-out M5.0 at PB01](../examples/seismic/docs/02-query-pb01-m5.jpg)
![Pooled MorletDB matches](../examples/seismic/docs/03-pooled-pg-matches.jpg)
![Approximate temporal plan with a 4-candidate budget](../examples/seismic/docs/04-temporal-approx-budget.jpg)

## Measured results (v2)

Provenance: code `0b41233` (clean tree), fresh database `seismic_eval_0b41233`
with `pg_morlet` installed from this branch's image, representation
`morlet-energy-lowrate-v1`, detector `stalta-baseline-v1`, manifest sha256
`c03f4126…`. Apple M5 Max, macOS 27, PostgreSQL 17.6 container (arm64). Three
parallel per-stream processes.

**Ingestion.** 900 station-hours (324 M samples), 0.92 GB of chunk storage and
631,865 feature windows. PB08 became 240 recordings: 238 in-period gaps
(0.01-4.02 s) plus the reference/held-out boundary. Its backward timing jumps
carried identical retransmitted samples, so they were verified as duplicates
(9,258 samples) and no conflicts occurred; conflict versioning is therefore
exercised by synthetic tests only. 20,374 header offsets of at most 0.0002
samples (2 µs, from microsecond-rounded record times) were absorbed and logged.
Each unpaced replay took 906-913 s (about 1,180-1,190x real time per stream). Append
batches of 16 x 30 s chunks had p50 0.33 s and p90 0.39 s including
synchronous Rust extraction. A per-window `morlet_features` round trip from
Python, including transfer of 2048 floats, measured p50 4.7 ms and p90 6.1 ms.
v1 measured 31 ms on the older demo database; the cause of the difference was
not isolated. No queue exists in replay; live backlog, reconnects and
freshness are reported by the SeedLink source but were not measured.

**Restart and lifecycle on real data (same commit).**
- Replaying PB02's held-out period again appended 0 samples and verified
  47,522,796 duplicates, all persisted to the progress row. The progress-only
  defect fixed below would have dropped them.
- PB08's held-out period stopped after 300 chunks (not finalized), then resumed,
  produced 951 triggers byte-identical to the uninterrupted run.
- v1 additionally showed an interrupted PB01 run plus full replay re-appending
  nothing (907,165 verified duplicates).

**Detection (ObsPy STA/LTA baseline only).** 14,197 triggers in 892 monitored
station-hours. Episodes (triggers less than 25 s apart grouped) not associated
with any cataloged event: 13.9-15.7/h at PB01 and PB02, 5.6-5.7/h at PB08.
Many are visibly local earthquakes with P/S pairs 11-17 s apart; ComCat is
incomplete below about M3.5 here, so unmatched is not false. All 77 monitored
regional catalog arrivals were detected; onset minus IASP91 P had median
0.48 s and maximum 2.0 s. All 9 teleseismic M6+ arrivals were missed: the 2-10 Hz
band is the wrong band for them. Four catalog arrivals fell outside recorded or
monitored time and are reported, not counted.

**Retrieval.** Queries are held-out detector episodes (all 37 catalog-associated
across stations, plus 60 evenly spaced unassociated per station). Galleries are
reference-only, checked by assertion. Catalog associations are an
**incomplete proxy label**: a match counts as catalog-relevant if it overlaps a
cataloged event's predicted arrival window at the same station. Precision@5:

| Station | Base rate in gallery | MorletDB pooled (all 118k windows) | Pooled, gallery | Temporal, gallery | Temporal approx. (64) |
|---|---:|---:|---:|---:|---:|
| PB01 | 29/4096 | 0.37 | 0.47 | 0.24 | 0.21 |
| PB02 | 30/4096 | 0.24 | 0.29 | 0.11 | 0.04 |
| PB08 | 30/3241 | 0.39 | 0.21 | 0.21 | 0.19 |

Pooled features retrieve cataloged-event windows far above the base rate.
**On these galleries, pooled features outperform or tie the current temporal
representation**: temporal is worse at PB01 and PB02 and ties at PB08. For
unassociated queries, both modes find essentially no cataloged windows
(expected; no label exists for them), and pooled and temporal tie on 59 of 60
queries per station. The same-source criterion (matched event within 50 km and
30 km depth of the query's event) was met for only 0-29% of associated queries.
Per-window z-scoring removes amplitude by design, so an M5.0 query can match a
much weaker uncataloged event.

**Approximate postings.** Each query visited 208k-690k of 603k-762k posting
entries (common quiet cells), and candidate recall against the exhaustive
temporal top-5 was 0.20-0.64 at 64 candidates and 0.54-0.81 at 1024. On this
data the posting plan visits many entries and has incomplete recall.
Exhaustive scoring of a 4096-window gallery took about 0.1 s in Python.

**v1 versus v2.** v2 reproduced every detection and retrieval metric, every
gallery generation ID and every per-query ranking in all four modes (217/217
queries). Only timings, duplicate counts and the extraction measurement differ.

## Defects found and fixed

Found while building:
- `morlet_read_interval` scanned and detoasted every earlier chunk of a recording
  (0.3-0.5 s per read deep into a 7-day recording). An implied lower bound now
  limits the index range (about 10 ms); a pgrx test covers the maximal-chunk boundary.
- Ending one stream's replay finalized other streams' still-growing recordings.

Found in review of PR #26, each reproduced by a failing PostgreSQL test first:
- **Version identity.** A conflicting packet starting at an existing recording's
  first sample reused its ID, and the append failed (`conflicting replay at chunk
  offset`). Conflict-opened recordings are now content-addressed versions;
  retransmissions match existing versions by content and never create copies.
- **Progress-only flushes.** An all-duplicate replay never persisted its
  counters. Counters are now committed with the transaction and cleared after
  commit, so they are never applied twice.
- **Stop/resume.** An operator Stop finalized the open recording, and resumed
  data was then silently skipped by the detector. Only natural exhaustion
  finalizes now; a finalized recording that grows has its truncated trailing
  blocks re-analyzed.

- **Crash window in version identity** (final-review check). A version committed
  before its samples could survive a crash empty, be filled by a different
  conflicting packet, and later block the original version with an identity
  error. Identities now commit with their opening samples. Tests cover re-chunked
  retransmission of both versions, distinct versions with shared prefixes, a
  forced suffix collision and the crash window.

Found while rerunning the evaluation:
- Parallel per-stream processes raced to create the collection on a fresh
  database. Setup is now serialized with an advisory lock and covered by a
  concurrent test.

## Installation and reproducibility

The SQL changes keep `pg_morlet` 0.1.0 and ship no upgrade script.
**Rebuilding the image or library does not update an existing database or
Docker volume**, and `ALTER EXTENSION ... UPDATE` does nothing. Use a fresh
database; see [existing installations](../extensions/pg_morlet/README.md#existing-installations).
v2 ran on a newly created database. v1 ran on a database patched by hand,
which is not a supported procedure.

CI (`seismic-example` job) installs this branch's extension into fresh pgrx
PostgreSQL, builds the Rust features adapter and runs the offline, database and
regression tests on synthetic data. Missing prerequisites or skipped tests fail
the job.

## Unsupported capabilities

- Event classification of any kind (earthquake/explosion/nuclear), magnitudes,
  locations, phase picking or alerts.
- Cross-station or cross-channel retrieval, synchronized multi-station
  association, three-component (HHN/HHE) analysis. Orientation is recorded but unused.
- Instrument-response removal; physical units. Comparisons are within one
  channel's raw counts.
- Frequency content below about 1.5 Hz at 100 Hz (teleseismic, long period).
- Persistent or incremental temporal index. The gallery is rebuilt in memory
  (about 21 s for 4096 windows) and capped at 4096 windows.
- Extension upgrade path (fresh installation only).
- Live mode. The SeedLink code paths (reconnect, freshness, backlog) have
  never run against a server.
- Analyst-reviewed relevance labels. None exist; catalog-derived labels are
  incomplete and carry arrival-time uncertainty.
- Ambiguous continuation between conflicting versions is decided by a
  deterministic rule and logged, not resolved from content.
- Display-only filtering of short match windows shows a filter startup transient.

## Historical explosion track

Not started, deliberately. A defensible version needs a cited authoritative
event list (for example an official announcement or a peer-reviewed catalog)
and stations with public waveforms that recorded both the reference explosion
and comparable earthquakes. It would be shown only as a "historical reference
event" overlay. Separating source types needs specialist validation and
additional evidence such as depth, first-motion polarity and spectral ratios,
not similarity scores.

## Next decisions

The seismic example stays as a reference application and evaluation workload.
The next product decision should follow demonstrated investigation value,
measured against simple baselines with reviewed relevance labels.

1. **Labels first.** Commission a small analyst-reviewed relevance set,
   including uncataloged events and negatives, before any tuning. Never tune on
   the held-out period.
2. **Temporal representation.** It did not beat pooled features here. Decide
   whether to try a different temporal design (for example lag-tolerant
   alignment or coarser bins) or drop it from the near-term roadmap. Do not
   build persistent wave-token indexes on the current cell layout.
3. **Amplitude.** Decide whether per-window z-score suits investigation work,
   or whether a retained amplitude feature is needed (a new representation version).
4. **Read path.** Ship the `morlet_read_interval` bound with a supported
   migration; every long recording depends on it.
5. **Live mode.** Run a supervised SeedLink session before calling live input supported.
6. **Next workload.** The [CNC spindle experiment spec](cnc-spindle-experiment.md):
   whole-tool holdouts, time-aligned current and temperature context, and
   20 reviewed pilot queries reported per query with uncertainty.

## Reproduce

```sh
docker compose -f examples/seismic/compose.yaml up -d --wait db
docker compose -f examples/seismic/compose.yaml exec db psql -U morlet -c "CREATE DATABASE seismic_eval"
cd examples/seismic
DSN=postgresql://morlet:morlet-demo-only@localhost:54339/seismic_eval
../../.venv-seismic/bin/python -m morlet_seismic download    # once, network
../../.venv-seismic/bin/python -m morlet_seismic verify
for s in PB01 PB02 PB08; do
  ../../.venv-seismic/bin/python -m morlet_seismic --dsn $DSN replay --streams CX.$s..HHZ \
    --output ../../data/seismic/runs/replay-$s.json &
done; wait
../../.venv-seismic/bin/python -m morlet_seismic --dsn $DSN evaluate \
  --replay-reports ../../data/seismic/runs --output ../../evaluation/reports/seismic-eval.json
```

Timings depend on hardware and concurrent load. Trigger counts, segments,
catalog associations and retrieval rankings are deterministic for a given
manifest and code version; v1 and v2 agree exactly.
