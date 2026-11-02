#!/usr/bin/env python3
"""B3 workload: 124 Paderborn acquisitions with exact nanosecond timing (PROTOCOL.md, "Dataset").

  .venv-b3/bin/python evaluation/tsdb_b3/workload.py manifest      # writes results/manifest.json

Other B3 scripts import load() and channels(); every adapter checks what it stored against the manifest.
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import scipy.io as sio

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/paderborn/zenodo-15845309/extracted"
RESULTS = Path(__file__).resolve().parent / "results"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SLOT_S = 60
GROUPS = {
    "high": {"channels": ["vibration_1", "phase_current_1", "phase_current_2"], "rate": 64000.0, "period_ns": 15625},
    "mid": {"channels": ["force", "speed", "torque"], "rate": 4000.0, "period_ns": 250000},
    "process": {"channels": ["temp_2_bearing_module"], "rate": 1.0, "period_ns": 1_000_000_000},
}
QUANTUM = {"vibration_1": 100 / 32768, "phase_current_1": 0.0006887817378, "phase_current_2": 0.0006887817378}
UNITS = {"vibration_1": "as distributed", "phase_current_1": "A", "phase_current_2": "A", "force": "N",
         "speed": "rpm", "torque": "Nm", "temp_2_bearing_module": "degC"}


def files():
    """The 124 acquisitions: every 20th of the 2,480 files sorted by path, starting with the first."""
    all_files = sorted(DATA.glob("*/*.mat"))
    if len(all_files) != 2480:
        raise SystemExit(f"expected 2,480 Paderborn files, found {len(all_files)}")
    return all_files[::20]


def start_ns(k):
    return int((T0 + timedelta(seconds=k * SLOT_S)).timestamp()) * 1_000_000_000


def load(path):
    """{channel: float64 array} for one acquisition."""
    m = sio.loadmat(path)
    s = m[path.stem][0, 0]
    out = {}
    for y in s["Y"][0]:
        name = str(y["Name"][0])
        if name in UNITS:
            out[name] = np.ascontiguousarray(y["Data"].ravel(), dtype="<f8")
    return out


def sha(x):
    return hashlib.sha256(np.ascontiguousarray(x, dtype="<f8").tobytes()).hexdigest()


def acquisitions():
    """[(k, acquisition id, path, start ns)]."""
    return [(k, p.stem, p, start_ns(k)) for k, p in enumerate(files())]


def manifest():
    rows, totals = [], {g: 0 for g in GROUPS}
    for k, acq, path, t0 in acquisitions():
        data = load(path)
        chans = {}
        for g, spec in GROUPS.items():
            lens = {len(data[c]) for c in spec["channels"]}
            if len(lens) != 1:
                raise SystemExit(f"{acq}: channels of group {g} differ in length {lens}")
            for c in spec["channels"]:
                chans[c] = {"group": g, "samples": int(len(data[c])), "sha256": sha(data[c])}
                totals[g] += len(data[c])
        rows.append({"k": k, "acquisition": acq, "path": str(path.relative_to(ROOT)), "start_ns": t0, "channels": chans})
    out = {"dataset": "Paderborn bearing data (Zenodo 15845309 copy), every 20th of 2,480 files", "slot_s": SLOT_S,
           "groups": GROUPS, "quantum": QUANTUM, "units": UNITS, "acquisitions": rows,
           "samples": totals, "canonical_bytes": {g: 8 * n for g, n in totals.items()}}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "manifest.json").write_text(json.dumps(out, indent=1) + "\n")
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["manifest"]:
        m = manifest()
        print(json.dumps({"acquisitions": len(m["acquisitions"]), "samples": m["samples"]}))
