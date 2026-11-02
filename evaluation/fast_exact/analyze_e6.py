"""E6 analysis (PROTOCOL.md): R(N) = p95(X-PG) / p95(P-PG) per gallery and configuration, the gap to the
materialized scan, and the speed-up over the measured build (E4, across sessions).

  python3 evaluation/fast_exact/analyze_e6.py     # results/{gate,run}_N*_*.json -> results/summary.json
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "strong_exact"))
from analyze import bootstrap_ratio, by_group  # noqa: E402

RES, E4 = HERE / "results", HERE.parent / "scaling_crossover" / "results"
ARMS = ["X-PG", "P-PG", "X-MAT32R"]


def rule(ci):
    return "exact faster" if ci[1] < 1 else ("probes faster" if ci[0] > 1 else "tie")


def p95(per, arm):
    return float(np.percentile([x for q in per.values() for x in q["ms"][arm]], 95))


def main():
    s = {}
    for g in ("N1", "N2", "N3"):
        gate = RES / f"gate_{g}.json"
        if not gate.exists():
            continue
        gj = json.loads(gate.read_text())
        s[g] = {"gate_passed": gj["passed"], "identity": {k: v for k, v in gj["identity_with_measured_build"].items() if k != "differ"}}
        old = json.loads((E4 / f"run_{g}.json").read_text())["per_query"]
        for cfg in ("default", "w8"):
            p = RES / f"run_{g}_{cfg}.json"
            if not p.exists():
                continue
            run = json.loads(p.read_text())
            per = run["per_query"]
            r = bootstrap_ratio(per, "X-PG", "P-PG")
            s[g][cfg] = {
                "windows": run["materialization"]["windows"],
                "request_ms": {a: by_group(per, lambda x, a=a: x["ms"][a]) for a in ARMS},
                "recall_vs_X-PG": {a: {"mean": float(np.mean([per[q]["recall"][a] for q in per])),
                                       "min": float(min(per[q]["recall"][a] for q in per))} for a in ARMS},
                "R = p95(X-PG) / p95(P-PG)": r, "verdict": rule(r["ci95"]),
                "X-PG over X-MAT32R": bootstrap_ratio(per, "X-PG", "X-MAT32R"),
                "speedup over measured build (E4 X-PG p95 / E6 X-PG p95, across sessions)": p95(old, "X-PG") / p95(per, "X-PG"),
                "E4 p95 X-PG / P-PG (measured build)": {"X-PG": p95(old, "X-PG"), "P-PG": p95(old, "P-PG")},
                "round2_differs": {q: per[q]["round2_differs"] for q in per if per[q]["round2_differs"]},
                "reloptions": run.get("envelope_table_reloptions"), "rounds": run["rounds"],
            }
    (RES / "summary.json").write_text(json.dumps(s, indent=1) + "\n")
    for g, x in s.items():
        for cfg in ("default", "w8"):
            if cfg in x:
                y = x[cfg]
                print(g, cfg, {a: round(y["request_ms"][a]["all"]["p95"], 1) for a in ARMS},
                      "R", {k: (round(v, 2) if isinstance(v, float) else [round(c, 2) for c in v]) for k, v in y["R = p95(X-PG) / p95(P-PG)"].items()},
                      y["verdict"], "speedup", round(y["speedup over measured build (E4 X-PG p95 / E6 X-PG p95, across sessions)"], 2))


if __name__ == "__main__":
    main()
