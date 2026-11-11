#!/usr/bin/env python3
"""Grizzbit 2.1 development measurement (DEVELOPMENT ONLY, not a frozen study; no benchmark input).

  python3 -I evaluation/codec_g21/run.py T261_WORK ELF_DIR OUT.json [--chunk 4096] [--reps 3] [--only SUBSTR]

T261_WORK is the work directory written by evaluation/codec_t261/prepare.py (branch dev/t261-reference,
commit 2a89c40): the five B3 development acquisitions K001, KA01, KA09, KB24, KI08 (vibration, both phase
currents, force) and FEMTO Bearing1_1 horizontal, as NAME.f64 (original float64, "as stored") and
NAME.grid.f64 (the exactly-on-grid k*q, "on k*q"), with each channel's quantum in inputs.json. ELF_DIR holds
the Elf CSV files; only B4's four development series are read (PROTOCOL.md "Development": Blockchain-tr,
POI-lat, Stocks-USA, electric_vehicle_charging), at quantum 1 as B4's M-auto arm. Each input runs
g21_bench (build it first: cd evaluation/codec_g21/g21_bench && cargo build --release); every chunk of every
arm is decoded bit for bit there.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE / "g21_bench/target/release/g21_bench"
ELF_DEV = ["Blockchain-tr", "POI-lat", "Stocks-USA", "electric_vehicle_charging"]


def group_of(name, group):
    if group == "femto":
        return "femto"
    for key, g in (("vibration", "vibration"), ("phase_current", "phase currents"), ("force", "force")):
        if key in name:
            return g
    return group


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("elf")
    ap.add_argument("out")
    ap.add_argument("--chunk", type=int, default=4096)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--view", default="", help="only this view ('as stored' or 'on k*q')")
    ap.add_argument("--strategies", action="store_true",
                    help="compare the LPC order-selection strategies instead of the four arms")
    a = ap.parse_args()
    work, elf = Path(a.work), Path(a.elf)
    inputs = json.loads((work / "inputs.json").read_text())["inputs"]
    jobs = []
    for r in inputs:
        g = group_of(r["name"], r["group"])
        jobs.append((r["name"], "as stored", g, work / f"{r['name']}.f64", r["quantum"]))
        jobs.append((r["name"], "on k*q", g, work / f"{r['name']}.grid.f64", r["quantum"]))
    for name in ELF_DEV:
        jobs.append((name, "as stored", "elf (B4 dev)", elf / f"{name}.csv", 1.0))
    rows = []
    for name, view, g, path, q in jobs:
        if (a.only and a.only not in name) or (a.view and a.view != view):
            continue
        env = dict(os.environ, **({"G21_STRATEGIES": "1"} if a.strategies else {}))
        out = subprocess.run([str(BENCH), name, str(path), repr(q), str(a.chunk), str(a.reps)],
                             check=True, capture_output=True, text=True, env=env).stdout
        row = json.loads(out)
        row.update({"view": view, "group": g})
        rows.append(row)
        arms = [k for k, v in row.items() if isinstance(v, dict) and "bits_per_sample" in v]
        print(f"{name[-28:]:28s} {view:9s} " + " ".join(
            f"{k} {row[k]['bits_per_sample']:.3f}/{row[k]['enc_msps']:.1f}/{row[k]['dec_msps']:.0f}" for k in arms),
            flush=True)
    Path(a.out).write_text(json.dumps({"chunk": a.chunk, "reps": a.reps, "strategies": a.strategies,
                                       "rows": rows}, indent=1) + "\n")


if __name__ == "__main__":
    sys.exit(main())
