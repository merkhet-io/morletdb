#!/usr/bin/env python3
"""IU4 step 1: one machine's HDF5 runs -> one .npz per run (PROTOCOL.md, Implementation).

  convert.py H5_DIR OUT_DIR MACHINE      (h5py, numpy; never prints signal values)

For every file M<machine>T<tool>R<run>...h5 of the machine: the 9 signals of the protocol, float32 widened exactly to
float64, in one .npz named M<m>T<t>R<r>.npz, plus the labels. Each run's time vectors are compared with the nominal
axis (25 kHz sensor, 500 Hz machine); the largest departure and the fitted rate are written to OUT_DIR/time_axis_<m>.json
(#393: reported, not corrected). Only the requested machine is read, so the confirmation machines stay unread until
the build record is committed.
"""
import json
import re
import sys
from pathlib import Path

import h5py
import numpy as np

SENSOR = ("force_sensor_x", "force_sensor_y", "force_sensor_z")
MACHINE = ("torque_spindle", "torque_axis_x", "torque_axis_y", "torque_axis_z",
           "position_control_deviation_axis_x", "position_control_deviation_axis_y")
RATES = {"sensor": 25000.0, "machine": 500.0}
NAME = re.compile(r"^M(\d+)T(\d+)R(\d+)C\d+VB\d+\.h5$")


def axis(t, rate):
    t = np.asarray(t, dtype=np.float64).ravel()
    off = (t - t[0]) - np.arange(t.size) / rate
    slope = float(np.polyfit(np.arange(t.size, dtype=np.float64), t, 1)[0]) if t.size > 2 else float("nan")
    return {"max_offset_s": float(np.max(np.abs(off))), "end_offset_s": float(off[-1]),
            "fitted_rate_hz": (1.0 / slope) if slope > 0 else None, "samples": int(t.size)}


def main():
    src, out, machine = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in src.rglob("*.h5") if (m := NAME.match(p.name)) and int(m.group(1)) == machine)
    report = {}
    for i, p in enumerate(files):
        m, t, r = (int(x) for x in NAME.match(p.name).groups())
        with h5py.File(p, "r") as f:
            arrays = {}
            for name in SENSOR:
                arrays[name] = np.asarray(f["signals_sensor"][name][0], dtype=np.float32).astype(np.float64)
            for name in MACHINE:
                arrays[name] = np.asarray(f["signals_machine"][name][0], dtype=np.float32).astype(np.float64)
            labels = {k: float(np.asarray(f["labels"][k]).ravel()[0]) for k in f["labels"]}
            report[f"M{m}T{t}R{r}"] = {"sensor": axis(f["signals_sensor"]["time_sensor"][0], RATES["sensor"]),
                                       "machine": axis(f["signals_machine"]["time_machine"][0], RATES["machine"])}
        if any(not np.all(np.isfinite(a)) for a in arrays.values()):
            raise ValueError(f"{p.name}: non-finite values")
        np.savez(out / f"M{m}T{t}R{r}.npz", **arrays, labels=json.dumps(labels), source=p.name)
        if i % 200 == 0:
            print(f"{i + 1}/{len(files)} {p.name}", flush=True)
    (out / f"time_axis_{machine}.json").write_text(json.dumps(report))
    print(f"machine {machine}: {len(files)} runs converted", flush=True)


if __name__ == "__main__":
    main()
