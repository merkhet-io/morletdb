"""C4 re-analysis (RESULTS.md, C4): the stored c4.json is kept unchanged. This script recomputes, from the raw
writer and client logs, (1) the ingest rate over the time the writers actually ran (summarize() divided by the
scheduled 7,200 s although the run stopped earlier), and (2) all C4 metrics up to the stall that preceded the
abort (no writer, client or monitor record between 13:04:26 and 13:05:57 CET).

  python3 evaluation/pilot/c4_reanalysis.py evaluation/pilot/results
"""
import json
import sys
from pathlib import Path

import numpy as np

STALL_START = 1793534658.0     # 2026-11-01 13:04:18 CET, last monitor sample before the stall


def pct(v):
    return {"n": len(v), "p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95)),
            "p99": float(np.percentile(v, 99)), "max": float(np.max(v))} if v else {"n": 0}


def main(results):
    results = Path(results)
    logs = results / "c4_logs"
    w = [json.loads(l) for f in sorted(logs.glob("w*.jsonl")) for l in f.read_text().splitlines()]
    q = [json.loads(l) for f in sorted(logs.glob("c*.jsonl")) for l in f.read_text().splitlines()]
    c4 = json.loads((results / "c4.json").read_text())
    t0, t1 = min(x["t"] for x in w), max(x["t"] for x in w)
    out = {"writers_first_t": t0, "writers_last_t": t1, "elapsed_s": t1 - t0,
           "rate_over_elapsed": sum(x["samples"] for x in w) / (t1 - t0)}
    ww = [x for x in w if x["t"] < STALL_START]
    qq = [x for x in q if x["t"] < STALL_START]
    tl = [s for s in c4["timeline"] if s["t"] <= STALL_START]
    rec = [x["recall"] for x in qq if x["recall"] is not None]
    out["before_stall"] = {
        "seconds": STALL_START - t0,
        "rate_samples_per_s": sum(x["samples"] for x in ww) / (STALL_START - t0),
        "append_txn_ms": pct([x["ms"] for x in ww]),
        "max_lag_s": max(x["lag_s"] for x in ww),
        "search_ms": {k: pct([x["ms"] for x in qq if x["kind"] == k]) for k in ("filtered", "unfiltered", "exact_unfiltered")},
        "recall": {"n": len(rec), "mean": float(np.mean(rec))},
        "errors": sum(1 for x in ww + qq if x.get("err")),
        "wal_bytes": tl[-1]["wal_bytes"], "db_growth_bytes": tl[-1]["db_bytes"] - c4["timeline"][0]["db_bytes"],
        "load_avg": {"mean": float(np.mean([s["load_avg"] for s in tl])), "max": max(s["load_avg"] for s in tl)},
        "max_writer_lag_by_hour": [max((s["max_writer_lag_s"] for s in tl if t0 + h * 3600 <= s["t"] < t0 + (h + 1) * 3600), default=None)
                                   for h in range(2)],
    }
    gaps = {}
    for f in sorted(logs.glob("*.jsonl")):
        ts = sorted(json.loads(l)["t"] for l in f.read_text().splitlines())
        g = max((ts[i + 1] - ts[i], ts[i]) for i in range(len(ts) - 1))
        gaps[f.name] = {"max_gap_s": g[0], "at": g[1]}
    out["largest_record_gap_per_process"] = gaps
    (results / "c4_reanalysis.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
