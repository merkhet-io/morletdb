#!/usr/bin/env python3
"""P2 step 1: dump the first 1,024 held-out chunks per dataset, as compression/extension/dump_chunks.py does.

Writes OUT/<name>.npz with `floats` (float64, what a user appends), `ints` (int64 counts) and `quantum`.

  .venv-compress/bin/python -I evaluation/plain_postgres/p2_dump.py OUT
"""
import sys
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(EVAL / "compression"), str(EVAL / "decision")]
import datasets as ds  # noqa: E402
import paderborn_mat as pm  # noqa: E402

CH, N_CHUNKS = 4096, 1024


def first_chunks(items):
    fl, it, q = [], [], None
    for floats, quantum, ints in items:
        q = quantum
        for i in range(0, len(floats), CH):
            fl.append(np.asarray(floats[i:i + CH], float))
            it.append(np.asarray(ints[i:i + CH], np.int64))
            if len(fl) == N_CHUNKS:
                return fl, it, q
    return fl, it, q


def pad():
    root = ds.DATA / "paderborn/zenodo-15845309/extracted"
    for blk in ds.paderborn("heldout"):
        _, _, _, vib, _ = pm.load_file(root / blk["unit"] / f"{blk['id']}.mat")
        yield np.asarray(vib, float)[:len(blk["x"])], blk["step"], blk["x"]


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    sets = {"cx": ((b["x"].astype(float), 1.0, b["x"]) for b in ds.cx("heldout")),
            "ims": ((b["x"] / 1000.0, 0.001, b["x"]) for b in ds.ims("heldout")),
            "paderborn-counts": ((b["x"].astype(float), 1.0, b["x"]) for b in ds.paderborn("heldout")),
            "paderborn-physical": pad()}
    for name, items in sets.items():
        fl, it, q = first_chunks(items)
        np.savez(out / f"{name}.npz", floats=np.concatenate(fl), ints=np.concatenate(it),
                 lengths=np.array([len(c) for c in fl]), quantum=q)
        print(name, len(fl), "chunks", sum(len(c) for c in fl), "samples", flush=True)


if __name__ == "__main__":
    main()
