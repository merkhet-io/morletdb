#!/usr/bin/env python3
"""J1 step 3: the frozen analysis (PROTOCOL.md). Reads states.json and calls.jsonl, writes result.json.

  analyze.py STATES_JSON CALLS_JSONL OUT_JSON
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy import stats

WORN_VB, RESAMPLES, SEED, PRICE_PER_M_INPUT = 0.3, 2000, 7, 0.042


def auroc(score, worn):
    s, y = np.asarray(score, float), np.asarray(worn, bool)
    pos, neg = s[y], s[~y]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    gt = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(gt / (len(pos) * len(neg)))


def main():
    states = json.loads(Path(sys.argv[1]).read_text())["rows"]
    calls = [json.loads(line) for line in Path(sys.argv[2]).read_text().splitlines()]
    ok = [c for c in calls if c["http_status"] == 200 and c["schema_ok"]]
    per_row = {}
    for c in ok:
        a = c["response"]["answers"]
        per_row.setdefault(c["row"], []).append((a["replace_insert"]["noul"], a["wear_level"]["score"]))
    rows = [i for i in range(len(states)) if i in per_row]
    vb = np.array([states[i]["vb"] for i in rows])
    worn = vb >= WORN_VB
    p = np.array([np.mean([x[0] for x in per_row[i]]) for i in rows])
    level = np.array([np.mean([x[1] for x in per_row[i]]) for i in rows])
    cond = np.array([states[i]["conditioned_score"] for i in rows])
    tiu = np.array([states[i]["time_in_use_min"] for i in rows])
    rng = np.random.default_rng(SEED)
    d_cond, d_time = [], []
    for _ in range(RESAMPLES):
        idx = rng.integers(0, len(rows), len(rows))
        if worn[idx].all() or not worn[idx].any():
            continue
        d_cond.append(auroc(p[idx], worn[idx]) - auroc(cond[idx], worn[idx]))
        d_time.append(auroc(p[idx], worn[idx]) - auroc(tiu[idx], worn[idx]))
    a_jev, a_cond, a_time = auroc(p, worn), auroc(cond, worn), auroc(tiu, worn)
    delta = a_jev - a_cond
    supported = a_jev >= 0.90 and delta >= -0.05
    walls = [c["wall_ms"] for c in calls if c["http_status"] == 200]
    tokens = sum(((c["response"] or {}).get("usage") or {}).get("input_tokens") or 0 for c in calls)
    spread = [float(np.mean([abs(x[0] - np.mean([y[0] for y in per_row[i]])) for x in per_row[i]])) for i in rows]
    res = {
        "runs_scored": len(rows), "runs_in_study": len(states), "worn": int(worn.sum()),
        "calls": len(calls), "calls_ok": len(ok),
        "http_errors": sum(c["http_status"] != 200 for c in calls),
        "schema_errors": sum(c["http_status"] == 200 and not c["schema_ok"] for c in calls),
        "auroc": {"jev_replace_insert": a_jev, "conditioned_score": a_cond, "time_in_use": a_time},
        "delta_auroc_vs_conditioned": {"point": delta, "ci95": [float(np.percentile(d_cond, 2.5)),
                                                                float(np.percentile(d_cond, 97.5))]},
        "delta_auroc_vs_time_in_use": {"point": a_jev - a_time, "ci95": [float(np.percentile(d_time, 2.5)),
                                                                         float(np.percentile(d_time, 97.5))]},
        "spearman_vb": {"jev_wear_level": float(stats.spearmanr(level, vb).statistic),
                        "jev_replace_insert": float(stats.spearmanr(p, vb).statistic),
                        "conditioned_score": float(stats.spearmanr(cond, vb).statistic),
                        "time_in_use": float(stats.spearmanr(tiu, vb).statistic)},
        "brier_jev": float(np.mean((p - worn) ** 2)),
        "brier_base_rate": float(np.mean((worn.mean() - worn) ** 2)),
        "calibration": [{"bin": f"{lo:.1f}-{lo + 0.2:.1f}", "n": int(m.sum()),
                         "mean_p": float(p[m].mean()) if m.any() else None,
                         "worn_rate": float(worn[m].mean()) if m.any() else None}
                        for lo in np.arange(0, 1, 0.2) for m in [(p >= lo) & ((p < lo + 0.2) | (lo >= 0.8))]],
        "repeat_spread_mean_abs": float(np.mean(spread)),
        "latency_ms": {"p50": float(np.percentile(walls, 50)), "p95": float(np.percentile(walls, 95))},
        "input_tokens": tokens, "cost_usd_at_list_price": tokens / 1e6 * PRICE_PER_M_INPUT,
        "decision": "SUPPORTED" if supported else "NOT SUPPORTED",
    }
    Path(sys.argv[3]).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
