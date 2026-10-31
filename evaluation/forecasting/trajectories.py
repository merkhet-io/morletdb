#!/usr/bin/env python3
"""Log-RMS trajectories of the XJTU-SY run-to-failure bearings (PROTOCOL.md, target definition).

One value per snapshot: ln(RMS) of the mean-removed channel over the whole snapshot
(32,768 samples, 1.28 s at 25.6 kHz). Snapshots are one minute apart. Snapshot files are
ordered by their integer file name. Nothing about failure time is used.

  .venv-seismic/bin/python -I evaluation/forecasting/trajectories.py \
      data/xjtu-sy/extracted/XJTU-SY_Bearing_Datasets evaluation/forecasting/results/trajectories.json
"""
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

CONDITIONS = {"35Hz12kN": 35.0, "37.5Hz11kN": 37.5, "40Hz10kN": 40.0}


def snapshot(path):
    x = pd.read_csv(path).to_numpy(float)
    assert x.shape == (32768, 2) and np.isfinite(x).all(), path
    x = x - x.mean(0)
    return np.log(np.sqrt((x ** 2).mean(0))).tolist(), hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    res = {}
    with ProcessPoolExecutor() as pool:
        for cond in CONDITIONS:
            for d in sorted((root / cond).glob("Bearing*")):
                files = sorted(d.glob("*.csv"), key=lambda p: int(p.stem))
                assert [int(p.stem) for p in files] == list(range(1, len(files) + 1)), d
                vals = list(pool.map(snapshot, files, chunksize=16))
                h = hashlib.sha256("".join(v[1] for v in vals).encode()).hexdigest()
                res[d.name.replace("Bearing", "")] = {
                    "condition": cond, "shaft_hz": CONDITIONS[cond], "cadence_s": 60, "snapshots": len(files),
                    "log_rms_horizontal": [v[0][0] for v in vals], "log_rms_vertical": [v[0][1] for v in vals],
                    "files_sha256": h}
                print(d.name, len(files), flush=True)
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
