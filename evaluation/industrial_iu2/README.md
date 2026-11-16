# IU2: bounded-lag envelope distance and tool wear (milling tools run to failure)

Evidence for the paper. Public data only.

| File | Content |
|---|---|
| `PROTOCOL.md` | Protocol frozen on 2026-11-11 before the raw signal files were read, with deviations 1 to 4 |
| `RESULTS.md` | Results W1 to W4, reading and limits |
| `peek.py` | Lists the remote archive's directory through HTTP range requests (written before the freeze; gives `zip_listing.json`) |
| `meta2.py` | Tool table from `metadata.xlsx` and the listing (gives `tools.json`) |
| `iu2.py` | Harness: `plan` (cycle table from metadata), `load` (reads the CSV files from the zip archive without extracting), `score` |
| `iu2_analyze.py` | Decisions and `results/summary.json` |
| `figures.py` | Figures 1 and 2 |
| `results/summary.json` | Every number of the study |
| `results/cycles.json` | Cycle table: order in time, position, baseline flag, split |
| `results/cycle_scores.json` | One row per scored cycle with every score |
| `results/scores.json.gz` | Raw per-window distances (BL, ZL, RMS window) |
| `results/load.json` | Versions, storage and cycle lengths. `seconds` is the resumed part of the load only (deviation 4) |
| `results/run.log`, `run2.log`, `report.txt` | Logs of the first load (stopped by a memory error), of the resumed run, and the printed report |

## Replay the committed analysis (no database)

From the repository root with NumPy 2.1.x:

```sh
python research/paper/replay_industrial.py --check --studies iu2
```

This explicitly reuses `cycle_scores.json` and validates its BL/ZL/RMS medians and
file universe against `scores.json.gz`. It does not independently regenerate the
feature table. For fresh scores, supply `--features` to the analyzer instead.

## Reproduce

Data: Piecuch and Żabiński, Scientific Data 12, 650 (2025), Figshare doi 10.6084/m9.figshare.28589216 (CC BY).
Download `raw_data.zip` (25.3 GB, MD5 615fef19c51344a163acea913d3f7f22), `metadata.xlsx` and
`FeatureAndMetadata_Milling.csv` into `evaluation/data/toollife/` (repository-relative). Software: MorletDB main at 088f03b (SQL 0.3.8), image as in
`industrial_iu1` (`compose.yaml`, `Dockerfile.db`); a Python image with `pyarrow`, `psycopg`, `numpy`, `openpyxl`.

```sh
# From evaluation/industrial_iu1; build its historical database image first.
docker compose run --rm -w /w/iu2 runner python meta2.py
docker compose run --rm -w /w/iu2 runner python iu2.py plan
docker compose run --rm -w /w/iu2 -e IU2_THREADS=2 runner python iu2.py load
docker compose run --rm -w /w/iu2 runner python iu2.py score
docker compose run --rm -w /w/iu2 runner python iu2_analyze.py --features /w/data/toollife/FeatureAndMetadata_Milling.csv
```

Paths inside the scripts are container paths (`/w/data/toollife`, `/w/iu2`). The archive is 111.8 GB unzipped and
is read file by file from the zip. Measured on a Windows 11 laptop with Docker Desktop (16 logical CPUs, 7.2 GB
for Docker).
