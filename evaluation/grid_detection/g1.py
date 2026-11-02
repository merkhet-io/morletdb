#!/usr/bin/env python3
"""G1 (PROTOCOL.md): bits/sample on B1's six inputs with the declared quanta (D), with quantum 1 and this
encoder (A), and with quantum 1 and the previous encoder (P). The bench binaries decode every chunk and
assert a bitwise round trip.

  python -I evaluation/grid_detection/g1.py data/b1-runs/inputs data/b1-runs/p2 BENCH_NEW BENCH_OLD OUT_DIR
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

INPUTS = {"cx": ("p2", "cx"), "ims": ("p2", "ims"), "pad-c": ("p2", "paderborn-counts"),
          "pad-p": ("p2", "paderborn-physical"), "sleep": ("inputs", "sleep"), "ecg": ("inputs", "ecg")}


def load(inputs, p2, name):
    where, stem = INPUTS[name]
    z = np.load((p2 if where == "p2" else inputs) / f"{stem}.npz")
    quanta = np.full(len(z["lengths"]), float(z["quantum"])) if "quantum" in z.files else z["quanta"]
    return z["floats"], z["lengths"], quanta


def bench(binary, x, lengths, quanta, tmp):
    path = Path(tmp) / "g1.bin"
    with open(path, "wb") as f:
        for ch, q in zip(np.split(x, np.cumsum(lengths)[:-1]), quanta):
            f.write(np.uint32(len(ch)).tobytes() + np.float64(q).tobytes() + ch.astype("<f8").tobytes())
    r = json.loads(subprocess.run([str(binary), str(path)], check=True, capture_output=True, text=True).stdout)
    path.unlink()
    return r


def main():
    inputs, p2, new, old, out = (Path(a) for a in sys.argv[1:6])
    rows = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in INPUTS:
            x, lengths, quanta = load(inputs, p2, name)
            ones = np.ones_like(quanta)
            rows[name] = {"D": bench(new, x, lengths, quanta, tmp), "A": bench(new, x, lengths, ones, tmp),
                          "P": bench(old, x, lengths, ones, tmp)}
            r = rows[name]
            r["A_over_D"] = round(r["A"]["bits_per_sample"] / r["D"]["bits_per_sample"], 4)
            r["A_over_P"] = round(r["A"]["bits_per_sample"] / r["P"]["bits_per_sample"], 4)
            r["criterion_recovery"] = r["A_over_D"] <= 1.05
            r["criterion_no_loss"] = r["A_over_P"] <= 1.001
            print(name, {k: r[k]["bits_per_sample"] for k in "DAP"}, r["A_over_D"], r["A_over_P"], flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "g1.json").write_text(json.dumps(rows, indent=1) + "\n")


if __name__ == "__main__":
    main()
