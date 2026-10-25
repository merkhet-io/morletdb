# Seismic replay investigation (local research application)

Replay public broadband recordings through MorletDB, propose candidate
transients with an STA/LTA baseline, retrieve similar historical intervals from
the same station, and inspect waveforms, spectra, provenance and catalog
references in a local Streamlit UI.

> **Research and investigation only.** Waveform similarity is a lead for review.
> This is not a validated earthquake classifier, explosion or nuclear-test
> detector, or public warning service. Distances are not probabilities.

## What it uses

| Piece | Reused component |
|---|---|
| Storage, appends, checkpoints, interval reads, pooled search | `pg_morlet` recording SQL API via `tools/client/morlet_client.py` |
| Morlet extraction | Rust `morlet-core`, explicit `morlet-energy-lowrate-v1` representation for < 128 Hz |
| Temporal retrieval | `evaluation/wave_index.py` offline PoC over a bounded in-memory gallery |
| Seismic formats, filtering, STA/LTA, travel times | ObsPy |

No waveform database or Morlet extraction is reimplemented in Python. App
bookkeeping (segments, progress, discontinuity events, triggers) lives in a
`seismic` schema beside the MorletDB collection `public.seismic_waveforms`.

## Dataset

[`datasets/cx-north-chile-2024.json`](datasets/cx-north-chile-2024.json) selects
three IPOC vertical broadband channels (CX.PB01, PB02, PB08 `HHZ`, 100 Hz, STS-2)
above the persistent ~100 km deep source region near Pica, northern Chile:

| Period | Split | UTC range |
|---|---|---|
| `reference-2024-03` | reference gallery | 2024-03-04 00:00 – 2024-03-11 00:00 |
| `heldout-2024-05` | held-out replay queries | 2024-05-01 00:00 – 2024-05-06 12:00 |

