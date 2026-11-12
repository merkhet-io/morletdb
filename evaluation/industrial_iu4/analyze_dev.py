#!/usr/bin/env python3
"""IU4 development (PROTOCOL.md): from M1's states, train C, choose A, fix the H1b thresholds; write the models.

  analyze_dev.py STATES_DIR OUT_DIR      (numpy, scikit-learn; M1 only)

Writes OUT_DIR/model_C.json (one model per arm: full, controller), OUT_DIR/dev.json (development AUROCs of A, B and
C per arm, A's choice, thresholds). Runs 1-10 of each tool are baseline and never scored (they have no state).
"""
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import ARMS, VALUES, feature_dict  # noqa: E402

WORN_VB = 100.0


def load(states_dir, machine):
    rows = []
    for p in sorted(Path(states_dir).glob(f"states_m{machine}t*.jsonl")):
        rows += [json.loads(line) for line in p.open()]
    return rows


def a_candidates(rows, arm):
    """Every (signal, value) as raw value and as own-baseline relative deviation, in the protocol's order."""
    out = {}
    for sig in ARMS[arm]:
        for name in VALUES:
            raw, ob = [], []
            for r in rows:
                st = r["states"][sig]
                hv = {v["name"]: v for v in st["values"]}
                own = {v["name"]: v for v in (st.get("own_baseline") or {}).get("values", [])}
                raw.append(hv.get(name, {}).get("value"))
                ob.append(own.get(name, {}).get("relative_deviation"))
            out[f"{sig}|raw|{name}"] = raw
            out[f"{sig}|ob|{name}"] = ob
    return out


def auc_dir(y, s):
    s = np.array([np.nan if v is None else v for v in s], float)
    ok = np.isfinite(s)
    if ok.sum() < 10 or len(set(y[ok])) < 2:
        return None, None
    a = roc_auc_score(y[ok], s[ok])
    return (a, 1) if a >= 0.5 else (1 - a, -1)


def b_score(r, arm):
    vals = []
    for sig in ARMS[arm]:
        d = [x for x in r["shape"][sig] if x is not None]
        if d:
            vals.append(float(np.median(d)))
    return max(vals) if vals else None


def causal_quantile(scores):
    """Each score's quantile among the same tool's earlier scored runs (first run: 0.5)."""
    out = []
    for i, s in enumerate(scores):
        prev = scores[:i]
        out.append(0.5 if not prev else (sum(p < s for p in prev) + 0.5 * sum(p == s for p in prev)) / len(prev))
    return out


def youden(y, q):
    best = (-1, None)
    for t in sorted(set(q)):
        flag = np.array(q) >= t
        tpr = (flag & (y == 1)).sum() / max(1, (y == 1).sum())
        fpr = (flag & (y == 0)).sum() / max(1, (y == 0).sum())
        if tpr - fpr > best[0]:
            best = (tpr - fpr, t)
    return best[1]


def main():
    states_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    rows = load(states_dir, 1)
    y = np.array([1 if r["vb"] >= WORN_VB else 0 for r in rows])
    tools = np.array([r["tool"] for r in rows])
    models, dev = {}, {"runs": len(rows), "positives": int(y.sum()), "arms": {}}
    for arm in ARMS:
        fds = [feature_dict(r["states"], arm) for r in rows]
        names = sorted(fds[0])
        X = np.array([[fd[n] for n in names] for fd in fds])
        mu, sd = X.mean(0), X.std(0)
        sd[sd == 0] = 1.0
        lr = LogisticRegression(C=1.0, max_iter=5000).fit((X - mu) / sd, y)
        models[arm] = {"names": names, "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0]),
                       "mean": mu.tolist(), "sd": sd.tolist(),
                       "settings": "sklearn LogisticRegression(C=1.0, max_iter=5000), lbfgs, L2; standardised on M1"}
        c_score = lr.decision_function((X - mu) / sd)
        cands = a_candidates(rows, arm)
        best = None
        for key, s in cands.items():          # protocol order; ties keep the earlier pair
            a, sign = auc_dir(y, s)
            if a is not None and (best is None or a > best[1]):
                best = (key, a, sign)
        a_key, a_auc, a_sign = best
        b = [b_score(r, arm) for r in rows]
        b_auc = auc_dir(y, b)
        per_tool, thresholds = {}, {}
        scores = {"A": [a_sign * (v if v is not None else np.nan) for v in cands[a_key]], "C": list(c_score),
                  "B": [np.nan if v is None else v for v in b]}
        for name, s in scores.items():
            q = []
            for t in sorted(set(tools)):
                idx = np.where(tools == t)[0]
                q_t = causal_quantile([s[i] for i in idx])
                q += q_t
                per_tool.setdefault(name, {})[int(t)] = float(roc_auc_score(y[idx], np.nan_to_num(np.array(s)[idx])))
            order = np.concatenate([np.where(tools == t)[0] for t in sorted(set(tools))])
            thresholds[name] = youden(y[order], q)
        dev["arms"][arm] = {"A": {"pair": a_key, "auc_pooled": a_auc, "direction": a_sign},
                            "B": {"auc_pooled": b_auc[0], "direction": b_auc[1]},
                            "C": {"auc_pooled": float(roc_auc_score(y, c_score))},
                            "per_tool_auc": per_tool, "h1b_thresholds": thresholds}
    (out / "model_C.json").write_text(json.dumps(models))
    (out / "dev.json").write_text(json.dumps(dev, indent=1))
    print(json.dumps(dev, indent=1))


if __name__ == "__main__":
    main()
