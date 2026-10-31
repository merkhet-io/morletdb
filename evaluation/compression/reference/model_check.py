#!/usr/bin/env python3
"""X1: check the cost model against the real codec on the 60 executions in model_check.npz.

Encodes x and d per axis with morlet_encode_chunk (quantum 1, 4096-sample chunks; any database
with pg_morlet, here morlet_speed_after) and compares
the real saving, cost(x) - cost(d), with the model's (PROTOCOL.md: within 10% of cost(x)).

  .venv-seismic/bin/python -I evaluation/compression/reference/model_check.py \
      evaluation/compression/reference/results
"""
import json
import sys
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))


def model_cost(x):
    import feasibility  # numpy and scipy only; h5py is imported lazily by read()
    return feasibility.cost(x)


def real_cost(conn, x):
    bits = 0
    for i in range(0, len(x), 4096):
        bits += 8 * conn.execute("SELECT octet_length(morlet_encode_chunk(%s::float8[], 1::float8))",
                                 (x[i:i + 4096].astype(float).tolist(),)).fetchone()[0]
    return bits


def main():
    out = Path(sys.argv[1])
    z = np.load(out / "model_check.npz")
    rows = []
    with psycopg.connect("host=localhost port=28817 dbname=morlet_speed_after", autocommit=True) as c:
        for j, name in enumerate(z["names"]):
            x, d = z[f"x{j}"], z[f"d{j}"]
            for a in range(3):
                mx, md = model_cost(x[:, a]), model_cost(d[:, a])
                rx, rd = real_cost(c, x[:, a]), real_cost(c, d[:, a])
                rows.append({"file": str(name), "axis": a, "model_x": mx, "model_d": md, "real_x": rx, "real_d": rd,
                             "saving_gap_of_x": abs((mx - md) / mx - (rx - rd) / rx)})
    gaps = [r["saving_gap_of_x"] for r in rows]
    res = {"pairs": len(rows), "max_gap": max(gaps), "median_gap": float(np.median(gaps)),
           "model_over_real_cost_x": float(np.median([r["model_x"] / r["real_x"] for r in rows])),
           "aggregate_saving_model": 1 - sum(r["model_d"] for r in rows) / sum(r["model_x"] for r in rows),
           "aggregate_saving_real": 1 - sum(r["real_d"] for r in rows) / sum(r["real_x"] for r in rows),
           "within_10pct": all(g <= 0.10 for g in gaps)}
    json.dump({"summary": res, "rows": rows}, open(out / "model_check.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
