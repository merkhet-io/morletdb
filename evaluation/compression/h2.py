#!/usr/bin/env python3
"""H2: does diagnostic evidence survive compression at each codec's dev operating point?

  paderborn  detection sensitivity at 5% false alarms (D-KURT, D-ENV), all accepted files
  ims        alarm time on the run-to-failure bearings (D-KURT, D-ENV, RMS), all snapshots
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import bench  # noqa: E402
import datasets  # noqa: E402
import fidelity  # noqa: E402

DAMAGED_HELDOUT = None  # filled from bearings.py


def codecs_at_operating(op, dataset):
    """[(label, codec, param, eps)] for every lossy codec with a dev operating point."""
    out = []
    params = op[dataset]["params"]
    for key, v in op[dataset]["operating"].items():
        name, p = key.split("|", 1)
        if v["r"] is None:
            continue
        p = None if p == "None" else (float(p) if name == "dwt-q" else int(float(p)))
        if name in ("dwt-q", "harmonic") and params.get(name) != p:
            continue  # only the parameter chosen on dev
        out.append((f"{name}@{v['eps']:.3g}", name, p, v["eps"]))
    return out


def scores(args):
    block, variants = args
    x, rate, ds = block["x"], block["rate"], block["dataset"]
    row = {k: block[k] for k in block if k != "x"}
    d = fidelity.detectors(x, rate, ds)
    row["orig"] = {**d, "rms": float(np.std(x))}
    for label, name, p, eps in variants:
        _, decode = bench.encode_lossy(name, p, x, eps, block)
        y = np.asarray(decode(), float)
        row[label] = {**fidelity.detectors(y, rate, ds), "rms": float(np.std(y))}
    return row


def paderborn(out: Path, workers: int):
    import bearings as b
    op = json.loads((out / "operating.json").read_text())
    variants = codecs_at_operating(op, "paderborn")
    blocks = [blk for role in ("dev", "heldout") for blk in datasets.paderborn(role, files_per_cell=None)]
    with mp.Pool(workers) as pool:
        rows = pool.map(scores, [(blk, variants) for blk in blocks], chunksize=2)
    reps = ["orig"] + [v[0] for v in variants]
    healthy_dev = [r for r in rows if r["unit"] in b.DEV and r["location"] == "healthy"]
    held = [r for r in rows if r["unit"] in b.HELDOUT]
    damaged = sorted({r["unit"] for r in held if r["location"] != "healthy"})
    healthy_held = sorted({r["unit"] for r in held if r["location"] == "healthy"})
    result = {"files": len(rows), "healthy_dev_files": len(healthy_dev), "damaged_heldout_bearings": damaged,
              "healthy_heldout_bearings": healthy_held, "representations": reps, "detectors": {}}
    for det in ("d_kurt", "d_env"):
        thr = {rep: float(np.percentile([r[rep][det] for r in healthy_dev], 95)) for rep in reps}
        sens = {rep: {u: float(np.mean([r[rep][det] > thr[rep] for r in held if r["unit"] == u])) for u in damaged}
                for rep in reps}
        fa_orig_thr = {rep: float(np.mean([r[rep][det] > thr["orig"] for r in held if r["unit"] in healthy_held]))
                       for rep in reps}
        sens_orig_thr = {rep: float(np.mean([np.mean([r[rep][det] > thr["orig"] for r in held if r["unit"] == u])
                                             for u in damaged])) for rep in reps}
        comps = {}
        for rep in reps[1:]:
            c = bootstrap({u: sens[rep][u] - sens["orig"][u] for u in damaged})
            weak = [u for u in damaged if b.BEARINGS[u][2] == 1]
            c["weak_extent1_mean"] = float(np.mean([sens[rep][u] - sens["orig"][u] for u in weak]))
            c["non_inferior_margin_0.05"] = c["ci95"][0] > -0.05
            comps[rep] = c
        result["detectors"][det] = {"threshold_5pct_fa_dev_healthy": thr,
                                    "mean_sensitivity": {rep: float(np.mean(list(sens[rep].values()))) for rep in reps},
                                    "per_bearing_sensitivity": sens, "delta_vs_orig": comps,
                                    "false_alarm_heldout_healthy_at_orig_threshold": fa_orig_thr,
                                    "sensitivity_at_orig_threshold": sens_orig_thr}
    (out / "h2-paderborn.json").write_text(json.dumps(result, indent=1))
    for det, d in result["detectors"].items():
        print(det, "orig sens", round(d["mean_sensitivity"]["orig"], 3))
        for rep, c in d["delta_vs_orig"].items():
            print(f"  {rep:28s} dSens {c['diff']:+.3f} [{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}] "
                  f"NI={c['non_inferior_margin_0.05']}  FA@origThr {d['false_alarm_heldout_healthy_at_orig_threshold'][rep]:.3f}")


def bootstrap(per_unit: dict, resamples=2000, seed=7):
    keys = sorted(per_unit)
    v = np.array([per_unit[k] for k in keys])
    rng = np.random.default_rng(seed)
    boots = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(resamples)]
    return {"diff": float(v.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "units": len(keys)}


def alarm(series, baseline_frac=0.25, pct=99, run=3):
    n = len(series)
    base = int(n * baseline_frac)
    thr = np.percentile(series[:base], pct)
    above = series > thr
    for i in range(base, n - run + 1):
        if above[i:i + run].all():
            return i, float(thr)
    return None, float(thr)


def ims(out: Path, workers: int):
    op = json.loads((out / "operating.json").read_text())
    variants = codecs_at_operating(op, "ims")
    cases = [("dev", "set1", 3), ("dev", "set1", 4), ("heldout", "set2", 1), ("heldout", "set3", 3)]
    result = {"cases": {}}
    for role, name, bearing in cases:
        blocks = list(datasets.ims(role, every=None, bearings=(bearing,), sets=(name,)))
        with mp.Pool(workers) as pool:
            rows = pool.map(scores, [(blk, variants) for blk in blocks], chunksize=8)
        times = np.array([r["time"] for r in rows])
        hours = (times - times[0]) / 3600
        reps = ["orig"] + [v[0] for v in variants]
        case = {"role": role, "snapshots": len(rows), "duration_h": float(hours[-1]), "alarms": {}}
        for det in ("d_kurt", "d_env", "rms"):
            case["alarms"][det] = {}
            for rep in reps:
                s = np.array([r[rep][det] for r in rows])
                i, thr = alarm(s)
                case["alarms"][det][rep] = {"index": i, "hours": float(hours[i]) if i is not None else None,
                                            "threshold": thr}
            o = case["alarms"][det]["orig"]["index"]
            for rep in reps[1:]:
                d = case["alarms"][det][rep]["index"]
                case["alarms"][det][rep]["delay_snapshots"] = (None if o is None or d is None else d - o)
                case["alarms"][det][rep]["non_inferior_6_snapshots"] = (
                    (o is None) or (d is not None and d - o <= 6))
        result["cases"][f"{name}:B{bearing}"] = case
        print(name, bearing, {det: {rep: a["index"] for rep, a in v.items()} for det, v in case["alarms"].items()})
    (out / "h2-ims.json").write_text(json.dumps(result, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["paderborn", "ims"])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = p.parse_args()
    {"paderborn": paderborn, "ims": ims}[a.stage](a.out, a.workers)


if __name__ == "__main__":
    main()
