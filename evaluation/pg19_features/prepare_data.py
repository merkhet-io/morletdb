#!/usr/bin/env python3
"""Write a B3 subset as plain .npy files, so the PostgreSQL 17/19 comparison (same_session.py) runs without scipy
and the Paderborn .mat files, for example on a small edge machine.

  python evaluation/pg19_features/prepare_data.py --out DIR [--every 20] [--offset 0]

Takes every `every`-th of the 2,480 Paderborn acquisitions of B3 (evaluation/tsdb_b3/workload.py), all channels,
with B3's units and storage hints, and writes DIR/<n>.npy (little-endian float64) and DIR/manifest.json.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tsdb_b3"))
import workload as wl  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=int, default=20)
    ap.add_argument("--offset", type=int, default=0)
    a = ap.parse_args()
    files = sorted(wl.DATA.glob("*/*.mat"))
    if len(files) != 2480:
        raise SystemExit(f"expected 2,480 Paderborn files in {wl.DATA}, found {len(files)}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    recs, digest, n = [], hashlib.sha256(), 0
    for path in files[a.offset::a.every]:
        data = wl.load(path)
        for ch in sorted(data):
            x = np.ascontiguousarray(data[ch], dtype="<f8")
            g = next(g for g, s in wl.GROUPS.items() if ch in s["channels"])
            name = f"{len(recs):05d}.npy"
            np.save(out / name, x)
            recs.append({"recording_id": f"{path.stem}/{ch}", "file": name, "rate": wl.GROUPS[g]["rate"],
                         "channel": ch, "unit": wl.UNITS[ch], "quantum": wl.QUANTUM.get(ch, 1.0), "samples": len(x)})
            digest.update(x.tobytes())
            n += len(x)
    manifest = {"source": "B3 Paderborn subset", "every": a.every, "offset": a.offset, "recordings": recs,
                "samples": n, "samples_sha256": digest.hexdigest()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{len(recs)} recordings, {n:,} samples, sha256 {digest.hexdigest()[:12]}")


if __name__ == "__main__":
    main()
