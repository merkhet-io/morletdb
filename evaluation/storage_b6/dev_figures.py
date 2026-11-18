#!/usr/bin/env python3
"""B6 development figures (PROTOCOL.md section 2, gates 7 and 8): Grizzbit 1 against Grizzbit 2.1 on the
development inputs only, never on a benchmark input.

  cargo build --release -p morlet-core --example rawcodec_bench
  python3 -I evaluation/storage_b6/dev_figures.py T1_WORK evaluation/storage_b6/results/dev/dev_storage.json

Inputs (gate 7), and nothing else:
- T1's development inputs, as T1's prepare.py wrote them (evaluation/codec_t261/prepare.py, commit 2a89c40, into
  T1_WORK): the B3 development acquisitions K001, KA01, KA09, KB24 and KI08 (vibration_1, phase_current_1,
  phase_current_2, force), the original float64 (NAME.f64), and FEMTO Bearing1_1 horizontal (files 1-1638);
- B4's four development Elf series (Blockchain-tr, POI-lat, Stocks-USA, electric_vehicle_charging), parsed as B4's
  load_elf does. The names are fixed here: B4's dev_names() is not called, because it hashes every Elf file,
  benchmark inputs included;
- XJTU-SY Bearing1_1 (35 Hz / 12 kN) horizontal, files 1-123 (B5's development input).

Per input: 4,096-sample chunks and quantum 1 (the codec detects grids itself), as B6's arms. Both arms run through the
codec bench example, which decodes every chunk and compares it bit for bit (by to_bits) before reporting.
Reported: bits per sample (frame and checksum included), chunk counts per kind and version, version-5 paths, the
number of chunks whose bytes differ between G1 and G21, and G21/G1. Speeds are not recorded: the build gates run
without B6's start conditions, and speed is measured only in B6's run (section 4).
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
T1_ACQ = ["N09_M07_F10_K001_19", "N15_M01_F10_KA01_19", "N15_M07_F04_KA09_19", "N15_M07_F10_KB24_19",
          "N09_M07_F10_KI08_19"]
T1_CHANNELS = ["vibration_1", "phase_current_1", "phase_current_2", "force"]
MODES = {0: "none", 1: "per sample", 2: "table", 3: "exceptions only"}


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


def inputs(t1_work):
    for acq in T1_ACQ:
        for ch in T1_CHANNELS:
            yield f"{acq}.{ch}", "t1-dev", np.fromfile(t1_work / f"{acq}.{ch}.f64", dtype="<f8")
    femto = DATA / "femto/extracted/Learning_set/Bearing1_1"
    yield "FEMTO Bearing1_1 horizontal", "t1-dev", csv_column(
        [femto / f"acc_{i:05d}.csv" for i in range(1, 1639)], 4)
    for name in ELF_DEV:
        yield name, "elf-dev", load_elf(name)
    xjtu = DATA / "xjtu-sy/extracted/XJTU-SY_Bearing_Datasets/35Hz12kN/Bearing1_1"
    yield "XJTU-SY Bearing1_1 horizontal", "b5-dev", csv_column([xjtu / f"{i}.csv" for i in range(1, 124)], 0)


def bench(path, *flags):
    out = subprocess.run([str(BENCH), *flags, "--per-chunk", str(path)], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def main(t1_work, out_path):
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, kind, x in inputs(Path(t1_work)):
            path = Path(tmp) / "chunks.bin"
            with open(path, "wb") as f:
                for at in range(0, x.size, CHUNK):
                    ch = x[at:at + CHUNK]
                    f.write(np.uint32(ch.size).tobytes() + np.float64(1.0).tobytes() + ch.tobytes())
            g1, g21 = bench(path), bench(path, "--grizzbit21")
            c1, c21 = g1["chunks"], g21["chunks"]
            assert len(c1) == len(c21)
            # Keep the smaller: a chunk differs only as a strictly smaller version-5 chunk.
            changed = [(a, b) for a, b in zip(c1, c21) if a[:3] != b[:3]]
            assert all(b[0] == 5 and b[2] < a[2] for a, b in changed), name
            assert all(b[0] != 5 for a, b in zip(c1, c21) if a[:3] == b[:3]), name

            def kinds(cs):
                d = {}
                for v, k, *_ in cs:
                    d[f"v{v} {k}"] = d.get(f"v{v} {k}", 0) + 1
                return d
            paths = {}
            for v, _, _, mode, lpc in c21:
                if v == 5:
                    paths[MODES[mode]] = paths.get(MODES[mode], 0) + 1
                    paths["LPC"] = paths.get("LPC", 0) + lpc
            row = {"input": name, "set": kind, "samples": int(x.size), "chunks": len(c1),
                   "bits_per_sample": {"G1": g1["bits_per_sample"], "G21": g21["bits_per_sample"]},
                   "G21_over_G1": round(g21["bytes"] / g1["bytes"], 4),
                   "bytes": {"G1": g1["bytes"], "G21": g21["bytes"]},
                   "chunk_kinds": {"G1": kinds(c1), "G21": kinds(c21)},
                   "version5_paths": paths, "chunks_changed": len(changed), "exact": True}
            print(json.dumps(row), file=sys.stderr)
            rows.append(row)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"study": "B6 development figures", "chunk": CHUNK, "quantum": 1.0,
                                          "inputs": rows}, indent=1) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
