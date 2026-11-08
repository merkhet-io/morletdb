#!/usr/bin/env python3
"""L1-beta summary (PROTOCOL.md): medians over repeats, knees against each dimension's baseline point.

  python summarize.py RESULTS_DIR > summary.json
"""
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

BASELINE = {"d1": "d1-rate-64000", "d2": "d2-len-1e6", "d4": "d4-kind-adc", "d5-chunk": "d5-chunk-4096",
            "d5-writers": "d5-writers-1", "d7": "d2-len-1e8"}
ORDER = {"d1": ["d1-rate-1over60", "d1-rate-1over3", "d1-rate-1", "d1-rate-1000", "d1-rate-44100", "d1-rate-64000",
                "d1-rate-1000000"],
         "d2": ["d2-len-1e4", "d2-len-1e6", "d2-len-1e8", "d2-len-1e9"],
         "d5-chunk": ["d5-chunk-4096", "d5-chunk-64", "d5-chunk-1", "d5-chunk-65536"],
         "d5-writers": ["d5-writers-1", "d5-writers-4", "d5-writers-16"],
         "d7": ["d7-mem-2g", "d7-mem-512m", "d7-mem-256m", "d7-cpu-1", "d7-disk-full"]}
# metric -> True if higher is better
METRICS = {"ingest_samples_per_s": True, "read_samples_per_s": True, "read4096_middle_p50": False,
           "read4096_end_p50": False, "stats1e6_end_p50": False, "overview_whole_p50": False}


def flat(r):
    q = r.get("queries") or {}
    return {"ingest_samples_per_s": r.get("ingest_samples_per_s"), "read_samples_per_s": r.get("read_samples_per_s"),
            "read4096_middle_p50": (q.get("read4096_middle") or {}).get("p50_ms"),
            "read4096_end_p50": (q.get("read4096_end") or {}).get("p50_ms"),
            "stats1e6_end_p50": (q.get("stats1e6_end") or {}).get("p50_ms"),
            "overview_whole_p50": (q.get("overview_whole") or {}).get("p50_ms"),
            "bits_per_sample": r.get("bits_per_sample")}


def main():
    runs = defaultdict(list)
    for f in sorted(Path(sys.argv[1]).glob("*_r*.json")):
        r = json.loads(f.read_text())
        runs[r["point"]].append(r)
    med = {}
    for p, rs in runs.items():
        vals = [flat(r) for r in rs]
        med[p] = {"repeats": len(rs),
                  "failed": [r.get("error") or r.get("refusals") for r in rs if r.get("error") or r.get("refusals")],
                  "problems": sorted({x for r in rs for x in (r.get("problems") or [])}),
                  "memory_peak_mib": max((r.get("memory") or {}).get("cgroup_peak_mib") or 0 for r in rs),
                  "oom_kills": sum(((r.get("memory") or {}).get("oom_events") or 0) for r in rs),
                  **{k: (st.median([v[k] for v in vals if v[k] is not None]) if any(v[k] is not None for v in vals)
                         else None) for k in vals[0]},
                  "range": {k: [min(v[k] for v in vals if v[k] is not None), max(v[k] for v in vals if v[k] is not None)]
                            for k in vals[0] if any(v[k] is not None for v in vals)}}
    knees = {}
    for dim, order in ORDER.items():
        base = med.get(BASELINE[dim])
        knees[dim] = []
        for p in order:
            m = med.get(p)
            if m is None or base is None:
                continue
            why = []
            if m["failed"] or m["problems"] or m["oom_kills"]:
                why.append("error, refusal, problem or out-of-memory")
            for k, higher in METRICS.items():
                if m.get(k) is None or base.get(k) is None:
                    continue
                worse = base[k] / m[k] if higher else m[k] / base[k]
                if worse > 2:
                    why.append(f"{k} {worse:.1f}x worse than {BASELINE[dim]}")
            knees[dim].append({"point": p, "knee": bool(why), "why": why})
    print(json.dumps({"points": med, "knees": knees}, indent=1, default=str))


if __name__ == "__main__":
    main()
