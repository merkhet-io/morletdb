"""E7 analysis (PROTOCOL.md): S(N) = p95(X-FULL) / p95(X-PG) and R(N) = p95(X-PG) / p95(P-PG) per gallery and
configuration, with 95% bootstrap intervals over queries, and the gap to the materialized scan.

  python3 evaluation/screened_exact/analyze_e7.py     # results/{gate,run}_N*_*.json -> results/summary.json
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "strong_exact"))
from analyze import bootstrap_ratio, by_group  # noqa: E402

RES = HERE / "results"
ARMS = ["X-PG", "X-FULL", "P-PG", "X-MAT32R"]


def speed_rule(ci):
    return "screened faster" if ci[0] > 1 else ("screened slower" if ci[1] < 1 else "tie")


def probe_rule(ci):
    return "exact faster" if ci[1] < 1 else ("probes faster" if ci[0] > 1 else "tie")


def main():
    s = {}
    for g in ("N1", "N2", "N3"):
        gate = RES / f"gate_{g}.json"
        if not gate.exists():
            continue
        gj = json.loads(gate.read_text())
        s[g] = {"gate_passed": gj["passed"]}
        for k in ("identity_with_measured_build", "identity_with_full_path"):
            s[g][k] = {a: b for a, b in gj[k].items() if a != "differ"}
        for cfg in ("default", "w8"):
            p = RES / f"run_{g}_{cfg}.json"
            if not p.exists():
                continue
            run = json.loads(p.read_text())
            per = run["per_query"]
            sp, rp = bootstrap_ratio(per, "X-FULL", "X-PG"), bootstrap_ratio(per, "X-PG", "P-PG")
            s[g][cfg] = {
                "windows": run["materialization"]["windows"],
                "request_ms": {a: by_group(per, lambda x, a=a: x["ms"][a]) for a in ARMS},
                "recall_vs_X-PG": {a: {"mean": float(np.mean([per[q]["recall"][a] for q in per])),
                                       "min": float(min(per[q]["recall"][a] for q in per))} for a in ARMS},
                "S = p95(X-FULL) / p95(X-PG)": sp, "speed_verdict": speed_rule(sp["ci95"]),
                "R = p95(X-PG) / p95(P-PG)": rp, "probe_verdict": probe_rule(rp["ci95"]),
                "X-PG over X-MAT32R": bootstrap_ratio(per, "X-PG", "X-MAT32R"),
                "round2_differs": {q: per[q]["round2_differs"] for q in per if per[q]["round2_differs"]},
                "reloptions": run.get("envelope_table_reloptions"), "rounds": run["rounds"],
            }
    (RES / "summary.json").write_text(json.dumps(s, indent=1) + "\n")
    for g, x in s.items():
        for cfg in ("default", "w8"):
            if cfg in x:
                y = x[cfg]
                fmt = lambda d: {k: (round(v, 2) if isinstance(v, float) else [round(c, 2) for c in v]) for k, v in d.items()}
                print(g, cfg, {a: round(y["request_ms"][a]["all"]["p95"], 1) for a in ARMS},
                      "S", fmt(y["S = p95(X-FULL) / p95(X-PG)"]), y["speed_verdict"],
                      "R", fmt(y["R = p95(X-PG) / p95(P-PG)"]), y["probe_verdict"])


if __name__ == "__main__":
    main()
