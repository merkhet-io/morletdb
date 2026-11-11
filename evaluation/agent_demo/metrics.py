#!/usr/bin/env python3
"""Metrics over the investigations (QUESTIONS.md), from demo_agent.py's output.

  python evaluation/agent_demo/metrics.py RESULTS_DIR      (writes RESULTS_DIR/metrics.json)
"""
import gzip
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def main():
    res = Path(sys.argv[1])
    runs = json.loads((res / "summary.json").read_text())
    done = [r for r in runs if "top" in r]
    m = {"investigations": len(runs), "completed": len(done),
         "stopped": [{"run": r["run"], "why": r.get("error")} for r in runs if "top" not in r]}
    m["pool_top_in_flagged_run"] = {"share": mean([float(r["pool_top_in_flagged"]) for r in done]),
                                    "n": len(done), "hits": sum(r["pool_top_in_flagged"] for r in done),
                                    "chance": mean([r["flagged_share_of_scored_windows"] for r in done])}
    for key in ("search_similar", "search_from_time"):
        rs = [r[key] for r in done if r.get(key)]
        m[key] = {"runs": len(rs), "precision_at_5": mean([x["p@5"] for x in rs]),
                  "precision_at_10": mean([x["p@10"] for x in rs]),
                  "runs_with_10_scored_hits": sum(1 for x in rs if x["p@10"] is not None),
                  "own_run_hits_dropped_mean": mean([x["own_run_hits_dropped"] for x in rs]),
                  "own_run_hits_dropped_max": max((x["own_run_hits_dropped"] for x in rs), default=None),
                  "hits_returned_mean": mean([x["hits"] for x in rs])}
    m["chance_bad_window_share"] = mean([r["chance"] for r in done if "chance" in r])
    st, sev = sum(r["statements"] for r in runs), sum(r["statements_with_evidence"] for r in runs)
    m["statements"] = {"total": st, "with_evidence": sev, "share": round(sev / st, 3) if st else None}
    walls = [r["wall_s"] for r in runs]
    m["wall_s"] = {"median": round(statistics.median(walls), 2), "max": max(walls), "min": min(walls),
                   "total": round(sum(walls), 1)}
    calls = [r["tool_calls"] for r in runs]
    m["tool_calls_per_investigation"] = {"median": statistics.median(calls), "max": max(calls)}
    errs = [e for r in runs for e in r["tool_errors"]]
    m["tool_errors"] = {"total": len(errs), "by_tool": dict(Counter(e["tool"] for e in errs)),
                        "messages": dict(Counter(f"{e['tool']}: {e['error'][:200]}" for e in errs))}
    m["kind"] = dict(Counter(r.get("kind") for r in done))
    m["top_abs_db"] = {"median": round(statistics.median(r["top"]["abs_db"] for r in done), 2) if done else None}
    m["recommendations"] = dict(Counter(r["recommendation"].split(";")[0].split(" at ")[0] for r in done
                                        if "recommendation" in r))
    # Control: the same window rule applied to the pools' good runs (leave-one-out against the other good runs).
    # How often would "inspect" (>= 6 dB) or "check" (>= 3 dB) fire on a good run?
    good_max, seen = [], set()
    for r in done:
        doc = json.load(gzip.open(res / "transcripts" / f"{r['run']}.json.gz", "rt"))
        power = {}
        for c in doc["calls"]:
            if c["tool"] == "describe_interval" and "result" in c:
                x = c["result"]
                run, ax = x["recording_id"].rsplit("-", 1)
                power[(run, ax, x["start_sample"] // 4096)] = x["std"] ** 2
        pool_good = r["pool"][1:]
        for g in pool_good:
            if g in seen:                      # the same good run appears in several pools of its operation
                continue
            seen.add(g)
            ds = []
            for (run, ax, i), p in power.items():
                if run != g:
                    continue
                refs = [power[(s, ax, i)] for s in pool_good if s != g and (s, ax, i) in power]
                if len(refs) >= 3 and p > 0 and statistics.median(refs) > 0:
                    ds.append(abs(10 * math.log10(p / statistics.median(refs))))
            if ds:
                good_max.append(max(ds))
    m["control_good_runs"] = {
        "runs": len(good_max), "median_top_abs_db": round(statistics.median(good_max), 2) if good_max else None,
        "share_ge_6db_inspect": round(sum(d >= 6 for d in good_max) / len(good_max), 3) if good_max else None,
        "share_ge_3db_check": round(sum(d >= 3 for d in good_max) / len(good_max), 3) if good_max else None,
        "note": "good runs scored against the other good runs of their pool (4 references, not 5)"}
    flagged_max = [r["top"]["abs_db"] for r in done]
    m["flagged_runs_top_abs_db"] = {"share_ge_6db": round(sum(d >= 6 for d in flagged_max) / len(flagged_max), 3),
                                    "share_ge_3db": round(sum(d >= 3 for d in flagged_max) / len(flagged_max), 3)}
    # Are the bad search hits from the same period (a batch of bad parts) or from other periods?
    same = other = 0
    for r in done:
        period = r["period"]
        for h in r["search_similar"]["top"][:5]:
            if h["label"] == "bad":
                same += period in h["recording_id"]
                other += period not in h["recording_id"]
    m["search_similar"]["bad_hits_in_top5_same_period"] = same
    m["search_similar"]["bad_hits_in_top5_other_period"] = other
    ev = res / "evidence_check.json"
    if ev.exists():
        e = json.loads(ev.read_text())
        m["evidence_bit_identical"] = {"checked": e["investigations"], "identical": e["bit_identical"]}
    (res / "metrics.json").write_text(json.dumps(m, indent=1) + "\n")
    print(json.dumps(m, indent=1))


if __name__ == "__main__":
    main()
