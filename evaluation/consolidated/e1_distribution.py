"""Post-hoc description of E1's stored results (no new measurement): per-query recall distributions, missed
reference items (rank in the exact list, best lag, whether an overlapping window of the same recording took
their place), and per-round latency statistics.

  python3 evaluation/consolidated/e1_distribution.py evaluation/consolidated/results
"""
import collections
import json
import sys
from pathlib import Path

W = 4096


def p95(xs):
    xs = sorted(xs)
    return xs[max(0, round(0.95 * (len(xs) - 1)))]


def describe(path):
    d = json.loads(path.read_text())
    per = d["per_query"]
    out = {"queries": len(per), "query_types": dict(collections.Counter(q["type"] for q in per.values())),
           "reference_sizes": dict(collections.Counter(len(q["top"]["EX-2"]) for q in per.values())),
           "aggregation": "mean over queries of |arm top-10 ∩ exact top-10| / 10", "recall_histogram": {}}
    for arm in ("PR", "Z-650", "Z-1000", "PX-61", "PX-13", "PQ-13", "PX-7", "EZ"):
        out["recall_histogram"][arm] = {str(k): v for k, v in sorted(collections.Counter(q["recall"][arm] for q in per.values()).items())}
    missed = []
    for key, q in per.items():
        ref = [tuple(x) for x in q["top"]["EX-2"]]
        got = [tuple(x) for x in q["top"]["PR"]]
        for i, (r, s) in enumerate(ref):
            if (r, s) not in got:
                overlap = any(gr == r and gs < s + W and gs + W > s and (gr, gs) not in ref for gr, gs in got)
                missed.append({"query": key, "type": q["type"], "rank_in_exact": i + 1, "best_lag": q["lstar"][i],
                               "replaced_by_overlapping_window": overlap})
    out["PR_missed"] = {"items": len(missed), "of": 10 * len(per),
                        "rank_histogram": dict(sorted(collections.Counter(m["rank_in_exact"] for m in missed).items())),
                        "replaced_by_overlapping_window": sum(m["replaced_by_overlapping_window"] for m in missed),
                        "worst_queries": [{"query": k, "type": per[k]["type"], "recall": per[k]["recall"]["PR"],
                                           "missed_ranks": [m["rank_in_exact"] for m in missed if m["query"] == k]}
                                          for k in sorted(per, key=lambda k: per[k]["recall"]["PR"])[:6]]}
    out["latency_by_round_ms"] = {arm: {f"round{r + 1}": {"p50": sorted(q["ms"][arm][r] for q in per.values())[49],
                                                         "p95": p95([q["ms"][arm][r] for q in per.values()])}
                                        for r in (0, 1)} for arm in ("EX-2", "EX-8", "PR", "Z-650", "Z-1000")}
    out["p95_ratio_strongest_exact_over_PR"] = d["summary"]["EX-8"]["latency_ms"]["p95"] / d["summary"]["PR"]["latency_ms"]["p95"]
    return out


def main(results):
    results = Path(results)
    res = {name: describe(results / f"e1_{name}.json") for name in ("heldout", "dev")}
    (results / "e1_distribution.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1)[:3000])


if __name__ == "__main__":
    main(sys.argv[1])
