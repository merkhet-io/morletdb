#!/usr/bin/env python3
"""C3: calibrated-v1 vs the C2 score on MillingVibes (PROTOCOL.md).

Usage: c3.py MILLINGVIBES_NPZ OUT   (compression environment: numpy, scipy)
The npz is the safe conversion of the published pickles (see PROTOCOL.md). Writes OUT/c3.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy import stats

RATE = 8000.0
SIMPLE = ("duration_s", "mean", "std", "rms", "p75", "peak_abs", "crest", "kurtosis")
VALUES = tuple(f"{v}_{a}" for a in "xyz" for v in SIMPLE)
C2_SET = ("duration_s_x", *(f"{v}_{a}" for a in "xyz" for v in ("std", "kurtosis", "crest")))
FLOOR = 0.01


def simple_v1(x):
    """morlet_core::values::simple."""
    n = len(x)
    mean = x.sum() / n
    m2 = ((x - mean) ** 2).sum() / n
    m4 = ((x - mean) ** 4).sum() / n
    rms = np.sqrt((x * x).sum() / n)
    peak = np.abs(x).max()
    return [n / RATE, mean, np.sqrt(m2), rms, float(np.percentile(x, 75)), peak,
            peak / rms if rms > 0 else 0.0, m4 / (m2 * m2) if m2 > 0 else 0.0]


def segment_values(seg):
    out = []
    for axis in seg:
        out += simple_v1(np.asarray(axis, float))
    return dict(zip(VALUES, out))


def spreads(history, names):
    """morlet_value_spreads with one context: relative MAD over history, floored."""
    out = {}
    for v in names:
        col = np.array([h[v] for h in history])
        med = np.median(col)
        out[v] = max(np.median(np.abs(col - med)) / abs(med), FLOOR) if med != 0 else None
    return out


def score(run, medians, names, spread=None):
    devs = []
    for v in names:
        med = medians[v]
        if med == 0:
            continue
        d = abs(run[v] - med) / abs(med)
        if spread is not None:
            if spread[v] is None:
                continue
            d /= spread[v]
        devs.append((d, v))
    return max(devs) if devs else (None, None)


def auroc(s, bad):
    s, b = np.asarray(s, float), np.asarray(bad, bool)
    return float(stats.mannwhitneyu(s[b], s[~b]).statistic / (b.sum() * (~b).sum()))


def analyse(train, test, resamples=2000, seed=7):
    """train/test: lists of dicts with values plus 'bad'."""
    history = [r for r in train if not r["bad"]]
    medians = {v: float(np.median([h[v] for h in history])) for v in VALUES}
    sp_all, sp_c2 = spreads(history, VALUES), spreads(history, C2_SET)
    arms = {
        "A_c2_score": [score(r, medians, C2_SET)[0] for r in test],
        "B_calibrated": [score(r, medians, VALUES, sp_all)[0] for r in test],
        "Bprime_calibrated_c2_set": [score(r, medians, C2_SET, sp_c2)[0] for r in test],
    }
    bad = [r["bad"] for r in test]
    au = {k: auroc(v, bad) for k, v in arms.items()}
    delta = au["B_calibrated"] - au["A_c2_score"]
    rng = np.random.default_rng(seed)
    b_idx, g_idx = np.where(bad)[0], np.where(~np.asarray(bad))[0]
    ds = []
    for _ in range(resamples):
        i = np.r_[rng.choice(b_idx, len(b_idx)), rng.choice(g_idx, len(g_idx))]
        bb = [bad[j] for j in i]
        ds.append(auroc([arms["B_calibrated"][j] for j in i], bb) - auroc([arms["A_c2_score"][j] for j in i], bb))
    ci = [float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5))]
    decision = ("supported" if delta >= 0.05 and ci[0] > 0 else "worse" if ci[1] < 0 else
                "no material difference" if ci[0] > -0.05 and ci[1] < 0.05 else "inconclusive")
    per_value = {}
    for v in VALUES:
        med = medians[v]
        if med == 0:
            continue
        per_value[v] = auroc([abs(r[v] - med) / abs(med) for r in test], bad)
    tops = {"bad": {}, "good": {}}
    for r in test:
        t = score(r, medians, VALUES, sp_all)[1]
        k = "bad" if r["bad"] else "good"
        tops[k][t] = tops[k].get(t, 0) + 1
    return {"history": len(history), "test": len(test), "test_bad": int(sum(bad)),
            "auroc": au, "delta_B_minus_A": delta, "ci95": ci, "decision": decision,
            "reported": {"per_value_auroc_relative_deviation": per_value, "spreads": sp_all,
                         "top_value_counts_B": tops}}


def main():
    npz, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    d = np.load(npz)
    train = [{**segment_values(x), "bad": y == 0} for x, y in zip(d["x_train"], d["y_train"])]
    test = [{**segment_values(x), "bad": y == 0} for x, y in zip(d["x_test"], d["y_test"])]
    res = analyse(train, test)
    plain = lambda o: o.item() if hasattr(o, "item") else str(o)  # noqa: E731
    (out / "c3.json").write_text(json.dumps(res, indent=1, default=plain))
    print(json.dumps(res, indent=1, default=plain))


if __name__ == "__main__":
    main()
