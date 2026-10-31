#!/usr/bin/env python3
"""X1, descriptive only (PROTOCOL.md): reference = the Aug 2019 execution of the same machine and
operation with the nearest lower file index. The index is an "example no.", not a verified time
order, so this does not decide anything; it checks whether closer-in-time executions (if the
index is roughly chronological) are any more alike than executions from the previous period.

  .venv-compress/bin/python -I evaluation/compression/reference/within_period.py \
      data/bosch/repo/data evaluation/compression/reference/results
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feasibility as f  # noqa: E402


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    fs = [x for x in f.files(root) if x["period"] == "Aug_2019"]
    groups = {}
    for x in fs:
        groups.setdefault((x["machine"], x["op"]), []).append(x)
    total_x = saved = 0
    rho_res, bounds = [], []
    pairs = 0
    for key, g in sorted(groups.items()):
        g.sort(key=lambda x: x["index"])
        prev = None
        for cur in g:
            x = f.read(cur["path"])
            cx = [f.cost(x[:, a]) for a in range(3)]
            total_x += sum(cx)
            if prev is not None:
                r = f.read(prev["path"])
                for a in range(3):
                    e, _ = f.evaluate(x[:, a], r[:, a])
                    saved += max(0, cx[a] - e["cost_d"])
                    rho_res.append(e["rho_res_best"])
                    bounds.append(e["bound_bits"])
                pairs += 1
            prev = cur
    q = lambda v: {p: float(np.percentile(v, p)) for p in (10, 50, 90)}
    res = {"executions": len(fs), "pairs": pairs, "upper_bound_saving": saved / total_x,
           "rho_res_best": q(rho_res), "bound_bits": q(bounds)}
    json.dump(res, open(out / "within_period.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
