#!/usr/bin/env python3
"""J2 step 2: the frozen analysis (PROTOCOL.md), leave one insert out.

  analyze.py J1_STATES J1_CALLS FEWSHOT_CALLS OUT_JSON
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

WORN_VB, RESAMPLES, SEED = 0.3, 2000, 7
CHANNELS = ("smcDC", "smcAC", "vib_table", "vib_spindle", "AE_table", "AE_spindle")
VALUES = ("duration_s", "mean", "std", "rms", "p75", "peak_abs", "crest", "kurtosis")


def auroc(s, y):
    s, y = np.asarray(s, float), np.asarray(y, bool)
    pos, neg = s[y], s[~y]
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum())
                 / (len(pos) * len(neg)))


def balanced(pred, y):
    pred, y = np.asarray(pred, bool), np.asarray(y, bool)
    return 0.5 * (pred[y].mean() + (~pred[~y]).mean()), float(pred[y].mean()), float((~pred[~y]).mean())


def best_threshold(s, y):
    """The threshold with the highest balanced accuracy on (s, y): midpoints between sorted distinct values;
    ties go to the lowest threshold."""
    v = np.unique(s)
    cands = np.concatenate([[v[0] - 1], (v[:-1] + v[1:]) / 2, [v[-1] + 1]])
    scores = [balanced(s >= t, y)[0] for t in cands]
    return float(cands[int(np.argmax(scores))])


def features(state):
    return [((state["channels"][c]["values"].get(v) or {}).get("relative_deviation") or 0.0)
            for c in CHANNELS for v in VALUES]


def mean_noul(calls, key="replace_insert"):
    out = {}
    for c in calls:
        if c["http_status"] == 200 and c["schema_ok"]:
            out.setdefault(c["row"], []).append(c["response"]["answers"][key]["noul"])
    return out


def main():
    rows = json.loads(Path(sys.argv[1]).read_text())["rows"]
    j1 = mean_noul([json.loads(x) for x in Path(sys.argv[2]).read_text().splitlines()])
    fs_calls = [json.loads(x) for x in Path(sys.argv[3]).read_text().splitlines()]
    j4 = mean_noul(fs_calls)
    idx = [i for i in range(len(rows)) if i in j1 and i in j4]
    case = np.array([rows[i]["case"] for i in idx])
    y = np.array([rows[i]["vb"] >= WORN_VB for i in idx])
    X = np.array([features(rows[i]["state"]) for i in idx])
    scores = {"jev_zero_shot": np.array([np.mean(j1[i]) for i in idx]),
              "jev_four_examples": np.array([np.mean(j4[i]) for i in idx]),
              "conditioned_score": np.array([rows[i]["conditioned_score"] for i in idx]),
              "time_in_use": np.array([rows[i]["time_in_use_min"] for i in idx])}
    lr = np.zeros(len(idx))
    for h in np.unique(case):
        tr, te = case != h, case == h
        mu, sd = X[tr].mean(0), X[tr].std(0)
        sd[sd == 0] = 1
        with warnings.catch_warnings():   # numpy's spurious matmul warnings with Apple's Accelerate
            warnings.simplefilter("ignore", RuntimeWarning)
            model = LogisticRegression(C=1.0, max_iter=5000).fit((X[tr] - mu) / sd, y[tr])
            lr[te] = model.predict_proba((X[te] - mu) / sd)[:, 1]
    scores["local_logistic_regression"] = lr

    res = {"runs": len(idx), "worn": int(y.sum()), "inserts": [int(c) for c in np.unique(case)],
           "fewshot_calls": len(fs_calls),
           "fewshot_http_errors": sum(c["http_status"] != 200 for c in fs_calls),
           "fewshot_schema_errors": sum(c["http_status"] == 200 and not c["schema_ok"] for c in fs_calls),
           "fewshot_input_tokens": sum(((c["response"] or {}).get("usage") or {}).get("input_tokens") or 0
                                       for c in fs_calls),
           "fewshot_latency_ms_p50": float(np.median([c["wall_ms"] for c in fs_calls if c["http_status"] == 200])),
           "scores": {}}
    for name, s in scores.items():
        pred = np.zeros(len(idx), bool)
        per_insert = {}
        for h in np.unique(case):
            tr, te = case != h, case == h
            t = best_threshold(s[tr], y[tr])
            pred[te] = s[te] >= t
            runs = sorted((rows[i]["run"], bool(p), bool(w)) for i, p, w in zip(np.array(idx)[te], pred[te], y[te]))
            first_flag = next((k for k, (_, p, _) in enumerate(runs) if p), None)
            first_worn = next((k for k, (_, _, w) in enumerate(runs) if w), None)
            per_insert[int(h)] = {"threshold": t, "first_flag_cut_index": first_flag,
                                  "first_worn_cut_index": first_worn,
                                  "cuts_early(+)/late(-)": (None if first_flag is None or first_worn is None
                                                            else first_worn - first_flag)}
        ba, sens, spec = balanced(pred, y)
        entry = {"auroc": auroc(s, y), "transferred_threshold": {"balanced_accuracy": ba, "sensitivity": sens,
                                                                 "specificity": spec, "per_insert": per_insert}}
        if name.startswith("jev") or name.startswith("local"):
            entry["at_0.5"] = dict(zip(("balanced_accuracy", "sensitivity", "specificity"), balanced(s >= 0.5, y)))
            entry["brier"] = float(np.mean((s - y) ** 2))
        res["scores"][name] = entry

    rng = np.random.default_rng(SEED)
    d_ex, d_lr = [], []
    for _ in range(RESAMPLES):
        b = rng.integers(0, len(idx), len(idx))
        if y[b].all() or not y[b].any():
            continue
        a0 = auroc(scores["jev_zero_shot"][b], y[b])
        d_ex.append(auroc(scores["jev_four_examples"][b], y[b]) - a0)
        d_lr.append(auroc(lr[b], y[b]) - a0)
    A = {k: v["auroc"] for k, v in res["scores"].items()}
    ci = lambda d: [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]
    res["delta_auroc_examples_vs_zero_shot"] = {"point": A["jev_four_examples"] - A["jev_zero_shot"], "ci95": ci(d_ex)}
    res["delta_auroc_local_vs_jev_zero_shot"] = {"point": A["local_logistic_regression"] - A["jev_zero_shot"],
                                                 "ci95": ci(d_lr)}
    ba0 = res["scores"]["jev_zero_shot"]["transferred_threshold"]["balanced_accuracy"]
    res["answers"] = {
        "Q1_threshold_transfers": "YES" if ba0 >= 0.80 else "NO",
        "Q2_local_model_matches_jev": "YES" if A["local_logistic_regression"] >= A["jev_zero_shot"] - 0.05 else "NO",
        "Q3_examples_help": ("YES" if res["delta_auroc_examples_vs_zero_shot"]["point"] >= 0.02
                             and res["delta_auroc_examples_vs_zero_shot"]["ci95"][0] > 0 else "NO"),
    }
    Path(sys.argv[4]).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
