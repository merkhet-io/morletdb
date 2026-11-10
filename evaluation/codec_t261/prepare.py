#!/usr/bin/env python3
"""Codec comparison T1 (development only): write the inputs for Grizzbit 1 and the T.261 reference software.

  .venv-b3/bin/python -I evaluation/codec_t261/prepare.py WORK_DIR

Inputs (development only, never a frozen study's benchmark inputs):
- five acquisitions of B3's development set (B3_SET=dev; positions 0, 25, 50, 75, 100 of its 124 files), every
  channel of the high (64 kHz) and mid (4 kHz) groups; the 1 Hz temperature (5 samples) is too short to code;
- FEMTO Bearing1_1 horizontal (column 4 of acc_*.csv, 0.001 grid) and XJTU-SY Bearing1_1 horizontal (column 0),
  the two non-B4 waveforms of the Grizzbit 2 spike.

Each channel is classified:
- exact: some step q gives x == k*q (or x == k/d for a decimal step 1/d, the way Grizzbit decodes such grids)
  bit for bit for every sample, integer k. T.261 gets k, Grizzbit gets the original float64 with quantum q:
  both code the same information losslessly (the like-for-like comparison).
- near: every sample is within 1e-3 steps of offset + k*q (Grizzbit's own grid tolerance) but not bit for bit
  (calibrated float64, or negative zeros: Grizzbit stores the integers plus last-place corrections). T.261 cannot hold the
  corrections, so this is an integer-coder view only: T.261 gets k, Grizzbit gets the exactly-on-grid values
  k*q (NAME.grid.f64, no corrections needed), and Grizzbit's real cost on the original float64 is reported too.
- excluded: anything else (no grid; or too short), with the reason.
WORK_DIR gets NAME.f64 (original float64 LE), NAME.grid.f64 (k*q), NAME.k (int16 or int32 LE, T.261 'Raw'), and
per acquisition ACQ.high.k with the three 64 kHz channels interleaved (joint view); inputs.json lists everything.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "evaluation/tsdb_b3"))
os.environ["B3_SET"] = "dev"  # development set only
import workload  # noqa: E402

PICK = [0, 25, 50, 75, 100]  # positions in B3's development set
MIN_SAMPLES = 4096
NEAR = 1e-3  # Grizzbit's NEAR_GRID


def detected_steps(x):
    """Candidate steps the way Grizzbit's detector finds them: smallest gap between distinct values, / 1..8."""
    v = np.unique(x)
    if len(v) < 3:
        return []
    gap = np.diff(v)
    gap = gap[gap > max(abs(v[0]), abs(v[-1])) * 1e-12]
    if len(gap) == 0:
        return []
    g = float(gap.min())
    return [g / m for m in range(1, 9)]


def candidates(x, hints):
    out = []
    for q in hints + detected_steps(x):
        inv = 1.0 / q
        if round(inv) >= 1 and abs(inv - round(inv)) <= 1e-9 * round(inv):
            out.append(("div", float(round(inv))))
        out.append(("mul", q))
    return out


def on(mode, s, k):
    return k / s if mode == "div" else k * s


def classify(x, hints):
    """('exact', mode, scale, k) | ('near', mode, scale, offset, k, max_dev_in_steps) | None."""
    cands = candidates(x, hints)
    for mode, s in cands:
        k = np.round(x * s) if mode == "div" else np.round(x / s)
        if np.abs(k).max() > 2**31 - 1:
            continue
        scales = [s]
        if mode == "mul":  # the step refitted from the largest |k|, +-2 ulps (as Grizzbit refits)
            i = int(np.argmax(np.abs(k)))
            if k[i] != 0:
                b = np.float64(x[i] / k[i]).view(np.int64)
                scales += [float(np.int64(b + d).view(np.float64)) for d in range(-2, 3)]
        for sc in scales:
            # + 0.0: an integer index has no negative zero, so -0.0 samples are not exact (FEMTO has 2,282).
            kk = (np.round(x * sc) if mode == "div" else np.round(x / sc)) + 0.0
            if np.array_equal(on(mode, sc, kk).view(np.int64), x.view(np.int64)):
                return "exact", mode, sc, kk.astype(np.int64)
    for mode, s in cands:
        q = 1.0 / s if mode == "div" else s
        for off in (0.0, float(x[0] - np.round(x[0] / q) * q)):
            k = np.round((x - off) / q)
            if np.abs(k).max() > 2**31 - 1:
                continue
            dev = float(np.abs(x - (off + k * q)).max() / q)
            if dev <= NEAR:
                return "near", mode, s, off, k.astype(np.int64), dev
    return None


