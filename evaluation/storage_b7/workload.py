#!/usr/bin/env python3
"""B7 workload (PROTOCOL.md, "Dataset"): float32-exact CNC milling signals and Paderborn mid-rate signals.

  ../../.venv-b3/bin/python workload.py manifest        # writes results/manifest.json (B7_SET=dev: results/dev/)

Groups (one table per group in ModelarDB, one recording per (unit, channel) in MorletDB):
  force:   IU4 (Denkena 2023) force_sensor_x/y/z, 25 kHz (40 us), float32 at the source
  ctrl:    IU4 torque_spindle, torque_axis_x/y/z, position_control_deviation_axis_x/y, 500 Hz (2 ms), float32 source
  pmid:    Paderborn force/speed/torque, 4 kHz (250 us), float64 rounded to the nearest float32 (conversion reported)
  pvib:    side arm, Paderborn vibration_1, 64 kHz (15.625 us: not a whole microsecond), float32-exact grid values
Unit k (0-based, in the order below) starts at 2026-01-01T00:00:00Z + k x 60 s; sample n is at start + n x period.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MAIN = Path(os.environ.get("B7_DATA_ROOT", "/Users/zin/sophia/morletdb"))       # data/ lives in the main checkout
IU4 = MAIN / "data/denkena2023/npz"
PAD = MAIN / "data/paderborn/zenodo-15845309/extracted"
SET = os.environ.get("B7_SET", "bench")
RESULTS = Path(__file__).resolve().parent / "results" / ("dev" if SET == "dev" else "")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SLOT_S = 60
GROUPS = {
    "force": {"channels": ["force_sensor_x", "force_sensor_y", "force_sensor_z"], "rate": 25000.0, "period_ns": 40_000},
    "ctrl": {"channels": ["torque_spindle", "torque_axis_x", "torque_axis_y", "torque_axis_z",
                         "position_control_deviation_axis_x", "position_control_deviation_axis_y"],
             "rate": 500.0, "period_ns": 2_000_000},
    "pmid": {"channels": ["force", "speed", "torque"], "rate": 4000.0, "period_ns": 250_000},
    "pvib": {"channels": ["vibration_1"], "rate": 64000.0, "period_ns": 15_625},
}
MAIN_GROUPS = ("force", "ctrl", "pmid")


def iu4_runs():
    """Every 20th IU4 run sorted by (machine, tool, run number); dev: every 20th starting at the 11th."""
    import re
    runs = []
    for p in IU4.glob("M*T*R*.npz"):
        m = re.match(r"M(\d+)T(\d+)R(\d+)\.npz$", p.name)
        runs.append((int(m[1]), int(m[2]), int(m[3]), p))
    runs.sort()
    if len(runs) != 6418:
        raise SystemExit(f"expected 6,418 IU4 runs, found {len(runs)}")
    return [r[3] for r in (runs[10::20] if SET == "dev" else runs[::20])]


def pad_files():
    """B3's acquisitions: every 20th of the 2,480 Paderborn files sorted by path (dev: from the 11th)."""
    allf = sorted(PAD.glob("*/*.mat"))
    if len(allf) != 2480:
        raise SystemExit(f"expected 2,480 Paderborn files, found {len(allf)}")
    return allf[10::20] if SET == "dev" else allf[::20]


def start_ns(k):
    return int((T0 + timedelta(seconds=k * SLOT_S)).timestamp()) * 1_000_000_000


def units(group):
    """[(unit id, path)] for a group, in load order."""
    if group in ("force", "ctrl"):
        return [(p.stem, p) for p in iu4_runs()]
    return [(p.stem, p) for p in pad_files()]


def _pad_load(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("b3_workload", MAIN / "evaluation/tsdb_b3/workload.py")
    b3w = importlib.util.module_from_spec(spec)          # B3's reader of the .mat files, under its own name
    spec.loader.exec_module(b3w)
    return b3w.load(path)


def load(group, path):
    """{channel: float32 array} for one unit of a group. pmid is rounded to float32 (the conversion is in
    conversion(); pvib vibration_1 is exact in float32)."""
    if group in ("force", "ctrl"):
        z = np.load(path)
        out = {}
        for ch in GROUPS[group]["channels"]:
            a = z[ch]
            f = a.astype(np.float32)
            if not np.array_equal(f.astype(np.float64), a, equal_nan=True):
                raise SystemExit(f"{path.name} {ch}: not float32-exact")
            out[ch] = f
        return out
    m = _pad_load(path)
    return {ch: np.asarray(m[ch], dtype=np.float64).astype(np.float32) for ch in GROUPS[group]["channels"]}


def conversion(group, path):
    """pmid/pvib: the float64 -> float32 rounding error per channel (max abs, RMS, share of changed values)."""
    m = _pad_load(path)
    out = {}
    for ch in GROUPS[group]["channels"]:
        a = np.asarray(m[ch], dtype=np.float64)
        d = a.astype(np.float32).astype(np.float64) - a
        out[ch] = {"n": int(a.size), "changed": int(np.count_nonzero(d)), "max_abs": float(np.max(np.abs(d))) if a.size else 0.0,
                   "rms": float(np.sqrt(np.mean(d * d))) if a.size else 0.0}
    return out


def sha32(a):
    return hashlib.sha256(np.ascontiguousarray(a, dtype="<f4").tobytes()).hexdigest()


def manifest():
    RESULTS.mkdir(parents=True, exist_ok=True)
    man = {"set": SET, "groups": {}}
    for g in GROUPS:
        entries, conv = [], {}
        for k, (uid, path) in enumerate(units(g)):
            data = load(g, path)
            entries.append({"unit": uid, "index": k, "file": str(path.relative_to(MAIN)),
                            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "channels": {ch: {"n": int(a.size), "sha256_f32": sha32(a)} for ch, a in data.items()}})
            if g in ("pmid", "pvib"):
                for ch, c in conversion(g, path).items():
                    t = conv.setdefault(ch, {"n": 0, "changed": 0, "max_abs": 0.0, "sq": 0.0})
                    t["n"] += c["n"]; t["changed"] += c["changed"]; t["max_abs"] = max(t["max_abs"], c["max_abs"])
                    t["sq"] += c["rms"] ** 2 * c["n"]
        man["groups"][g] = {"units": entries, "samples": sum(sum(c["n"] for c in e["channels"].values()) for e in entries),
                            **({"float32_rounding": {ch: {"n": t["n"], "changed": t["changed"], "max_abs": t["max_abs"],
                                                          "rms": (t["sq"] / t["n"]) ** 0.5 if t["n"] else 0.0}
                                                     for ch, t in conv.items()}} if conv else {})}
        print(g, len(entries), "units", man["groups"][g]["samples"], "samples", flush=True)
    (RESULTS / "manifest.json").write_text(json.dumps(man, indent=1))


if __name__ == "__main__":
    if sys.argv[1:] == ["manifest"]:
        manifest()
