#!/usr/bin/env python3
"""F1 decision rule (PROTOCOL.md): each policy against recency, per budget, on the nearest-hit ratio.

Per query: d = recency's ratio - the policy's (positive = the policy finds something closer). Queries are averaged
within their group (workpiece for cycles, event for seismic rare queries, recording for typical queries), then over
groups; the 95% interval comes from 10,000 bootstrap resamples of groups (seed 20261105). An infinite ratio (nothing
searchable) counts as the largest finite ratio of the dataset plus 1.

  python3 evaluation/forgetting_f1/analyze.py results/f1_s02_*.json [--measure best_ratio]
"""
import json
import math
import random
import sys
from collections import defaultdict

SEED, RESAMPLES = 20261105, 10000


def known_key(run):
    return "nok_searchable" if "nok_searchable" in run else "known_searchable"


def compare(base, other, rare, measure, cap):
    def vals(run):
        return {p["query"]: (p["group"], min(p[measure], cap)) for p in run["per_query"] if p["rare"] == rare}
    a, b = vals(base), vals(other)
    groups = defaultdict(list)
    for q, (g, va) in a.items():
        if q in b:
            groups[g].append(va - b[q][1])
    means = [sum(v) / len(v) for v in groups.values()]
    if not means:
        return None
    rng = random.Random(SEED)
    boot = sorted(sum(rng.choice(means) for _ in means) / len(means) for _ in range(RESAMPLES))
    lo, hi = boot[int(0.025 * RESAMPLES)], boot[int(0.975 * RESAMPLES) - 1]
    return {"mean": sum(means) / len(means), "lo": lo, "hi": hi, "groups": len(means), "queries": sum(map(len, groups.values()))}


def main(path, measure="best_ratio"):
    d = json.load(open(path))
    finite = [p[measure] for r in d["runs"] for p in r["per_query"] if math.isfinite(p[measure])]
    cap = max(finite) + 1
    runs = {(r["policy"], r["budget"]): r for r in d["runs"]}
    out = []
    print(f"{d.get('dataset', d.get('scenario', path))}: {measure}, infinite counted as {cap:.3f}")
    print("budget  policy               kept   known  | rare: diff [95% CI] verdict          | typical: diff [95% CI]")
    for (pol, budget), r in sorted(runs.items(), key=lambda x: (-x[0][1], x[0][0])):
        if pol == "recency":
            continue
        base = runs[("recency", budget)]
        kk = known_key(r)
        rare = compare(base, r, True, measure, cap)
        typ = compare(base, r, False, measure, cap)
        reachable = r["fraction_kept"] <= budget + 0.005
        known_ok = (r[kk] or 0) >= (base[kk] or 0)
        if not reachable:
            verdict = "unreachable"
        elif rare is None:
            verdict = "no rare queries"
        elif rare["lo"] > 0 and known_ok:
            verdict = "better"
        elif rare["hi"] < 0:
            verdict = "worse"
        else:
            verdict = "no difference demonstrated"
        row = {"policy": pol, "budget": budget, "kept": r["fraction_kept"], "known": r[kk],
               "known_recency": base[kk], "rare": rare, "typical": typ, "verdict": verdict}
        out.append(row)
        f = lambda c: "n/a" if c is None else f"{c['mean']:+.3f} [{c['lo']:+.3f}, {c['hi']:+.3f}]"
        print(f"{budget:>5.0%}   {pol:<20} {r['fraction_kept']:>5.1%}  {(r[kk] or 0):>5.0%}  | {f(rare):<26} "
              f"{verdict:<17}| {f(typ)}")
    return out


if __name__ == "__main__":
    m = sys.argv[sys.argv.index("--measure") + 1] if "--measure" in sys.argv else "best_ratio"
    res = main(sys.argv[1], m)
    if "--json" in sys.argv:
        print(json.dumps(res, indent=1))
