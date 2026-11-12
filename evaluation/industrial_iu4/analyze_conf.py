#!/usr/bin/env python3
"""IU4 confirmation (PROTOCOL.md, Hypotheses): H1 (with its ceiling rule), H1b, H3 and the reported measures on
M2 and M3, with the models and choices fixed on M1 (dev.json, model_C.json).

  analyze_conf.py RESULTS_DIR      writes RESULTS_DIR/confirm.json
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_dev import a_candidates, b_score, causal_quantile, load  # noqa: E402
from features import ARMS, feature_dict, predict  # noqa: E402

WORN_VB, SEED, RESAMPLES = 100.0, 20261112, 10000


def two_level(per_tool_scores, rng):
    """Bootstrap over tools, then runs within tools: mean per-tool AUROC difference C - A."""
    tools = list(per_tool_scores)
    out = []
    for _ in range(RESAMPLES):
        diffs = []
        for t in rng.choice(tools, len(tools)):
            y, a, c = per_tool_scores[t]
            idx = rng.integers(0, len(y), len(y))
            if len(set(y[idx])) < 2:
                continue
            diffs.append(roc_auc_score(y[idx], c[idx]) - roc_auc_score(y[idx], a[idx]))
        if diffs:
            out.append(float(np.mean(diffs)))
    return np.percentile(out, [2.5, 97.5]).tolist()


def unit_only(per_tool_diff, rng):
    v = np.array(list(per_tool_diff.values()))
    boots = [float(np.mean(rng.choice(v, len(v)))) for _ in range(RESAMPLES)]
    return np.percentile(boots, [2.5, 97.5]).tolist()


def main():
    res_dir = Path(sys.argv[1])
    dev = json.loads((res_dir / "dev.json").read_text())
    models = json.loads((res_dir / "model_C.json").read_text())
    rows = load(res_dir, 2) + load(res_dir, 3)
    rng = np.random.default_rng(SEED)
    out = {"runs": len(rows), "arms": {}}
    for arm in ARMS:
        d = dev["arms"][arm]
        a_key, a_sign = d["A"]["pair"], d["A"]["direction"]
        cands = a_candidates(rows, arm)
        a_all = np.array([np.nan if v is None else a_sign * v for v in cands[a_key]], float)
        c_all = np.array([predict(models[arm], feature_dict(r["states"], arm))[0] for r in rows])
        b_all = np.array([np.nan if (v := b_score(r, arm)) is None else d["B"]["direction"] * v for r in rows])
        y_all = np.array([1 if r["vb"] >= WORN_VB else 0 for r in rows])
        tools = np.array([r["tool"] for r in rows])
        per_tool, scores, h1b = {}, {}, {}
        for t in sorted(set(tools)):
            i = np.where(tools == t)[0]
            y, a, c, b = y_all[i], np.nan_to_num(a_all[i]), c_all[i], np.nan_to_num(b_all[i])
            vb = np.array([rows[k]["vb"] for k in i])
            per_tool[int(t)] = {"runs": int(len(i)), "positives": int(y.sum()),
                                "auc_A": float(roc_auc_score(y, a)), "auc_B": float(roc_auc_score(y, b)),
                                "auc_C": float(roc_auc_score(y, c)),
                                "spearman_vb_A": float(spearmanr(a, vb)[0]), "spearman_vb_C": float(spearmanr(c, vb)[0])}
            scores[int(t)] = (y, a, c)
            for name, s in (("A", list(a)), ("C", list(c))):
                q = np.array(causal_quantile(s))
                flags = q >= d["h1b_thresholds"][name]
                first = next((int(rows[i[k]]["run"]) for k in range(len(i)) if flags[k]), None)
                first_vb = next((float(vb[k]) for k in range(len(i)) if flags[k]), None)
                h1b.setdefault(name, {})[int(t)] = {"first_flag_run": first, "first_flag_vb": first_vb,
                                                    "flagged_before_vb150": bool(first_vb is not None and first_vb < 150),
                                                    "false_alarms_vb_below_50": int((flags & (vb < 50)).sum())}
        diff = {t: v["auc_C"] - v["auc_A"] for t, v in per_tool.items()}
        ceiling = sum(v["auc_A"] >= 0.98 for v in per_tool.values()) >= 5
        ci = two_level(scores, rng)
        mean_diff = float(np.mean(list(diff.values())))
        h1 = {"mean_diff_C_minus_A": mean_diff, "ci95_two_level": ci, "ci95_unit_only": unit_only(diff, rng),
              "ceiling_rule_triggered": ceiling}
        h1["verdict"] = ("undecidable (ceiling rule)" if ceiling else
                         "pass" if mean_diff >= 0.03 and ci[0] > 0 else "fail")
        fa = {n: sum(v["false_alarms_vb_below_50"] for v in h1b[n].values()) for n in h1b}
        h1b_pass = all(v["flagged_before_vb150"] for v in h1b["C"].values()) and fa["C"] < fa["A"]
        out["arms"][arm] = {"A_pair": a_key, "per_tool": per_tool, "H1": h1,
                            "H1b": {"per_tool": h1b, "false_alarms_total": fa, "verdict": "pass" if h1b_pass else "fail"}}
    # H3: evidence of every C decision (full arm and controller arm)
    ev = {"decisions": 0, "with_precedent_from_other_tool": 0}
    for p in sorted(res_dir.glob("decisions_m[23]t*.jsonl")):
        for line in p.open():
            dcs = json.loads(line)
            ev["decisions"] += 1
            prec = dcs["evidence"].get("precedents") or []
            other = [e for e in prec if not e["recording_id"].startswith(f"m{dcs['machine']}t{dcs['tool']}r")]
            ev["with_precedent_from_other_tool"] += bool(other)
    ev["verdict"] = "pass" if ev["decisions"] and ev["with_precedent_from_other_tool"] == ev["decisions"] else "fail"
    out["H3"] = ev
    (res_dir / "confirm.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({arm: {"H1": v["H1"], "H1b": v["H1b"]["verdict"]} for arm, v in out["arms"].items()} | {"H3": ev},
                     indent=1))


if __name__ == "__main__":
    main()
