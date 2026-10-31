#!/usr/bin/env python3
"""F1 development gate (PROTOCOL.md): kNN continuation vs persistence and AR on XJTU-SY
log-RMS trajectories, leave-one-bearing-out with nested tuning, plus controls.

  .venv-seismic/bin/python -I evaluation/forecasting/gate.py \
      evaluation/forecasting/results/trajectories.json evaluation/forecasting/results
"""
import json
import sys
from pathlib import Path

import numpy as np

MIN_T = 29                                   # 30 values of history: the longest context
PRIMARY_H = 10
HORIZONS = (1, 10, 30)
AR_P = (1, 2, 3, 5, 8, 12, 16, 20, 29)
KNN = [(L, k) for L in (5, 10, 30) for k in (1, 5, 25)]
RIDGE = 1e-6
SEEDS = range(5)
GATE_RATIO = 0.90
RESAMPLES, SEED = 2000, 7


def origins(n, h):
    return np.arange(MIN_T, n - h)


def contexts(y, ts, L):
    """Offset-corrected contexts: last L values minus the last one."""
    idx = ts[:, None] + np.arange(-L + 1, 1)[None, :]
    return y[idx] - y[ts][:, None]


def gallery(traj, bearings, h, L):
    """Contexts, continuations and (bearing, origin) ids of every origin in `bearings`."""
    C, D, ids = [], [], []
    for b in bearings:
        y = traj[b]
        ts = origins(len(y), h)
        if len(ts) == 0:
            continue
        C.append(contexts(y, ts, L))
        D.append(y[ts + h] - y[ts])
        ids += [(b, int(t)) for t in ts]
    if not C:
        return np.zeros((0, L)), np.zeros(0), []
    return np.vstack(C), np.concatenate(D), ids


def nearest(Q, C, k):
    """Exact k nearest rows of C for each row of Q (Euclidean)."""
    d2 = (Q ** 2).sum(1)[:, None] + (C ** 2).sum(1)[None, :] - 2 * Q @ C.T
    k = min(k, C.shape[0])
    idx = np.argpartition(d2, k - 1, axis=1)[:, :k]
    return idx


def ar_features(y, ts, p):
    dy = np.diff(y)                            # dy[i] = y[i+1] - y[i]
    idx = ts[:, None] - 1 - np.arange(p)[None, :]
    return np.hstack([dy[idx], np.ones((len(ts), 1))])


def ar_fit(traj, bearings, h, p):
    X, Y = [], []
    for b in bearings:
        y = traj[b]
        ts = origins(len(y), h)
        if len(ts):
            X.append(ar_features(y, ts, p))
            Y.append(y[ts + h] - y[ts])
    X, Y = np.vstack(X), np.concatenate(Y)
    return np.linalg.solve(X.T @ X + RIDGE * np.eye(X.shape[1]), X.T @ Y)


def predict(method, cfg, traj, train, q, h, rng=None):
    """Predictions of y[t+h] for every origin of bearing q, trained on `train` only."""
    assert q not in train, "query bearing in training set"
    y = traj[q]
    ts = origins(len(y), h)
    if method == "persistence":
        return y[ts]
    if method == "ar":
        w = ar_fit(traj, train, h, cfg)
        return y[ts] + ar_features(y, ts, cfg) @ w
    L, k = cfg
    C, D, ids = gallery(traj, train, h, L)
    assert all(b != q for b, _ in ids), "query bearing in gallery"
    Q = contexts(y, ts, L)
    if method == "knn":
        return y[ts] + D[nearest(Q, C, k)].mean(1)
    if method == "random":
        return y[ts] + D[rng.integers(0, len(D), (len(ts), min(k, len(D))))].mean(1)
    if method == "shuffled":
        return y[ts] + D[rng.permutation(len(D))][nearest(Q, C, k)].mean(1)
    raise ValueError(method)


def mae(traj, q, h, pred, mask=None):
    y = traj[q]
    ts = origins(len(y), h)
    err = np.abs(pred - y[ts + h])
    if mask is not None:
        err = err[mask(y, ts)]
    return float(err.mean()) if len(err) else None


def tune(method, configs, traj, train, h):
    """Configuration with the lowest mean per-bearing MAE in inner leave-one-bearing-out."""
    scores = []
    for cfg in configs:
        per = [mae(traj, i, h, predict(method, cfg, traj, [b for b in train if b != i], i, h))
               for i in train if len(origins(len(traj[i]), h))]
        scores.append(np.mean([v for v in per if v is not None]))
    return configs[int(np.argmin(scores))]


