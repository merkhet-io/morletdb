"""Benchmark datasets as integer ADC blocks with independent-unit labels (PROTOCOL.md)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "decision")]
DATA = HERE.parents[2] / "morletdb" / "data" if (HERE.parents[2] / "morletdb" / "data").exists() else HERE.parents[1] / "data"

PADERBORN_ROWS = 62  # 62 x 4096 = 253,952 samples per 4 s file (SPERR needs full rows)
CX_BLOCK = 16 * 4096  # 65,536 samples (10.9 min at 100 Hz)
IMS_BLOCK = 20480  # one snapshot (about 1 s at 20 kHz)


def paderborn(role: str, files_per_cell: int | None = 2, root: Path | None = None):
    """Accepted vibration files (frozen 0.01% rate rule) as int16 ADC counts.

    files_per_cell: the first N accepted measurements per bearing x setting (None = all).
    """
    import bearings as b
    import paderborn_mat as pm
    root = root or DATA / "paderborn/zenodo-15845309/extracted"
    names = b.DEV if role == "dev" else b.HELDOUT
    step = 0.0030517578104827248  # observed ADC step in the files (100/32768 to ~7e-10 relative)
    for bearing in names:
        per_cell = {}
        for path in sorted((root / bearing).glob("*.mat"), key=lambda p: (p.name[:11], int(p.stem.split("_")[-1]))):
            setting, _, measurement, vib, checks = pm.load_file(path)
            if not checks["rate_ok"]:
                continue
            if files_per_cell is not None and per_cell.get(setting, 0) >= files_per_cell:
                continue
            per_cell[setting] = per_cell.get(setting, 0) + 1
            q = np.round(vib / step)
            grid_dev = float(np.abs(q * step - vib).max() / step)
            if grid_dev > 1e-3:  # integer recovery is unambiguous far below 0.5 step
                raise ValueError(f"{path.name}: not on the ADC grid ({grid_dev})")
            x = q.astype(np.int64)[:PADERBORN_ROWS * 4096]
            location, origin, extent = b.BEARINGS[bearing]
            yield {"dataset": "paderborn", "unit": bearing, "id": path.stem, "x": x, "step": step, "grid_dev": grid_dev,
                   "rate": 64000.0, "setting": setting, "measurement": measurement,
                   "location": location, "origin": origin, "extent": extent}


def cx(role: str, root: Path | None = None):
    """Continuous IPOC CX HHZ int32 counts in 65,536-sample blocks; unit = station."""
    root = root or DATA / "cx-continuous"
    manifest = json.loads((root / "manifest.json").read_text())
    for m in manifest:
        if m["role"] != role:
            continue
        for seg in m["segments"]:
            x = np.load(root / role / seg["file"]).astype(np.int64)
            for k in range(len(x) // CX_BLOCK):
                yield {"dataset": "cx", "unit": m["station"], "id": f"{seg['file']}#{k}",
                       "x": x[k * CX_BLOCK:(k + 1) * CX_BLOCK], "step": 1.0, "rate": seg["rate"],
                       "start": seg["start"], "offset": k * CX_BLOCK}


def ims_grid(values: np.ndarray):
    """IMS values are ASCII decimals; find their quantization step to recover integer counts."""
    u = np.unique(np.round(values, 6))
    step = float(np.min(np.diff(u)))
    q = np.round(values / step)
    return step, float(np.abs(q * step - values).max())


IMS_SETS = {"dev": ("set1",), "heldout": ("set2", "set3")}
IMS_FAILING = {"set1": (3, 4), "set2": (1,), "set3": (3,)}  # readme: bearing numbers


def ims(role: str, every: int | None = 10, root: Path | None = None, bearings=None, sets=None):
    """IMS snapshots as int16 counts of 0.001 (as distributed); unit = set:bearing."""
    root = root or DATA / "ims/npz"
    for name in sets or IMS_SETS[role]:
        d = np.load(root / f"{name}.npz")
        x, t = d["x"], d["t"]
        idx = range(0, len(x), every) if every else range(len(x))
        for ch in range(x.shape[2]):
            bearing = ch + 1
            if bearings is not None and bearing not in bearings:
                continue
            for i in idx:
                yield {"dataset": "ims", "unit": f"{name}:B{bearing}", "id": f"{name}:B{bearing}:{i}",
                       "x": x[i, :, ch].astype(np.int64), "step": 0.001, "rate": 20000.0,
                       "snapshot": i, "time": float(t[i]), "failing": bearing in IMS_FAILING[name]}
