#!/usr/bin/env python3
"""Usage: fetch_windows.py YEAR OUT DATA (seismic environment).

Runs the timing test's unchanged pipeline (catalog, window plan, fetch, prepare) for a
year outside its CLI's {2023, 2024} choice. Station coordinates are reused from the
timing run so all periods share them."""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "decision")]
import timing  # noqa: E402

year, out, data = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
stations = HERE.parents[1] / "data/timing-runs/012bb3d/stations.json"
if stations.exists() and not (out / "stations.json").exists():
    shutil.copy(stations, out / "stations.json")
timing.catalog(year, out)
timing.plan_windows(year, out)
timing.fetch(year, out, data)
timing.prepare(year, out, data)
