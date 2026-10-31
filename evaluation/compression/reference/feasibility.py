#!/usr/bin/env python3
"""X1 feasibility gate (PROTOCOL.md): upper bound on the storage saving from coding a Bosch
CNC execution against an earlier execution of the same machine and operation.

  .venv-compress/bin/python -I evaluation/compression/reference/feasibility.py \
      data/bosch/repo/data evaluation/compression/reference/results

Writes feasibility.json (per execution and axis, plus aggregates) and model_check.npz (the
x and d arrays of 60 random eligible executions, for model_check.py).
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
from scipy.signal import correlate

PERIODS = ["Feb_2019", "Aug_2019", "Feb_2020", "Aug_2020", "Feb_2021", "Aug_2021"]
DEVELOPMENT = ("Feb_2019", "Aug_2019")
MAX_SHIFT = 100
CHUNK, PARTS = 4096, 16
NAME = re.compile(r"(M0\d)_(\w{3}_\d{4})_(OP\d\d)_(\d+)\.h5$")


def files(root):
    out = []
    for p in sorted(Path(root).glob("M0*/OP*/*/*.h5")):
        m = NAME.search(p.name)
        out.append({"path": p, "machine": m[1], "period": m[2], "op": m[3], "index": int(m[4]),
                    "label": p.parent.name})
    return out


def read(p):
    import h5py  # only the dump side needs it; model_check.py imports the cost model without it

    with h5py.File(p, "r") as h:
        x = h["vibration_data"][:]
    assert x.ndim == 2 and x.shape[1] == 3 and np.all(np.isfinite(x)) and np.all(x == np.round(x)), p
    return x.astype(np.int64)


RICE = np.arange(0, 33)


def chunks_cost(X):
    """The codec's integer cost model for equal-length chunks (rows of X): per chunk the best
    fixed-predictor order 0-4 (k warm-up samples at 64 bits), 16 partitions each with its best
    Rice parameter (+6 bits), and 32 header bits."""
    m, n = X.shape
    best = np.full(m, np.iinfo(np.int64).max)
    for k in range(min(5, n)):
        r = np.diff(X, n=k, axis=1) if k else X
        z = np.where(r >= 0, 2 * r, -2 * r - 1)
        bounds = np.unique(np.linspace(0, z.shape[1], PARTS + 1).astype(int)[:-1])
        sizes = np.diff(np.append(bounds, z.shape[1]))
        shifted = z[None, :, :] >> RICE[:, None, None]                      # (p, m, L)
        per_part = np.add.reduceat(shifted, bounds, axis=2) + sizes[None, None, :] * (RICE[:, None, None] + 1)
        bits = per_part.min(axis=0).sum(axis=1) + 6 * len(bounds) + 64 * k
        best = np.minimum(best, bits)
    return best + 32


def cost(x):
    m = len(x) // CHUNK
    total = int(chunks_cost(x[:m * CHUNK].reshape(m, CHUNK)).sum()) if m else 0
    if len(x) > m * CHUNK:
        total += int(chunks_cost(x[m * CHUNK:][None, :])[0])
    return total


def codec_order(x):
    return int(np.argmin([np.abs(np.diff(x[:65536], n=k) if k else x[:65536]).sum() for k in range(5)]))


def xcorr_best(a, b, max_shift):
    """Correlation of a[t] with b[t + s], |s| <= max_shift, via FFT. Means and spreads come from
    the whole series; with shifts of at most 100 samples on series of 10^4-10^5 samples this only
    picks the shift, and the coded saving is evaluated exactly afterwards."""
    a = a.astype(float) - a.mean()
    b = b.astype(float) - b.mean()
    sa, sb = a.std(), b.std()
    if sa == 0 or sb == 0:
        return 0.0, 0
    full = correlate(b, a, mode="full", method="fft")          # index (len(a) - 1) + s is lag s
    best = (-2.0, 0)
    for s in range(-max_shift, max_shift + 1):
        lo, hi = max(0, -s), min(len(a), len(b) - s)
        if hi - lo < 1000:
            continue
        rho = float(full[len(a) - 1 + s] / ((hi - lo) * sa * sb))
        if rho > best[0]:
            best = (rho, s)
    return best


def residual(x, r, s):
    """d = x - round(a * r[t + s]) on the overlap (least-squares a), x elsewhere; and a."""
    lo, hi = max(0, -s), min(len(x), len(r) - s)
    d = x.copy()
    if hi - lo < 1:
        return d, 0.0
    u, v = x[lo:hi].astype(float), r[lo + s:hi + s].astype(float)
    a = float((u * v).sum() / max((v * v).sum(), 1e-12))
    d[lo:hi] = x[lo:hi] - np.round(a * v).astype(np.int64)
    return d, a


def corr0(a, b):
    n = min(len(a), len(b))
    u, v = a[:n].astype(float), b[:n].astype(float)
    if u.std() == 0 or v.std() == 0:
        return 0.0
    return float(np.corrcoef(u, v)[0, 1])


def evaluate(x, r):
    """Best coded saving over shifts {0, best raw, best residual} for one axis pair."""
    k = codec_order(x)
    ex, er = (np.diff(x, n=k), np.diff(r, n=k)) if k else (x, r)
    raw_best, s_raw = xcorr_best(x, r, MAX_SHIFT)
    res_best, s_res = xcorr_best(ex, er, MAX_SHIFT)
    out = {"order": k, "rho_raw_0": corr0(x, r), "rho_raw_best": raw_best, "shift_raw": s_raw,
           "rho_res_0": corr0(ex, er), "rho_res_best": res_best, "shift_res": s_res,
           "bound_bits": 0.5 * np.log2(1 / max(1 - res_best ** 2, 1e-12)) if res_best > 0 else 0.0}
    best = None
    for s in sorted({0, s_raw, s_res}):
        d, a = residual(x, r, s)
        c = cost(d)
        if best is None or c < best[0]:
            best = (c, s, a, d)
    out.update({"cost_d": best[0], "shift": best[1], "a": best[2]})
    return out, best[3]


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    fs = [f for f in files(root) if f["period"] in DEVELOPMENT]
    by_key = {}
    for f in fs:
        by_key.setdefault((f["machine"], f["op"], f["period"]), []).append(f)
    rng = np.random.default_rng(7)
    rows, check_pool = [], []
    for f in fs:
        x = read(f["path"])
        row = {k: f[k] for k in ("machine", "op", "period", "index", "label")}
        row["n"] = len(x)
        row["cost_x"] = [cost(x[:, a]) for a in range(3)]
        row["axes"] = None
        if f["period"] == "Aug_2019":
            cands = by_key.get((f["machine"], f["op"], "Feb_2019"), [])
            row["candidates"] = len(cands)
            best_axes, best_d = None, None
            for c in cands:
                r = read(c["path"])
                axes, ds = [], []
                for a in range(3):
                    e, d = evaluate(x[:, a], r[:, a])
                    e["reference"] = c["path"].name
                    axes.append(e)
                    ds.append(d)
                total = sum(e["cost_d"] for e in axes)
                if best_axes is None or total < sum(e["cost_d"] for e in best_axes):
                    best_axes, best_d = axes, ds
            row["axes"] = best_axes
            if best_axes is not None:
                check_pool.append((f["path"].name, x, np.stack(best_d, 1)))
        rows.append(row)
        print(f["path"].name, row["n"], row.get("candidates", "-"), flush=True)

    total_x = sum(sum(r["cost_x"]) for r in rows)
    saved = sum(max(0, cx - e["cost_d"]) for r in rows if r["axes"] for cx, e in zip(r["cost_x"], r["axes"]))
    eligible = [r for r in rows if r["axes"]]
    per_exec = [sum(max(0, cx - e["cost_d"]) for cx, e in zip(r["cost_x"], r["axes"])) / sum(r["cost_x"]) for r in eligible]
    flat = [e for r in eligible for e in r["axes"]]
    q = lambda v: {p: float(np.percentile(v, p)) for p in (10, 50, 90)}
    agg = {"executions": len(rows), "eligible": len(eligible),
           "aggregate_upper_bound_saving": saved / total_x,
           "eligible_only_upper_bound_saving": sum(max(0, cx - e["cost_d"]) for r in eligible for cx, e in zip(r["cost_x"], r["axes"]))
           / max(1, sum(sum(r["cost_x"]) for r in eligible)),
           "share_executions_saving_ge_25pct": float(np.mean([p >= 0.25 for p in per_exec])) if per_exec else 0.0,
           "codec_model_bits_per_sample": total_x / sum(3 * r["n"] for r in rows),
           "rho_raw_0": q([e["rho_raw_0"] for e in flat]), "rho_raw_best": q([e["rho_raw_best"] for e in flat]),
           "rho_res_0": q([e["rho_res_0"] for e in flat]), "rho_res_best": q([e["rho_res_best"] for e in flat]),
           "bound_bits": q([e["bound_bits"] for e in flat]),
           "gate": "PASS (>= 25%)" if saved / total_x >= 0.25 else "PAUSE (< 25%)"}
    pick = rng.choice(len(check_pool), min(60, len(check_pool)), replace=False)
    np.savez_compressed(out / "model_check.npz", names=np.array([check_pool[i][0] for i in pick]),
                        **{f"x{j}": check_pool[i][1] for j, i in enumerate(pick)},
                        **{f"d{j}": check_pool[i][2] for j, i in enumerate(pick)})
    json.dump({"aggregate": agg, "rows": rows}, open(out / "feasibility.json", "w"), indent=1, default=float)
    print(json.dumps(agg, indent=1))


if __name__ == "__main__":
    main()
