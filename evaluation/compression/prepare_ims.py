#!/usr/bin/env python3
"""Convert IMS ASCII snapshots to integer arrays (exact: values are decimal with a fixed step).

One accelerometer per bearing (set 1: the first channel of each pair, Ch 1/3/5/7), int16
counts of `step` g-units as written in the files, plus snapshot timestamps from file names.
"""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SETS = {"set1": ("1st_test", [0, 2, 4, 6]), "set2": ("2nd_test", [0, 1, 2, 3]), "set3": ("4th_test/txt", [0, 1, 2, 3])}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, (folder, cols) in SETS.items():
        files = sorted((a.runs / folder).iterdir())
        times, data, digest = [], [], hashlib.sha256()
        for f in files:
            raw = f.read_bytes()
            digest.update(raw)
            v = pd.read_csv(f, sep="\t", header=None, engine="c").to_numpy()[:, cols]
            data.append(v)
            times.append(datetime.strptime(f.name, "%Y.%m.%d.%H.%M.%S").timestamp())
        v = np.stack(data)  # files x 20480 x 4
        step = 0.001
        q = np.round(v / step)
        dev = float(np.abs(q * step - v).max())
        ints = q.astype(np.int16)
        np.savez(a.out / f"{name}.npz", x=ints, t=np.array(times), step=step)
        summary[name] = {"folder": folder, "files": len(files), "samples_per_file": int(v.shape[1]),
                         "channels_used": [c + 1 for c in cols], "step": step, "max_grid_deviation": dev,
                         "range_counts": [int(ints.min()), int(ints.max())],
                         "first": files[0].name, "last": files[-1].name, "sha256_of_concatenated_files": digest.hexdigest()}
        print(name, summary[name])
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
