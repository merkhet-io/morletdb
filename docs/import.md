# From files to a first search

`tools/ingest/morlet_import.py` imports WAV, EDF/EDF+, miniSEED, TDMS or CSV files, or MorletDB exchange files, into a collection, builds the
envelopes that search uses, and prints a query to try. The first time through takes about ten minutes;
most of it is starting the database.

## 1. A database with pg_morlet

The release image (PostgreSQL 17 with pg_morlet; linux/amd64 and, from 0.3.3-alpha.1, linux/arm64 — on ARM
machines add `MORLET_PLATFORM=linux/arm64`):

```sh
MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1 docker compose -f compose.release.yaml up -d db
export DATABASE_URL=postgresql://morlet:morlet-demo-only@127.0.0.1:54329/morlet
psql "$DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS pg_morlet'
```

Or build from source ([installation guide](installation-and-queries.md)). Probe search additionally needs
pgvector 0.8 or later (`CREATE EXTENSION vector`); exact search does not.

## 2. Import

```sh
python -m pip install -r tools/ingest/requirements.txt      # includes npTDMS; add obspy for miniSEED
python tools/ingest/morlet_import.py night.edf --collection public.sleep --create
python tools/ingest/morlet_import.py station/*.mseed --collection public.seismic --create --probes
python tools/ingest/morlet_import.py pump/*.wav --collection public.pumps --create
python tools/ingest/morlet_import.py log.csv --rate 1000 --time-column time --unit g \
    --collection public.rig --create
python tools/ingest/morlet_import.py bench/*.tdms --collection public.bench --create
python tools/ingest/morlet_import.py export.parquet --collection public.copy --create   # exchange file
```

`--dry-run` reads and validates the files without a database and prints what would be imported.

What becomes a recording, and how it is stored:

| Format | One recording per | Values stored | Quantum (sample grid) | Unit |
|---|---|---|---|---|
| WAV (PCM 8/16/24-bit) | channel | integer counts | 1 | `counts` |
| WAV (PCM 32-bit) | channel | counts × 2⁻³¹ (exact) | 2⁻³¹ | `full-scale` |
| EDF, EDF+C | ordinary signal (annotations skipped) | physical values, as EDF readers compute them | the calibration step | the signal's physical dimension |
| miniSEED (ObsPy) | contiguous segment (touching records joined, gaps kept) | counts as stored | 1 | `counts` for integer data |
| CSV with a header row | numeric column | the values as parsed | 10⁻ᵈ for the most decimal places d in the column | `--unit` |
| TDMS (npTDMS) | numeric waveform channel (with `wf_increment`) | scaled (physical) values as npTDMS computes them from the file's scaling; `--tdms-values raw` for the stored values | 1 (values stored exactly either way) | the channel's `unit_string` |
| MorletDB exchange (Parquet) | recording | the exported samples, checked against the file's SHA-256 per recording | as exported | as exported |

The quantum is what lets the codec store calibrated values as integers. It decodes every sample bit for
bit either way, but with the right quantum, EDF and seismic data take a fraction of their float64 size
(on one Sleep-EDF night, 14.3 bits per sample; on a day of CX seismic data, 4.7). Recording identifiers are
`<file stem>.<channel>`, and miniSEED adds the segment's start time. `--prefix` adds a common prefix. EDF
patient and recording identification fields are not copied into metadata.

Segment features use `morlet-energy-v1` for rates of at least 128 Hz and `morlet-energy-lowrate-v1` below.
Envelopes for search are built for every recording with windows of `--window` samples (default 4096)
at half-window stride.

## 3. First search

The importer ends with a query you can run as printed: the ten intervals most similar to a window in the
middle of the longest recording, for example

```sql
SELECT * FROM morlet_search_similar('public.seismic', 'CX.PB01..HHZ.20250228T235958',
  4317184, 4321280, '{}'::jsonb, 10);
```

Each row is a recording, an interval and its distance; `morlet_read_interval` returns the original samples
of any of them. `morlet_search_similar` (SQL 0.3.2) uses probe search when it is enabled and exact search
otherwise. On an older server (0.3.1-alpha.1 or earlier), the importer prints an explicit query instead,
because exact search there refuses galleries of more than 4,096 windows by default.

## Measured on this machine (Apple M5 Max, PostgreSQL 17, local)

| Input | Samples | Import, envelopes (and probes) | First search |
|---|---|---|---|
| one day of CX seismic data (miniSEED, 100 Hz), `--probes` | 8.6 M | 38 s | 56 ms |
| one Sleep-EDF night (7 signals, 100 Hz and 1 Hz) | 24.2 M | 75 s | 55 ms |
| the same night into the release image (`docker compose`, amd64 emulated on arm64) | 24.2 M | 115 s (database up in 26 s, image already pulled) | 159 ms |

Every sample read back bit for bit identical to the source file.

### TDMS

A recording per numeric waveform channel: rate `1 / wf_increment`, start time `wf_start_time` plus
`wf_start_offset` when present, identifier `<file>.<group>.<channel>`. Metadata records the group and channel,
whether the values are `scaled` or `raw`, the TDMS data type and the file's NI scaling properties. Channels without
waveform timing (irregular or timed by a separate time channel), non-numeric channels and empty channels are not
imported; their names and reasons are printed, and a file with no importable channel fails.

Tested on 2026-11-02 with files written by npTDMS 1.9.0 (scaled int16 with a linear scale, float, untimed and
string channels): stored samples are bit for bit what npTDMS reads, with rate, unit and start time. Files written
by NI software were not available for testing. A TDMS file has no gaps within a channel; a recording that
continues in another file is a separate recording. LabVIEW is a trademark of National Instruments; MorletDB is
not affiliated with, endorsed by or sponsored by National Instruments.

### Exchange files

[Exchange files](exchange-format.md) written by `morlet_export.py` import with their recording ids, rates, units,
metadata and sample quanta, after every recording's samples match the file's SHA-256.

## Not yet supported

Float WAV, compressed WAV, BDF, discontinuous EDF+ (EDF+D), ASAM MDF (MF4), TDMS channels without waveform timing
and timestamps that are not uniformly sampled.
CSV files are read into memory. For Parquet sources and other layouts, use
[`parquet_import.py`](../tools/ingest/README.md), which this importer builds on.