- Waveforms and StationXML: GFZ GEOFON FDSN web services (open access).
  Cite: GFZ & CNRS-INSU (2006), *IPOC Seismic Network*,
  [doi:10.14470/PK615318](https://doi.org/10.14470/PK615318).
- Reference catalog: USGS ANSS ComCat FDSN event service, regional
  (≤ 400 km, M ≥ 2.5) and global (M ≥ 6) queries per period.
- Availability was checked with GEOFON fdsnws-availability before fixing the
  selection. PB08 was kept *because* it has frequent real timing discontinuities.

`download` writes `data/seismic/cx-north-chile-2024-v1/` (git-ignored, ~300 MB):
raw miniSEED day files, StationXML at response level, QuakeML and a
`manifest.json` with exact request URLs/parameters, SHA-256 checksums, byte sizes,
per-trace start/end/npts/rate/quality, instrument metadata, catalog event IDs,
retrieval times and software versions. The committed copy is
[`manifests/cx-north-chile-2024-v1.manifest.json`](manifests/cx-north-chile-2024-v1.manifest.json).
A failed request fails the download; nothing is substituted. Synthetic data
appears only in `tests/`.

## Setup

Requires Docker Compose v2, Rust (see `rust-toolchain.toml`) and Python 3.12.

```sh
# From the repository root
cargo build --locked --release -p morlet-core --example features   # Rust adapter for temporal modes
python3.12 -m venv .venv-seismic
.venv-seismic/bin/pip install -r apps/seismic/requirements.txt
docker compose -f apps/seismic/compose.yaml up -d --wait db          # 127.0.0.1:54339, own volume

cd apps/seismic
../../.venv-seismic/bin/python -m morlet_seismic download            # network, once
../../.venv-seismic/bin/python -m morlet_seismic verify              # offline checksums
../../.venv-seismic/bin/python -m morlet_seismic replay --output ../../data/seismic/replay.json
../../.venv-seismic/bin/streamlit run app.py                         # http://127.0.0.1:8501
```

The database container is separate from the repository walkthrough (`compose.yaml`
at the root uses port 54329 and another volume). Credentials are public demo
values; bind locally only. `docker compose -f apps/seismic/compose.yaml down -v`
deletes the replayed data.

After `download`, everything else runs without network access. Replaying all
three streams unpaced as three parallel processes took 22-32 minutes on an
Apple M5 Max (see the evaluation report); `--streams CX.PB01..HHZ --periods heldout-2024-05` is a
quicker start. Parallel per-stream processes are safe: each stream's
recordings are independent.

## Commands

| Command | Purpose |
|---|---|
| `download [--refresh]` | Fetch public data into the cache and write the manifest |
| `verify` | Re-checksum every cached file against the manifest (offline) |
| `init-db` | Create the MorletDB collection and app tables |
| `replay [--streams ...] [--periods ...] [--speed X] [--max-chunks N]` | Replay cached recordings through ingestion and detection |
| `live --enable-live [--duration-s S]` | Opt-in SeedLink ingestion from `geofon.gfz.de:18000` |
| `status` | Committed progress per stream |
| `evaluate --output report.json [--replay-reports DIR]` | Real-data evaluation report (JSON + Markdown) |

Run them as `python -m morlet_seismic <command>` from `apps/seismic`.

## How ingestion behaves

Historical replay (`ReplaySource`) and live input (`SeedLinkSource`) emit the same
`Chunk` (stream ID, header start time in integer UTC ns, nominal rate, raw counts,
quality code, origin, local emission time) into one `Ingestor`:

- **Coordinates.** Each contiguous run is one MorletDB recording
  `<dataset>/<NET.STA.LOC.CHA>/<first-sample UTC>`; sample *i* is at
  start + *i*/rate. Recording metadata carries station coordinates, orientation
  (azimuth/dip), sensor, sensitivity, split, period, provider and preprocessing version.
- **Gaps.** A chunk that does not start on the segment's sample grid at its
  committed end (tolerance 0.5 sample) opens a new recording. Nothing is
  concatenated across missing time or zero-filled; search windows never cross gaps.
  Absorbed sub-sample header jitter is logged.
- **Duplicates.** Overlap with stored samples is compared exactly. Identical
  overlap (for example adjacent day files, or a replay after restart) is a verified
  duplicate; only new samples are appended.
- **Conflicts.** Differing overlap opens a separate recording and logs an
  `overlap_conflict`; both versions remain readable.
- **Durability.** Samples, MorletDB extraction/checkpoint, segment rows, progress
  (`last_committed_end_ns`) and discontinuity events commit in one transaction.
  On restart, state is reloaded from PostgreSQL and replay resumes by verifying
  the committed prefix. Rejected chunks (wrong rate domain, non-finite or
  out-of-range values) are logged and do not advance state.
- **Pacing.** `--speed 0` is unpaced; `--speed 600` replays ten minutes of samples
  per second. The UI exposes pause, resume and stop.

Live mode connects once per run to the public GEOFON SeedLink server, selects
the chosen streams, reconnects with 10 s → 5 min backoff, commits each packet
promptly and reports connection state, reconnects, dropped packets, queue
backlog and per-stream sample freshness. It never polls an archive and never
runs in tests. It has been implemented but **was not exercised for this
report**: the evaluation used historical replay only.

## Detection, retrieval and catalog references

These are deliberately separate:

1. **Trigger (proposal).** ObsPy recursive STA/LTA (1 s / 30 s, on 4.0, off 1.5,
   ≥ 1 s) on a demeaned, causal 2–10 Hz Butterworth copy of committed samples. Fixed
   10-minute blocks on each recording's sample grid make results independent of
   chunking and restarts. The first 120 s of each recording is unmonitored
   (LTA warm-up). The query window is 2048 samples (20.48 s) from 2 s before onset.
2. **Retrieval (similar history).** Same station/channel, reference split only:
   - `pooled-pg`: MorletDB exact pooled search over every stored reference window
     (stride 512 = 5.12 s), candidate budget 200,000, source exclusion and overlap
     suppression before top-k.
   - `pooled-gallery`, `temporal-exhaustive`, `temporal-approx`: wave-index PoC over
     a bounded gallery (≤ 4096 windows: one per reference trigger plus a 300 s
     background grid), rebuilt in memory from MorletDB samples. Exhaustive
     temporal scoring is the oracle for approximate recall; candidate and posting
     budgets are explicit, and budget exhaustion returns no partial top-k.
3. **Catalog reference (evidence overlay).** USGS events mapped to each station
   with IASP91 travel times. A trigger is associated when its onset lies in
   [P − 5 s, S + 20 s] (regional) or [P − 5 s, P + 30 s] (teleseismic). Origin time is
   not arrival time; the overlay never feeds the detector or retrieval.

Representation: `morlet-energy-lowrate-v1`, identical math to `morlet-energy-v1`
over [1, 128) Hz. At 100 Hz the twelve bands span 1.56–40 Hz; the lowest band's
kernel support is ±1.84 s, so only the central 16.8 s of a 20.48 s window is
covered at every band. Energy below ~1.5 Hz (teleseismic surface waves, long
period) is not represented. Raw counts are stored; instrument response is not
removed, so only same-channel comparisons are meaningful.

## Demonstration walkthrough

1. `verify` prints the checksum-verified file counts (works offline).
2. Interrupted ingestion: `replay --streams CX.PB01..HHZ --max-chunks 300`, then
   a full `replay`. The second run reports `duplicate_samples` equal to the
   interrupted prefix plus day-file overlaps, and the same recording IDs.
3. Open the UI, choose **CX.PB08..HHZ**: the timeline shows dozens of separate
   recordings separated by real gaps (zoom the timeline with the mouse wheel;
   gaps are 0.01-4 s). The discontinuity table lists each gap with its
   size and source file.
4. Choose **CX.PB01..HHZ** → *Investigate an interval*, filter to
   catalog-associated held-out triggers, and select one. The query waveform,
   spectrogram and catalog annotation (event ID, magnitude, onset − predicted P)
   appear separately.
5. Switch retrieval modes. Compare distances, scored windows and posting visits,
   then open matches to compare z-scored waveforms and raw-count spectra with
   their recording coordinates and acquisition metadata.
6. Set `temporal-approx` with candidate budget 4 to see recall against the
   exhaustive oracle drop, or posting budget 100 to see the explicit budget error.

Screenshots of these steps are in [`docs/`](docs/).

## Tests

```sh
cd apps/seismic/tests
../../../.venv-seismic/bin/python -m unittest test_offline test_ingest_db
```

`test_offline` needs no database or network. `test_ingest_db` creates and drops a
temporary database on the local server (`MORLET_SEISMIC_TEST_DSN` overrides the
default) and skips if none is reachable. Fixtures are synthetic.

## Unsupported and next decisions

See [`docs/seismic-investigation.md`](../../docs/seismic-investigation.md) for
the evaluation summary, limitations and decisions.
