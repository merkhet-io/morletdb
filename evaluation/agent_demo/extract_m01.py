#!/usr/bin/env python3
"""Stage 1 of the agent demo: M01 HDF5 files to plain arrays (needs numpy and h5py, no database).

  python evaluation/agent_demo/extract_m01.py BOSCH_DIR OUT_DIR

BOSCH_DIR holds COMMIT, SHA256SUMS and repo/data/M01. Only machine M01 is read: M02 and M03 are reserved as
confirmation data for a planned study and are never opened. Every file's SHA-256 is checked against SHA256SUMS.
Writes OUT_DIR/<run id>.npy (int32, samples x 3 axes) and OUT_DIR/manifest.json.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import h5py
import numpy as np

NAME = re.compile(r"^(M01)_(\w+_\d{4})_(OP\d\d)_(\d+)\.h5$")


def main():
    base, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    sums = {}
    for line in (base / "SHA256SUMS").read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        if path.startswith("data/M01/"):          # the other machines' lines are not even kept
            sums[path] = digest
    m01 = base / "repo" / "data" / "M01"
    runs, dtypes = [], {}
    for p in sorted(m01.glob("OP*/*/*.h5")):
        rel = p.relative_to(base / "repo").as_posix()
        assert rel.startswith("data/M01/"), rel
        m = NAME.match(p.name)
        label = p.parent.name
        if not m or label not in ("good", "bad"):
            raise SystemExit(f"unexpected file {rel}")
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if sums.get(rel) != digest:
            raise SystemExit(f"{rel}: SHA-256 {digest} does not match SHA256SUMS")
        with h5py.File(p, "r") as f:
            a = np.asarray(f["vibration_data"][()])
        # Some files store float64, some int64; all values are integer counts (checked), kept exactly as int32.
        if (a.ndim != 2 or a.shape[1] != 3 or a.dtype.kind not in "if" or not np.isfinite(a).all()
                or np.abs(a).max() >= 2 ** 31 or not np.array_equal(a, np.round(a))):
            raise SystemExit(f"{rel}: unexpected shape {a.shape}, dtype {a.dtype} or non-integer values")
        dtypes[str(a.dtype)] = dtypes.get(str(a.dtype), 0) + 1
        run = f"{p.stem}-{digest[:6]}"
        np.save(out / f"{run}.npy", a.astype(np.int32))
        _, period, op, index = m.groups()
        runs.append({"run": run, "file": rel, "sha256": digest, "machine": "M01", "operation": op,
                     "period": period, "index": int(index), "label": label, "samples": int(a.shape[0])})
    commit = (base / "COMMIT").read_text().strip()
    (out / "manifest.json").write_text(json.dumps({"dataset_commit": commit, "hdf5_dtypes": dtypes, "runs": runs}, indent=1))
    print(f"{len(runs)} M01 runs ({sum(r['label'] == 'bad' for r in runs)} bad), "
          f"{sum(r['samples'] for r in runs)} samples per axis, dtypes {dtypes}, dataset commit {commit}")


if __name__ == "__main__":
    main()
