#!/usr/bin/env python3
"""Single-thread encode/decode cost per codec (PROTOCOL.md "Costs"): 12 held-out blocks per dataset,
3 repetitions each, lossless codecs and lossy codecs at their dev operating points."""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

for var in ("OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[var] = "1"

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import bench  # noqa: E402
import h2  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    op = json.loads((a.out / "operating.json").read_text())
    report = {"provenance": bench.provenance(), "datasets": {}}
    for dataset in ("paderborn", "ims", "cx"):
        blocks = list(itertools.islice(bench.loader(dataset, "heldout"), 0, None, 7))[:12]
        res = {}
        for name, (enc, dec) in bench.LOSSLESS.items():
            te = td = 0.0
            ok = True
            for blk in blocks:
                for _ in range(3):
                    try:
                        t = time.perf_counter(); b = enc(blk["x"]); te += time.perf_counter() - t
                    except ValueError:
                        ok = False
                        break
                    t = time.perf_counter(); dec(b); td += time.perf_counter() - t
            n = sum(len(b_["x"]) for b_ in blocks) * 3
            res[name] = {"encode_msamples_s": n / te / 1e6, "decode_msamples_s": n / td / 1e6} if ok else "unsupported"
        for label, name, param, eps in h2.codecs_at_operating(op, dataset):
            te = td = 0.0
            for blk in blocks:
                for _ in range(3):
                    t = time.perf_counter(); _, decode = bench.encode_lossy(name, param, blk["x"], eps, blk); te += time.perf_counter() - t
                    t = time.perf_counter(); decode(); td += time.perf_counter() - t
            n = sum(len(b_["x"]) for b_ in blocks) * 3
            res[label] = {"encode_msamples_s": n / te / 1e6, "decode_msamples_s": n / td / 1e6}
        report["datasets"][dataset] = {"blocks": len(blocks), "samples_per_block": int(len(blocks[0]["x"])), "codecs": res}
        print(dataset, {k: (round(v["encode_msamples_s"], 1), round(v["decode_msamples_s"], 1)) if isinstance(v, dict) else v
                        for k, v in res.items()})
    (a.out / "costs.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
