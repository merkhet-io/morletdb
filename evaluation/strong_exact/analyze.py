"""E3 analysis: per-boundary latency, effective threads, recall and the frozen decision rule (PROTOCOL.md).

  python3 evaluation/strong_exact/analyze.py      # reads results/run_{full,matched}.json, writes results/summary.json
Standard library and numpy only.
"""
import json
import sys
from pathlib import Path

import numpy as np

RES = Path(__file__).resolve().parent / "results"
ARMS = ["X-PG", "P-PG", "X-MAT64", "X-MAT32R"]
MAT = ["X-MAT64", "X-MAT32R"]
SEED, B = 20261101, 10_000


def stats(xs):
    a = np.asarray(xs, dtype=float)
    return {"p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)), "p99": float(np.percentile(a, 99)),
            "mean": float(a.mean()), "n": int(a.size)}


def by_group(per, f):
    groups = {"all": list(per), **{t: [q for q in per if per[q]["type"] == t] for t in ("random", "energetic")}}
    return {g: stats([x for q in qs for x in f(per[q])]) for g, qs in groups.items()}


def bootstrap_ratio(per, num, den):
    keys = list(per)
    rng = np.random.default_rng(SEED)
    a = [np.asarray(per[q]["ms"][num]) for q in keys]
    b = [np.asarray(per[q]["ms"][den]) for q in keys]
    r = np.empty(B)
    for i in range(B):
        idx = rng.integers(0, len(keys), len(keys))
        r[i] = np.percentile(np.concatenate([a[j] for j in idx]), 95) / np.percentile(np.concatenate([b[j] for j in idx]), 95)
    point = np.percentile(np.concatenate(a), 95) / np.percentile(np.concatenate(b), 95)
    return {"point": float(point), "ci95": [float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))]}


def verdict(ci):
    if ci[1] <= 0.5:
        return "advantage for probes demonstrated"
    if ci[0] > 0.5:
        return "no advantage demonstrated"
    return "inconclusive"


def config_summary(run, gate_ok):
    per = run["per_query"]
    out = {"request_ms": {a: by_group(per, lambda r, a=a: r["ms"][a]) for a in ARMS}}
    for a in MAT:
        out.setdefault("parts_ms", {})[a] = {
            part: by_group(per, lambda r, a=a, part=part: [p[part] for p in r["parts"][a]])
            for part in ("prep_ms", "kernel_ms", "search_ms")}
        cpu = np.array([p["kernel_cpu_ms"] for q in per for p in per[q]["parts"][a]])
        wall = np.array([p["kernel_ms"] for q in per for p in per[q]["parts"][a]])
        out.setdefault("effective_threads_kernel", {})[a] = {"median": float(np.median(cpu / wall)),
                                                             "p5": float(np.percentile(cpu / wall, 5)),
                                                             "p95": float(np.percentile(cpu / wall, 95))}
    out["recall_vs_X-PG"] = {a: {"mean": float(np.mean([per[q]["recall"][a] for q in per])),
                                 "min": float(min(per[q]["recall"][a] for q in per))} for a in ARMS}
    out["round2_differs"] = {q: per[q]["round2_differs"] for q in per if per[q]["round2_differs"]}
    valid = [a for a in MAT if gate_ok and out["recall_vs_X-PG"][a]["min"] == 1.0]
    best = min(valid, key=lambda a: out["request_ms"][a]["all"]["p95"]) if valid else None
    out["fastest_valid_matrix_arm"] = best
    if best:
        r = bootstrap_ratio(per, "P-PG", best)
        out["decision"] = {"R = p95(P-PG) / p95(matrix)": r, "verdict": verdict(r["ci95"])}
    out["X-PG_over_X-MAT64_request_p95"] = bootstrap_ratio(per, "X-PG", "X-MAT64")
    out["rounds"] = run["rounds"]
    out["materialization"] = run["materialization"]
    out["maxrss_bytes"] = run["maxrss_bytes"]
    out["threads_env"] = run["threads_env"]
    return out


def main():
    gate = json.loads((RES / "gate.json").read_text())
    s = {"gate_passed": gate["passed"], "bootstrap": {"resamples": B, "seed": SEED, "unit": "query (both timings)"}}
    for cfg in ("full", "matched"):
        p = RES / f"run_{cfg}.json"
        if p.exists():
            s[cfg] = config_summary(json.loads(p.read_text()), gate["passed"])
    s["primary"] = s.get("full", {}).get("decision")
    s["secondary"] = s.get("matched", {}).get("decision")
    (RES / "summary.json").write_text(json.dumps(s, indent=1) + "\n")
    for cfg in ("full", "matched"):
        if cfg in s:
            c = s[cfg]
            print(cfg, {a: round(c["request_ms"][a]["all"]["p95"], 1) for a in ARMS},
                  "kernel p95", {a: round(c["parts_ms"][a]["kernel_ms"]["all"]["p95"], 1) for a in MAT},
                  "threads", {a: round(c["effective_threads_kernel"][a]["median"], 1) for a in MAT},
                  "decision", c.get("decision"))


if __name__ == "__main__":
    sys.exit(main())
