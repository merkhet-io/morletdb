#!/usr/bin/env python3
"""I1 read investigation (exploratory, after B3.11): build versus write path versus noise.

Arms (results/i1r-*): rel-033 (old path, released image), b310-033 (old path, local image), b310-i1 (I1 path,
local image); three loads each, the read benchmark three times per load. For each query's p50: the median over
all nine rounds per arm, the spread within a load (max/min of its three rounds, median over loads) and between
loads (max/min of the loads' medians), and the ratios build = b310-033 / rel-033 and write = b310-i1 / b310-033.
"""
import json
import statistics
from pathlib import Path

R = Path(__file__).resolve().parent / "results"
ARMS = {"rel-033": "morletdb-033", "b310-033": "morletdb-033", "b310-i1": "morletdb-i1"}
QUERIES = ["R1", "R2", "R3", "R4_recent", "R4_older", "R5", "analytics"]


def rounds(arm, q):
    """p50 per round, grouped by load: [[r1, r2, r3] per load]."""
    out = []
    for rep in (1, 2, 3):
        d = json.loads((R / f"i1r-{arm}" / f"load_{ARMS[arm]}_D2_r{rep}.json").read_text())
        assert d["correct"], (arm, rep)
        if q == "analytics":
            xs = [d["analytics"]] + d["analytics_repeats"]
        else:
            xs = [d["reads"][q]] + [x[q] for x in d["reads_repeats"]]
        out.append([x["p50_ms"] for x in xs])
    return out


def main():
    print("| Query | rel-033 median | b310-033 median | b310-i1 median | build ratio | write-path ratio | "
          "within-load spread (rel / b310-033 / i1) | between-load spread (rel / b310-033 / i1) |")
    print("|---|---:|---:|---:|---:|---:|---|---|")
    res = {}
    for q in QUERIES:
        g = {a: rounds(a, q) for a in ARMS}
        med = {a: statistics.median([x for load in v for x in load]) for a, v in g.items()}
        within = {a: statistics.median([max(l) / min(l) for l in v]) for a, v in g.items()}
        between = {a: max(statistics.median(l) for l in v) / min(statistics.median(l) for l in v) for a, v in g.items()}
        res[q] = {"p50_rounds": g, "median": med, "build_ratio": med["b310-033"] / med["rel-033"],
                  "write_ratio": med["b310-i1"] / med["b310-033"], "within": within, "between": between}
        print(f"| {q} | {med['rel-033']:.3f} | {med['b310-033']:.3f} | {med['b310-i1']:.3f} | "
              f"{res[q]['build_ratio']:.3f} | {res[q]['write_ratio']:.3f} | "
              f"{within['rel-033']:.2f} / {within['b310-033']:.2f} / {within['b310-i1']:.2f} | "
              f"{between['rel-033']:.2f} / {between['b310-033']:.2f} / {between['b310-i1']:.2f} |")
    (R / "i1r-summary.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
