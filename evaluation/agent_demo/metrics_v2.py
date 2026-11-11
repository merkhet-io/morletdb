#!/usr/bin/env python3
"""Version 2 metrics and the v1-against-v2 headline table (QUESTIONS.md, "Version 2").

  python evaluation/agent_demo/metrics_v2.py V1_RESULTS V2_RESULTS     (writes V2_RESULTS/metrics.json)
"""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def share(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 3) if xs else None


def precision(rs, part):
    return {"p@5": mean([r[part]["p@5"] for r in rs]), "p@10": mean([r[part]["p@10"] for r in rs]),
            "runs_with_5": sum(r[part]["p@5"] is not None for r in rs),
            "runs_with_10": sum(r[part]["p@10"] is not None for r in rs)}


def main():
    v1, v2 = Path(sys.argv[1]), Path(sys.argv[2])
    s1 = json.loads((v1 / "summary.json").read_text())
    s2 = json.loads((v2 / "summary.json").read_text())
    cal = json.loads((v2 / "calibration.json").read_text())
    done = [r for r in s2 if "top" in r]
    held = [r for r in cal["runs"].values() if r["role"] == "held_out" and "D" in r]
    calib = [r for r in cal["runs"].values() if r["role"] == "calibration" and "D" in r]
    th = cal["thresholds"]

    def fires(rows, level, op_key="op"):
        return share(r["D"] >= th[r[op_key]][level] for r in rows)

    m = {"investigations": len(s2), "completed": len(done),
         "stopped": [{"run": r["run"], "why": r.get("error")} for r in s2 if "top" not in r],
         "thresholds": {"pooled": cal["pooled"], "sources": dict(Counter(t["source"].split(" ")[0] for t in th.values()))},
         "runs": {"comparison": sum(r["role"] == "comparison" for r in cal["runs"].values()),
                  "calibration": len(calib), "held_out": len(held)}}
    # alignment over every run and axis that was aligned (calibration phase + flagged runs)
    status = Counter(st for r in cal["runs"].values() for _, st in r["onsets"].values())
    status_flagged = Counter(st for r in done for _, st in r["onsets"].values())
    m["alignment"] = {"good_run_axes": dict(status), "flagged_run_axes": dict(status_flagged),
                      "unclear_fallback": "onset 0 (raw positions)"}
    m["pool_top_in_flagged_run"] = {"share": share(r["pool_top_in_flagged"] for r in done),
                                    "chance": mean([r["flagged_share_of_scored_windows"] for r in done])}
    m["top_window_not_at_sample_0"] = sum(r["top"]["start"] != 0 for r in done)
    m["top_window_not_aligned_window_0"] = sum(r["top"]["window"] != 0 for r in done)
    m["firing"] = {
        "flagged": {"check": share(r["top"]["abs_db"] >= th[r["operation"]]["check"] for r in done),
                    "inspect": share(r["top"]["abs_db"] >= th[r["operation"]]["inspect"] for r in done)},
        "held_out_good": {"check": fires(held, "check"), "inspect": fires(held, "inspect")},
        "calibration_good_in_sample": {"check": fires(calib, "check"), "inspect": fires(calib, "inspect")}}
    m["firing_v1_rule"] = {
        "flagged": {"check_3db": share(r["top"]["abs_db"] >= 3 for r in s1), "inspect_6db": share(r["top"]["abs_db"] >= 6 for r in s1)},
        "held_out_good": {"check_3db": share(r["D_raw"] >= 3 for r in held if r.get("D_raw") is not None),
                          "inspect_6db": share(r["D_raw"] >= 6 for r in held if r.get("D_raw") is not None)}}

    def kind(r):
        level = r["D"] >= th[r["op"]]["check"]
        shape = r.get("S") is not None and r["S"] >= th[r["op"]]["shape"]
        return "level and shape" if level and shape else "level only" if level else "shape only" if shape else "neither"
    m["level_shape"] = {"flagged": dict(Counter(r["kind"] for r in done)), "held_out_good": dict(Counter(kind(r) for r in held))}
    for key in ("search_similar", "search_from_time"):
        rs = [r[key] for r in done if r.get(key)]
        m[key] = {"all": precision(rs, "all"), "same_period": precision(rs, "same_period"),
                  "other_periods": precision(rs, "other_periods"),
                  "bad_runs_top10_mean": mean([x["bad_runs_top10"] for x in rs]),
                  "bad_runs_top10_other_periods_mean": mean([x["bad_runs_top10_other_periods"] for x in rs]),
                  "own_run_hits_dropped_mean": mean([x["own_run_hits_dropped"] for x in rs])}
    m["chance"] = {k: mean([r["chance"][k] for r in done]) for k in ("all", "same_period", "other_periods")}
    st, sev = sum(r["statements"] for r in s2), sum(r["statements_with_evidence"] for r in s2)
    m["statements"] = {"total": st, "with_evidence": sev, "share": round(sev / st, 3) if st else None}
    walls = [r["wall_s"] for r in s2]
    m["wall_s"] = {"median": round(statistics.median(walls), 2), "max": max(walls),
                   "calibration_once": cal["wall_s"], "calibration_tool_calls": cal["tool_calls"]}
    m["tool_calls_per_investigation_median"] = statistics.median(r["tool_calls"] for r in s2)
    errs = [e for r in s2 for e in r["tool_errors"]] + cal["tool_errors"]
    m["tool_errors"] = {"total": len(errs), "messages": dict(Counter(f"{e['tool']}: {e['error'][:200]}" for e in errs))}
    ev = v2 / "evidence_check.json"
    if ev.exists():
        e = json.loads(ev.read_text())
        m["evidence_bit_identical"] = {"checked": e["investigations"], "identical": e["bit_identical"]}
    # v1 numbers for the table
    m1 = json.loads((v1 / "metrics.json").read_text())
    same = other = 0
    for r in s1:
        for h in r["search_similar"]["top"][:5]:
            if h["label"] == "bad":
                same += r["period"] in h["recording_id"]
                other += r["period"] not in h["recording_id"]
    m["v1"] = {"pool_top_in_flagged": m1["pool_top_in_flagged_run"]["share"],
               "top_window_not_at_sample_0": sum(r["top"]["start"] != 0 for r in s1),
               "p@5": m1["search_similar"]["precision_at_5"], "p@10": m1["search_similar"]["precision_at_10"],
               "bad_hits_top5_same_period": same, "bad_hits_top5_other_period": other,
               "evidence_share": m1["statements"]["share"], "bit_identical": m1.get("evidence_bit_identical"),
               "wall_median_s": m1["wall_s"]["median"]}
    (v2 / "metrics.json").write_text(json.dumps(m, indent=1) + "\n")
    print(json.dumps(m, indent=1))


if __name__ == "__main__":
    main()