def bits_for(k):
    lo, hi = int(k.min()), int(k.max())
    b = 2
    while not (-(1 << (b - 1)) <= lo and hi < (1 << (b - 1))):
        b += 1
    return b


def write_k(path, cols, bits):
    a = np.stack(cols, axis=1) if len(cols) > 1 else cols[0]
    a.astype("<i2" if bits <= 16 else "<i4").tofile(path)


def csv_column(paths, col):
    out = []
    for p in paths:
        for line in p.read_text().splitlines():
            parts = line.split(",")
            if len(parts) > col:
                try:
                    out.append(float(parts[col]))
                except ValueError:
                    pass
    return np.array(out, dtype="<f8")


def main():
    work = Path(sys.argv[1])
    work.mkdir(parents=True, exist_ok=True)
    acqs = workload.acquisitions()
    series, joint = [], []
    for i in PICK:
        _, acq, path, _ = acqs[i]
        data = workload.load(path)
        for g, spec in workload.GROUPS.items():
            for c in spec["channels"]:
                series.append((f"{acq}.{c}", "b3-dev", g, spec["rate"], data[c],
                               [workload.QUANTUM[c]] if c in workload.QUANTUM else []))
        joint.append((acq, [f"{acq}.{c}" for c in workload.GROUPS["high"]["channels"]]))
    femto = ROOT / "data/femto/extracted/Learning_set/Bearing1_1"
    series.append(("FEMTO.B1_1.horiz", "nonB4", "femto", 25600.0,
                   csv_column([femto / f"acc_{i:05}.csv" for i in range(1, 1639)], 4), [0.001]))
    xjtu = ROOT / "data/xjtu-sy/extracted/XJTU-SY_Bearing_Datasets/35Hz12kN/Bearing1_1"
    series.append(("XJTU-SY.B1_1.horiz", "nonB4", "xjtu", 25600.0,
                   csv_column([xjtu / f"{i}.csv" for i in range(1, 124)], 0), []))

    inputs, excluded = [], []
    for name, set_, g, rate, x, hints in series:
        x = np.ascontiguousarray(x, dtype="<f8")
        if len(x) < MIN_SAMPLES:
            excluded.append({"name": name, "samples": len(x), "why": f"fewer than {MIN_SAMPLES} samples"})
            continue
        c = classify(x, hints)
        if c is None:
            f32 = np.array_equal(x.astype(np.float32).astype(np.float64), x)
            excluded.append({"name": name, "samples": len(x), "why": "no grid: not within 1e-3 steps of any "
                             "offset + k*step" + (" (float32 values)" if f32 else "")})
            continue
        if c[0] == "exact":
            _, mode, scale, k = c
            off, dev = 0.0, 0.0
        else:
            _, mode, scale, off, k, dev = c
        quantum = 1.0 / scale if mode == "div" else scale
        bits = bits_for(k)
        x.tofile(work / f"{name}.f64")
        on(mode, scale, k.astype(np.float64)).astype("<f8").tofile(work / f"{name}.grid.f64")
        write_k(work / f"{name}.k", [k], bits)
        inputs.append({"name": name, "set": set_, "group": g, "rate": rate, "samples": len(x), "class": c[0],
                       "mode": mode, "scale": scale, "quantum": quantum, "offset": off, "max_dev_steps": dev,
                       "bits": bits, "k_min": int(k.min()), "k_max": int(k.max()),
                       "distinct": int(len(np.unique(k)))})
    ok = {r["name"]: r for r in inputs}
    joints = []
    for acq, names in joint:
        if all(n in ok for n in names):
            bits = max(ok[n]["bits"] for n in names)
            ks = [np.fromfile(work / f"{n}.k", dtype="<i2" if ok[n]["bits"] <= 16 else "<i4").astype(np.int64)
                  for n in names]
            write_k(work / f"{acq}.high.k", ks, bits)
            joints.append({"name": f"{acq}.high", "channels": names, "bits": bits, "samples": len(ks[0])})
    (work / "inputs.json").write_text(json.dumps({"inputs": inputs, "joint": joints, "excluded": excluded}, indent=1))
    for r in inputs:
        print(f"{r['class']:5s} {r['name']:40s} n={r['samples']:8d} {r['mode']} q={r['quantum']!r} off={r['offset']:.6g} "
              f"dev={r['max_dev_steps']:.2g} bits={r['bits']} distinct={r['distinct']}")
    for r in excluded:
        print(f"EXCLUDED {r['name']}: {r['why']}")


if __name__ == "__main__":
    main()
