"""E4 analysis (PROTOCOL.md): per-gallery latency and recall, R(N) with bootstrap intervals, and the crossover
rule (measured, else a labelled extrapolation).

  python3 evaluation/scaling_crossover/analyze_e4.py     # results/{gate,run}_N{1,2,3}.json -> results/summary.json
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "strong_exact"))
from analyze import bootstrap_ratio, by_group, ARMS, MAT  # noqa: E402  E3's statistics

RES = HERE / "results"
GALLERIES = ["N1", "N2", "N3"]


def rule(ci):
    return "probes faster" if ci[1] < 1 else ("exact faster" if ci[0] > 1 else "tie")


def main():
    s, pts = {}, []
    for g in GALLERIES:
        gp, rp = RES / f"gate_{g}.json", RES / f"run_{g}.json"
        if not (gp.exists() and rp.exists()):
            continue
        gate, run = json.loads(gp.read_text()), json.loads(rp.read_text())
        per = run["per_query"]
        n = run["materialization"]["windows"]
        r = bootstrap_ratio(per, "P-PG", "X-MAT32R")
        s[g] = {"windows": n, "gate_passed": gate["passed"],
                "request_ms": {a: by_group(per, lambda x, a=a: x["ms"][a]) for a in ARMS},
                "parts_ms": {a: {p: by_group(per, lambda x, a=a, p=p: [y[p] for y in x["parts"][a]])
                                 for p in ("prep_ms", "kernel_ms", "search_ms")} for a in MAT},
                "recall_vs_X-PG": {a: {"mean": float(np.mean([per[q]["recall"][a] for q in per])),
                                       "min": float(min(per[q]["recall"][a] for q in per)),
                                       "below_0.9": int(sum(per[q]["recall"][a] < 0.9 for q in per))} for a in ARMS},
                "R = p95(P-PG) / p95(X-MAT32R)": r, "verdict": rule(r["ci95"]),
                "E3 threshold 0.5 met": r["ci95"][1] <= 0.5,
                "materialization": run["materialization"], "maxrss_bytes": run["maxrss_bytes"], "rounds": run["rounds"],
                "round2_differs": {q: per[q]["round2_differs"] for q in per if per[q]["round2_differs"]}}
        pts.append((n, s[g]["request_ms"]["P-PG"]["all"]["p95"], s[g]["request_ms"]["X-MAT32R"]["all"]["p95"]))
    measured = [g for g in GALLERIES if g in s and s[g]["verdict"] == "probes faster"]
    cross = {"measured": {"gallery": measured[0], "windows": s[measured[0]]["windows"]} if measured else None}
    if len(pts) >= 2:
        N = np.array([p[0] for p in pts], float)
        pp, xm = np.array([p[1] for p in pts]), np.array([p[2] for p in pts])
        b, a = np.polyfit(N, xm, 1)                               # X-MAT32R p95 = a + b N
        d, logc = np.polyfit(np.log(N), np.log(pp), 1)            # P-PG p95 = c N^d
        grid = np.logspace(np.log10(N.min()), 9, 4000)
        diff = np.exp(logc) * grid ** d - (a + b * grid)
        idx = np.flatnonzero(diff < 0)
        cross["extrapolation"] = {"X-MAT32R_p95_ms": f"{a:.3f} + {b * 1e6:.3f} per million windows",
                                  "P-PG_p95_ms": f"{np.exp(logc):.4g} * N^{d:.3f}",
                                  "crossover_windows": float(grid[idx[0]]) if len(idx) else None,
                                  "note": "extrapolated from fits to the measured galleries, not measured"}
    s["crossover"] = cross
    s["recall_flag_below_0.95"] = [g for g in GALLERIES if g in s and s[g]["recall_vs_X-PG"]["P-PG"]["mean"] < 0.95]
    (RES / "summary.json").write_text(json.dumps(s, indent=1) + "\n")
    for g in GALLERIES:
        if g in s:
            x = s[g]
            print(g, x["windows"], {a: round(x["request_ms"][a]["all"]["p95"], 1) for a in ARMS},
                  "recall P-PG", round(x["recall_vs_X-PG"]["P-PG"]["mean"], 3), "R", x["R = p95(P-PG) / p95(X-MAT32R)"], x["verdict"])
    print("crossover", json.dumps(cross))


if __name__ == "__main__":
    main()
