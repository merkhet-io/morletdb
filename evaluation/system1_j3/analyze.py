#!/usr/bin/env python3
"""J3 step 3: the frozen analysis (PROTOCOL.md).

  analyze.py RESULTS_DIR OUT_JSON

Reads RESULTS_DIR/{states.json, check_<arm>.json, calls_<arm>.jsonl} for arms laya, laya-td, jev and J1's states
and calls. Leave one insert out as J2 for thresholds and for the recalibration (Platt on the logit).
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "system1_j2"))
import analyze as j2   # noqa: E402  auroc, balanced, best_threshold, features, mean_noul

ARMS = ("laya", "laya-td", "jev")
PRIMARY, RESAMPLES, SEED = "laya-td", 2000, 7


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def main():
    rd, out = Path(sys.argv[1]), Path(sys.argv[2])
    j1 = json.loads((HERE.parent / "system1_j1/results/states.json").read_text())["rows"]
    j1_calls = j2.mean_noul([json.loads(x) for x in (HERE.parent / "system1_j1/results/calls.jsonl").read_text().splitlines()])
    y = np.array([r["vb"] >= j2.WORN_VB for r in j1])
    case = np.array([r["case"] for r in j1])
    res = {"runs": len(j1), "worn": int(y.sum()), "k": json.loads((rd / "states.json").read_text())["k"], "arms": {}}
    scores = {}
    for arm in ARMS:
        calls = [json.loads(x) for x in (rd / f"calls_{arm}.jsonl").read_text().splitlines()]
        ok = {c["row"]: c for c in calls if c["schema_ok"]}
        control = json.loads((rd / f"check_{arm}.json").read_text())["passed"]
        # a failed control (#156) makes the yes/no answer invalid; the wear level's expected value / 2 is used then
        key = "noul" if control else "score"
        def prob(i):
            if i not in ok:
                return np.nan
            a = ok[i]["response"]["answers"]
            return a["replace_insert"]["noul"] if control else a["wear_level"]["score"] / 2
        s = np.array([prob(i) for i in range(len(j1))], float)
        lat = sorted(c["wall_ms"] for c in calls if c["schema_ok"])
        trunc = sum(bool(((c["response"] or {}).get("usage") or {}).get("truncated")) for c in calls)
        res["arms"][arm] = {"control_passed": control, "probability_from": key, "calls": len(calls),
                            "schema_errors": len(calls) - len(ok), "truncated": trunc,
                            "latency_ms_p50": lat[len(lat) // 2] if lat else None,
                            "latency_ms_p95": lat[int(0.95 * len(lat))] if lat else None,
                            "wear_level_spearman_vb": None}
        lv = np.array([ok[i]["response"]["answers"]["wear_level"]["score"] if i in ok else np.nan for i in range(len(j1))])
        m = ~np.isnan(lv)
        from scipy.stats import spearmanr
        res["arms"][arm]["wear_level_spearman_vb"] = float(spearmanr(lv[m], np.array([r["vb"] for r in j1])[m]).statistic)
        scores[arm] = s
    scores["jev_full_state_j1"] = np.array([np.mean(j1_calls[i]) if i in j1_calls else np.nan for i in range(len(j1))])
    scores["conditioned_score"] = np.array([r["conditioned_score"] for r in j1])
    X = np.array([j2.features(r["state"]) for r in j1])
    lr = np.zeros(len(j1))
    for h in np.unique(case):
        tr, te = case != h, case == h
        mu, sd = X[tr].mean(0), X[tr].std(0)
        sd[sd == 0] = 1
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            lr[te] = LogisticRegression(C=1.0, max_iter=5000).fit((X[tr] - mu) / sd, y[tr]).predict_proba((X[te] - mu) / sd)[:, 1]
    scores["local_logistic_regression"] = lr

    res["scores"] = {}
    for name, s in scores.items():
        m = ~np.isnan(s)
        pred, cal = np.zeros(len(s), bool), np.full(len(s), np.nan)
        for h in np.unique(case):
            tr, te = (case != h) & m, (case == h) & m
            pred[te] = s[te] >= j2.best_threshold(s[tr], y[tr])
            if name in ARMS or name == "jev_full_state_j1":
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    pl = LogisticRegression(C=1e6, max_iter=5000).fit(logit(s[tr])[:, None], y[tr])
                cal[te] = pl.predict_proba(logit(s[te])[:, None])[:, 1]
        ba, sens, spec = j2.balanced(pred[m], y[m])
        e = {"n": int(m.sum()), "auroc": j2.auroc(s[m], y[m]),
             "transferred_threshold": {"balanced_accuracy": ba, "sensitivity": sens, "specificity": spec}}
        if name in ARMS or name.startswith("jev") or name.startswith("local"):
            e["at_0.5_balanced_accuracy"] = j2.balanced(s[m] >= 0.5, y[m])[0]
            e["brier"] = float(np.mean((s[m] - y[m]) ** 2))
        if not np.isnan(cal).all():
            mc = ~np.isnan(cal)
            e["recalibrated_brier"] = float(np.mean((cal[mc] - y[mc]) ** 2))
            e["recalibrated_at_0.5_balanced_accuracy"] = j2.balanced(cal[mc] >= 0.5, y[mc])[0]
        res["scores"][name] = e
    res["base_rate_brier"] = float(np.mean((y.mean() - y) ** 2))

    rng = np.random.default_rng(SEED)
    pairs = {"laya-td_vs_jev_compact": ("laya-td", "jev"), "laya_vs_jev_compact": ("laya", "jev"),
             "jev_compact_vs_jev_full": ("jev", "jev_full_state_j1"),
             "laya-td_vs_local_lr": ("laya-td", "local_logistic_regression")}
    m = np.all([~np.isnan(scores[a]) for a in ("laya", "laya-td", "jev", "jev_full_state_j1")], axis=0)
    idx = np.where(m)[0]
    d = {k: [] for k in pairs}
    for _ in range(RESAMPLES):
        b = rng.choice(idx, len(idx))
        if y[b].all() or not y[b].any():
            continue
        for k, (a, c) in pairs.items():
            d[k].append(j2.auroc(scores[a][b], y[b]) - j2.auroc(scores[c][b], y[b]))
    res["deltas"] = {k: {"point": j2.auroc(scores[a][idx], y[idx]) - j2.auroc(scores[c][idx], y[idx]),
                         "ci95": [float(np.percentile(d[k], 2.5)), float(np.percentile(d[k], 97.5))]}
                     for k, (a, c) in pairs.items()}
    A = res["scores"][PRIMARY]["auroc"]
    res["decision"] = ("SUPPORTED" if A >= 0.90 and res["deltas"]["laya-td_vs_jev_compact"]["point"] >= -0.05
                       else "NOT SUPPORTED")
    out.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
