#!/usr/bin/env python3
"""B5 development figures (PROTOCOL.md section 2, gates 7 and 8): Grizzbit 1 against Grizzbit 2 on the development
inputs only, never on a benchmark input.

  cargo build --release -p morlet-core --example rawcodec_bench
  .venv-b4/bin/python -I evaluation/storage_b5/dev_figures.py evaluation/storage_b5/results/dev/dev_storage.json

Inputs (gate 7): B4's four development Elf series (seed 20261104: Blockchain-tr, POI-lat, Stocks-USA,
electric_vehicle_charging), parsed as B4's load_elf does; XJTU-SY Bearing1_1 (35 Hz / 12 kN) horizontal, files
1-123; FEMTO Bearing1_1 horizontal, files 1-1638. The names are fixed here: B4's dev_names() is not called, because
it hashes every Elf file, benchmark inputs included.

Per input: 4,096-sample chunks, quantum 1 (the codec detects grids itself), as B4's M-auto. Both arms run through
the codec bench example, which decodes every chunk and compares it bit for bit (by to_bits) before reporting.
Reported: bits per sample (frame and checksum included), chunk counts per kind and version, the number of chunks
stored as version 4, and G2/G1. Speeds are reported as measured, without B5's start conditions (development only).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("MORLET_DATA", ROOT / "data"))   # a checkout's data/ directory
BENCH = ROOT / "target/release/examples/rawcodec_bench"
CHUNK = 4096
ELF_DEV = ["Blockchain-tr", "POI-lat", "Stocks-USA", "electric_vehicle_charging"]


def load_elf(name):
    # As B4's load_elf: every line that parses as a number, in file order; other lines are skipped.
    vals = []
    for line in (DATA / "b4-elf" / f"{name}.csv").read_text().splitlines():
        try:
            vals.append(float(line.strip()))
        except ValueError:
            pass
    return np.array(vals, dtype="<f8")


def csv_column(paths, col):
    # One column of each CSV file, in file order; lines whose column does not parse (headers) are skipped.
    vals = []
    for p in paths:
        for line in p.read_text().splitlines():
            parts = line.split(",")
            if len(parts) > col:
                try:
                    vals.append(float(parts[col].strip()))
                except ValueError:
                    pass
    return np.array(vals, dtype="<f8")


def inputs():
    for name in ELF_DEV:
        yield name, "elf-dev", load_elf(name)
    xjtu = DATA / "xjtu-sy/extracted/XJTU-SY_Bearing_Datasets/35Hz12kN/Bearing1_1"
    yield "XJTU-SY Bearing1_1 horizontal", "wave-dev", csv_column([xjtu / f"{i}.csv" for i in range(1, 124)], 0)
    femto = DATA / "femto/extracted/Learning_set/Bearing1_1"
    yield "FEMTO Bearing1_1 horizontal", "wave-dev", csv_column(
        [femto / f"acc_{i:05d}.csv" for i in range(1, 1639)], 4)


def bench(path, *flags):
    out = subprocess.run([str(BENCH), *flags, "--per-chunk", str(path)], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def main(out_path):
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, kind, x in inputs():
            path = Path(tmp) / "chunks.bin"
            with open(path, "wb") as f:
                for at in range(0, x.size, CHUNK):
                    ch = x[at:at + CHUNK]
                    f.write(np.uint32(ch.size).tobytes() + np.float64(1.0).tobytes() + ch.tobytes())
            g1, g2 = bench(path), bench(path, "--grizzbit2")
            c1, c2 = g1["chunks"], g2["chunks"]
            assert len(c1) == len(c2)
            # Keep the smaller: a chunk differs only as a strictly smaller version-4 chunk.
            changed = [(a, b) for a, b in zip(c1, c2) if a != b]
            assert all(b[0] == 4 and b[2] < a[2] for a, b in changed), name
            by_g1_kind = {}
            for a, b in changed:
                k = f"v{a[0]} {a[1]}"
                by_g1_kind[k] = by_g1_kind.get(k, 0) + 1

            def kinds(cs):
                d = {}
                for v, k, _ in cs:
                    d[f"v{v} {k}"] = d.get(f"v{v} {k}", 0) + 1
                return d
            row = {"input": name, "set": kind, "samples": int(x.size), "chunks": len(c1),
                   "bits_per_sample": {"G1": g1["bits_per_sample"], "G2": g2["bits_per_sample"]},
                   "G2_over_G1": round(g2["bytes"] / g1["bytes"], 4),
                   "bytes": {"G1": g1["bytes"], "G2": g2["bytes"]},
                   "chunk_kinds": {"G1": kinds(c1), "G2": kinds(c2)},
                   "chunks_changed": len(changed), "changed_by_G1_kind": by_g1_kind, "exact": True,
                   "msamples_s_uncontrolled": {"G1_encode": g1["encode_msamples_s"],
                                               "G1_decode": g1["decode_msamples_s"],
                                               "G2_encode": g2["encode_msamples_s"],
                                               "G2_decode": g2["decode_msamples_s"]}}
            print(json.dumps(row), file=sys.stderr)
            rows.append(row)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"study": "B5 development figures", "chunk": CHUNK, "quantum": 1.0,
                                          "pco": "1.0.4", "level": 8, "inputs": rows}, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