def degrading(y, ts):
    return y[ts] - np.median(y[:10]) > np.log(1.5)


def outer(traj, h, pool=None):
    """Leave one bearing out; `pool(q)` gives the training bearings (default: all others)."""
    names = sorted(traj)
    pool = pool or (lambda q: [b for b in names if b != q])
    rows = {}
    for q in names:
        if not len(origins(len(traj[q]), h)):
            continue
        train = [b for b in pool(q) if len(origins(len(traj[b]), h))]
        p = tune("ar", list(AR_P), traj, train, h)
        cfg = tune("knn", KNN, traj, train, h)
        preds = {"persistence": predict("persistence", None, traj, train, q, h),
                 "ar": predict("ar", p, traj, train, q, h),
                 "knn": predict("knn", cfg, traj, train, q, h)}
        for ctl in ("random", "shuffled"):
            preds[ctl] = np.mean([predict(ctl, cfg, traj, train, q, h, np.random.default_rng(s)) for s in SEEDS], 0)
        rows[q] = {"origins": int(len(origins(len(traj[q]), h))), "ar_p": p, "knn_L_k": list(cfg),
                   "mae": {m: mae(traj, q, h, v) for m, v in preds.items()},
                   "mae_degrading": {m: mae(traj, q, h, v, degrading) for m, v in preds.items()}}
        print(h, q, rows[q]["mae"], flush=True)
    return rows


def ratio(rows, a, b, key="mae", rng=None):
    qs = [q for q in rows if rows[q][key][a] is not None and rows[q][key][b] is not None]
    A = np.array([rows[q][key][a] for q in qs])
    B = np.array([rows[q][key][b] for q in qs])
    rng = rng or np.random.default_rng(SEED)
    boots = []
    for _ in range(RESAMPLES):
        i = rng.integers(0, len(qs), len(qs))
        boots.append(A[i].mean() / B[i].mean())
    return {"bearings": len(qs), "ratio": float(A.mean() / B.mean()),
            "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "bearings_better": int((A < B).sum())}


def summarize(rows, key="mae"):
    methods = ("persistence", "ar", "knn", "random", "shuffled")
    means = {m: float(np.mean([r[key][m] for r in rows.values() if r[key][m] is not None])) for m in methods}
    best = min(("persistence", "ar"), key=lambda m: means[m])
    return {"mean_mae": means, "best_baseline": best,
            "knn_vs_best": ratio(rows, "knn", best, key), "knn_vs_random": ratio(rows, "knn", "random", key),
            "knn_vs_shuffled": ratio(rows, "knn", "shuffled", key), "ar_vs_persistence": ratio(rows, "ar", "persistence", key)}


def decide(s):
    c1 = s["knn_vs_best"]["ratio"] <= GATE_RATIO and s["knn_vs_best"]["ci95"][1] < 1.0
    c2 = s["knn_vs_random"]["ci95"][1] < 1.0
    c3 = s["knn_vs_shuffled"]["ci95"][1] < 1.0
    return {"beats_best_baseline_by_10pct": c1, "beats_random": c2, "beats_shuffled": c3,
            "gate": "PASS" if c1 and c2 and c3 else "STOP"}


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    raw = json.load(open(src))
    traj = {b: np.array(v["log_rms_horizontal"]) for b, v in raw.items()}
    cond = {b: v["condition"] for b, v in raw.items()}
    res = {}
    for h in HORIZONS:
        rows = outer(traj, h)
        res[f"H{h}"] = {"rows": rows, "summary": summarize(rows), "degrading": summarize(rows, "mae_degrading")}
    res["decision"] = decide(res[f"H{PRIMARY_H}"]["summary"])
    loco = outer(traj, PRIMARY_H, pool=lambda q: [b for b in traj if cond[b] != cond[q]])
    res["leave_one_condition_out_H10"] = {"rows": loco, "summary": summarize(loco)}
    json.dump(res, open(out / "gate.json", "w"), indent=1)
    for h in HORIZONS:
        s = res[f"H{h}"]["summary"]
        print(f"H={h}", {m: round(v, 4) for m, v in s["mean_mae"].items()}, "knn/best", s["knn_vs_best"])
    print("decision", res["decision"])


if __name__ == "__main__":
    main()
