#!/usr/bin/env python3
"""F2 (PROTOCOL.md): retrieval forecasting in the fast-degradation regime, C-MAPSS FD001.

  .venv-seismic/bin/python -I evaluation/forecasting/f2/f2.py gate \
      data/cmapss/extracted/CMAPSSData/train_FD001.txt evaluation/forecasting/f2/results
  # only if the gate passes:
  .venv-seismic/bin/python -I evaluation/forecasting/f2/f2.py confirm \
      data/cmapss/extracted/CMAPSSData/train_FD001.txt evaluation/forecasting/f2/results
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gate as g  # noqa: E402  (F1's tested functions: origins, predict, mae, ratio, ...)

TARGET_COL, DESCRIPTIVE_COL = 15, 8          # sensor 11 (Ps30) and sensor 4 (T50), 0-based columns
SPLIT_SEED = 2026
SLOPE_WINDOW = 20
FAST_QUANTILE = 75
PRIMARY_H = 10
HORIZONS = (1, 10, 30)
METHODS = ("persistence", "ar", "knn", "random", "shuffled")


def load(path, col):
    a = np.loadtxt(path)
    out = {}
    for u in np.unique(a[:, 0]).astype(int):
        rows = a[a[:, 0] == u]
        assert np.array_equal(rows[:, 1], np.arange(1, len(rows) + 1)), u
        out[f"u{u:03d}"] = rows[:, col].astype(float)
    return out


def split(units):
    ids = sorted(units)
    perm = np.random.default_rng(SPLIT_SEED).permutation(len(ids))
    dev = sorted(ids[i] for i in perm[:len(ids) // 2])
    conf = sorted(ids[i] for i in perm[len(ids) // 2:])
    assert not set(dev) & set(conf) and len(dev) + len(conf) == len(ids)
    return dev, conf


def slopes(y, ts):
    """Least-squares slope of y over the SLOPE_WINDOW values ending at each t (causal)."""
    x = np.arange(SLOPE_WINDOW) - (SLOPE_WINDOW - 1) / 2
    w = y[ts[:, None] + np.arange(-SLOPE_WINDOW + 1, 1)[None, :]]
    return (w - w.mean(1, keepdims=True)) @ x / (x @ x)


def threshold(traj, units):
    s = np.concatenate([slopes(traj[u], g.origins(len(traj[u]), 1)) for u in units])
    return float(np.percentile(s, FAST_QUANTILE))


def fast_mask(tau):
    return lambda y, ts: slopes(y, ts) > tau


def score(traj, q, h, pred, mask):
    return g.mae(traj, q, h, pred, mask)


def tune(method, configs, traj, train, h, mask):
    best, best_cfg = None, None
    for cfg in configs:
        per = [score(traj, i, h, g.predict(method, cfg, traj, [b for b in train if b != i], i, h), mask)
               for i in train if len(g.origins(len(traj[i]), h))]
        per = [v for v in per if v is not None]
        s = np.mean(per)
        if best is None or s < best:
            best, best_cfg = s, cfg
    return best_cfg


def fold(traj, train, q, h, mask, cfgs=None):
    """Predictions for unit q with training units `train`; configs tuned unless given."""
    assert q not in train
    p = cfgs["ar"] if cfgs else tune("ar", list(g.AR_P), traj, train, h, mask)
    k = cfgs["knn"] if cfgs else tune("knn", g.KNN, traj, train, h, mask)
    preds = {"persistence": g.predict("persistence", None, traj, train, q, h),
             "ar": g.predict("ar", p, traj, train, q, h), "knn": g.predict("knn", k, traj, train, q, h)}
    for ctl in ("random", "shuffled"):
        preds[ctl] = np.mean([g.predict(ctl, k, traj, train, q, h, np.random.default_rng(s)) for s in g.SEEDS], 0)
    return {"origins": int(len(g.origins(len(traj[q]), h))),
            "fast_origins": int(mask(traj[q], g.origins(len(traj[q]), h)).sum()),
            "ar_p": p, "knn_L_k": list(k),
            "mae_fast": {m: score(traj, q, h, v, mask) for m, v in preds.items()},
            "mae_all": {m: score(traj, q, h, v, None) for m, v in preds.items()}}


def summarize(rows, key):
    rows = {q: r for q, r in rows.items() if all(v is not None for v in r[key].values())}
    means = {m: float(np.mean([r[key][m] for r in rows.values()])) for m in METHODS}
    best = min(("persistence", "ar"), key=lambda m: means[m])
    return {"units": len(rows), "mean_mae": means, "best_baseline": best,
            "knn_vs_best": g.ratio(rows, "knn", best, key), "knn_vs_random": g.ratio(rows, "knn", "random", key),
            "knn_vs_shuffled": g.ratio(rows, "knn", "shuffled", key)}


def run_gate(traj, dev, tau, h):
    sub = {u: traj[u] for u in dev}          # confirmation units are not even passed in
    mask = fast_mask(tau)
    rows = {}
    for q in dev:
        if not len(g.origins(len(sub[q]), h)):
            continue
        rows[q] = fold(sub, [u for u in dev if u != q], q, h, mask)
        print(h, q, {m: round(v, 4) if v is not None else None for m, v in rows[q]["mae_fast"].items()}, flush=True)
    return rows


def main():
    stage, path, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    traj = load(path, TARGET_COL)
    dev, conf = split(traj)
    tau = threshold(traj, dev)
    if stage == "gate":
        res = {"split": {"development": dev, "confirmation": conf}, "tau": tau}
        for h in HORIZONS:
            rows = run_gate(traj, dev, tau, h)
            res[f"H{h}"] = {"rows": rows, "fast": summarize(rows, "mae_fast"), "all": summarize(rows, "mae_all")}
        s = res[f"H{PRIMARY_H}"]["fast"]
        res["decision"] = g.decide(s)
        t4 = load(path, DESCRIPTIVE_COL)
        tau4 = threshold(t4, dev)
        rows4 = run_gate(t4, dev, tau4, PRIMARY_H)
        res["sensor4_H10"] = {"tau": tau4, "fast": summarize(rows4, "mae_fast"), "all": summarize(rows4, "mae_all")}
        json.dump(res, open(out / "gate.json", "w"), indent=1)
        for h in HORIZONS:
            for k in ("fast", "all"):
                s = res[f"H{h}"][k]
                print(f"H={h} {k}", {m: round(v, 4) for m, v in s["mean_mae"].items()}, "knn/best", s["best_baseline"],
                      round(s["knn_vs_best"]["ratio"], 3), [round(x, 3) for x in s["knn_vs_best"]["ci95"]])
        print("decision", res["decision"])
    elif stage == "confirm":
        gate_res = json.load(open(out / "gate.json"))
        assert gate_res["decision"]["gate"] == "PASS", "confirmation runs only after a passing gate"
        mask = fast_mask(tau)
        cfgs = {"ar": tune("ar", list(g.AR_P), traj, dev, PRIMARY_H, mask),
                "knn": tune("knn", g.KNN, traj, dev, PRIMARY_H, mask)}
        rows = {q: fold(traj, dev, q, PRIMARY_H, mask, cfgs) for q in conf if len(g.origins(len(traj[q]), PRIMARY_H))}
        s = summarize(rows, "mae_fast")
        res = {"configs": {"ar": cfgs["ar"], "knn": list(cfgs["knn"])}, "rows": rows, "fast": s,
               "all": summarize(rows, "mae_all"), "decision": g.decide(s)}
        json.dump(res, open(out / "confirm.json", "w"), indent=1)
        print(json.dumps({k: res[k] for k in ("configs", "decision")}), s["mean_mae"])


if __name__ == "__main__":
    main()
