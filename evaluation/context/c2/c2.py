#!/usr/bin/env python3
"""C2: same-operation history vs context-free on Bosch CNC anomalies (PROTOCOL.md).

Usage: c2.py DATA_DIR OUT   (compression environment: numpy, scipy, h5py)
DATA_DIR is the repository's data/ folder. Writes OUT/c2.json. Synthetic checks: test_c2.py.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
from scipy import stats

RATE = 2000.0
HISTORY_PERIODS = ("Feb_2019", "Aug_2019", "Feb_2020", "Aug_2020")
EVAL_PERIODS = ("Feb_2021", "Aug_2021")
VALUES = ("duration_s", *(f"{v}_{a}" for a in "xyz" for v in ("std", "kurtosis", "crest")))
MIN_HISTORY = 3
NAME = re.compile(r"^(M\d\d)_(\w+_\d{4})_(OP\d\d)_(\d+)\.h5$")


def axis_values(x):
    """simple-v1 definitions (morlet_core::values): population std, Pearson kurtosis, crest."""
    m = x.mean()
    m2 = np.mean((x - m) ** 2)
    m4 = np.mean((x - m) ** 4)
    rms = np.sqrt(np.mean(x ** 2))
    peak = np.max(np.abs(x))
    return [np.sqrt(m2), m4 / (m2 * m2) if m2 > 0 else 0.0, peak / rms if rms > 0 else 0.0]


def file_values(a):
    out = [len(a) / RATE]
    for i in range(3):
        out += axis_values(a[:, i])
    return dict(zip(VALUES, out))


def load(data_dir: Path):
    import h5py
    rows, rejected = [], []
    for p in sorted(data_dir.rglob("*.h5")):
        m = NAME.match(p.name)
        label = p.parent.name
        if not m or label not in ("good", "bad"):
            rejected.append({"file": str(p), "reason": "unexpected name or folder"})
            continue
        with h5py.File(p, "r") as f:
            keys = list(f.keys())
            a = np.asarray(f[keys[0]][()], float)
        if a.ndim != 2 or a.shape[1] != 3 or len(a) < 2 or not np.isfinite(a).all() or np.abs(a).max() > 1e9:
            rejected.append({"file": p.name, "reason": f"shape {a.shape} or non-finite/out-of-range values"})
            continue
        machine, period, op, _ = m.groups()
        rows.append({"file": p.name, "machine": machine, "period": period, "op": op, "bad": label == "bad",
                     **file_values(a)})
    return rows, rejected


def score(run, history):
    """Max over values of |x - median| / |median| (relative deviation; morlet_compare_to_history)."""
    out = []
    for v in VALUES:
        med = np.median([h[v] for h in history])
        if med != 0:
            out.append(abs(run[v] - med) / abs(med))
    return max(out) if out else None


def auroc(scores, bad):
    s, b = np.asarray(scores, float), np.asarray(bad, bool)
    if b.all() or not b.any():
        return None
    return float(stats.mannwhitneyu(s[b], s[~b]).statistic / (b.sum() * (~b).sum()))


def arms(run, history):
    same_mo = [h for h in history if h["machine"] == run["machine"] and h["op"] == run["op"]]
    same_op = [h for h in history if h["op"] == run["op"]]
    return {"machine_operation": same_mo, "operation": same_op, "context_free": history}


def analyse(rows, resamples=2000, seed=7):
    history = [r for r in rows if r["period"] in HISTORY_PERIODS and not r["bad"]]
    evaluation = [r for r in rows if r["period"] in EVAL_PERIODS]
    scored, excluded = [], []
    for r in evaluation:
        refs = arms(r, history)
        row = {"file": r["file"], "machine": r["machine"], "op": r["op"], "period": r["period"], "bad": r["bad"],
               "history_n": {k: len(v) for k, v in refs.items()}}
        for k, ref in refs.items():
            row[k] = score(r, ref) if len(ref) >= MIN_HISTORY else None
        (scored if row["machine_operation"] is not None else excluded).append(row)
    bad = [x["bad"] for x in scored]
    a_c = auroc([x["machine_operation"] for x in scored], bad)
    a_f = auroc([x["context_free"] for x in scored], bad)
    groups = sorted({(x["machine"], x["op"]) for x in scored})
    rng = np.random.default_rng(seed)
    deltas, dropped = [], 0
    for _ in range(resamples):
        pick = [groups[i] for i in rng.integers(0, len(groups), len(groups))]
        sub = [x for g in pick for x in scored if (x["machine"], x["op"]) == g]
        b = [x["bad"] for x in sub]
        c, f = auroc([x["machine_operation"] for x in sub], b), auroc([x["context_free"] for x in sub], b)
        if c is None or f is None:
            dropped += 1
            continue
        deltas.append(c - f)
    delta = a_c - a_f
    ci = [float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5))]
    decision = "supported" if delta >= 0.05 and ci[0] > 0 else "not supported" if ci[1] < 0.05 else "inconclusive"

    def threshold(arm):
        loo = []
        for h in history:
            ref = [x for x in arms(h, history)[arm] if x is not h]
            if len(ref) >= MIN_HISTORY:
                loo.append(score(h, ref))
        return float(np.percentile(loo, 99)), len(loo)

    usefulness = {}
    for arm in ("machine_operation", "context_free"):
        thr, n = threshold(arm)
        flagged = [x[arm] > thr for x in scored]
        tp = sum(f and x["bad"] for f, x in zip(flagged, scored))
        fp = sum(f and not x["bad"] for f, x in zip(flagged, scored))
        usefulness[arm] = {"threshold": thr, "history_scores": n, "true_positives": tp, "bad": sum(bad),
                           "false_positives": fp, "good": len(scored) - sum(bad)}
    per_value = {}
    for v in VALUES:
        dev = []
        for x in scored:
            ref = arms(next(r for r in evaluation if r["file"] == x["file"]), history)["machine_operation"]
            med = np.median([h[v] for h in ref])
            dev.append(abs(next(r for r in evaluation if r["file"] == x["file"])[v] - med) / abs(med) if med else 0.0)
        per_value[v] = auroc(dev, bad)
    return {
        "history_files": len(history), "evaluation_files": len(evaluation), "scored": len(scored),
        "scored_bad": int(sum(bad)), "excluded": [{"file": x["file"], "bad": x["bad"],
                                                     "history_n": x["history_n"]["machine_operation"]} for x in excluded],
        "auroc_machine_operation": a_c, "auroc_context_free": a_f, "delta": delta, "ci95": ci,
        "bootstrap": {"valid": len(deltas), "dropped_one_class": dropped}, "decision": decision,
        "reported": {
            "auroc_operation_any_machine": auroc([x["operation"] for x in scored], bad),
            "per_machine": {m: {arm: auroc([x[arm] for x in scored if x["machine"] == m],
                                           [x["bad"] for x in scored if x["machine"] == m])
                                for arm in ("machine_operation", "context_free")}
                            for m in sorted({x["machine"] for x in scored})},
            "per_period": {p: {arm: auroc([x[arm] for x in scored if x["period"] == p],
                                          [x["bad"] for x in scored if x["period"] == p])
                               for arm in ("machine_operation", "context_free")}
                           for p in EVAL_PERIODS},
            "per_value_auroc_machine_operation": per_value,
            "usefulness_at_history_threshold": usefulness,
            "history_per_context": {f"{m}/{o}": sum(1 for h in history if h["machine"] == m and h["op"] == o)
                                    for m, o in sorted({(h["machine"], h["op"]) for h in history})},
        },
        "rows": scored,
    }


def main():
    data_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    rows, rejected = load(data_dir)
    res = analyse(rows)
    res["rejected"] = rejected
    res["loaded"] = len(rows)
    plain = lambda o: o.item() if hasattr(o, "item") else str(o)  # noqa: E731  numpy scalars
    (out / "c2.json").write_text(json.dumps(res, indent=1, default=plain))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1, default=plain))


if __name__ == "__main__":
    main()
