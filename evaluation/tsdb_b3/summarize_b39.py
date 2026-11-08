#!/usr/bin/env python3
"""B3.8 (quiet) and B3.9 summary: every measure of each same-session pair, with B3's statement rule.

  python3 evaluation/tsdb_b3/summarize_b39.py            # markdown tables
  python3 evaluation/tsdb_b3/summarize_b39.py --json     # the same numbers as JSON

Ingestion: median [range] of three loads; a statement needs >= 10% in all loads (every load of the faster system
against every load of the slower one). Reads and analytics: D2 r1 of each system; a statement needs >= 10% at both
p50 and p95. Memory: median peak server memory over all six loads of the system in the pair.
"""
import glob
import json
import re
import statistics
import sys
from pathlib import Path

R = Path(__file__).resolve().parent / "results"
M = "morletdb-033"
# run, system, label, reads key, analytics key
PAIRS = [
    ("b38q", "questdb", "QuestDB 10.1.0 (native)", "reads_native", "analytics"),
    ("b38z", "questdb", "QuestDB 10.1.0 (native), end of night", "reads_native", "analytics"),
    ("b39-tdengine", "tdengine-default", "TDengine 3.4.3.3 (default)", "reads", "analytics"),
    ("b39-timescale", "timescale", "TimescaleDB 2.31.0 (columnstore)", "reads_col", "analytics_col"),
    ("b39-timescale", "timescale", "TimescaleDB 2.31.0 (rowstore)", "reads_row", "analytics_row"),
    ("b39-iotdb", "iotdb-table", "IoTDB 2.0.11 (table)", "reads", "analytics"),
    ("b39-crate", "crate-array", "CrateDB 6.4.6 (array)", "reads", "analytics"),
    ("b39-crate-row", "crate-row", "CrateDB 6.4.6 (row)", "reads", "analytics"),
]
QUERIES = ["R1", "R2", "R3", "R4_recent", "R4_older"]


def j(p):
    return json.loads(Path(p).read_text())


def loads(run, system, d):
    fs = sorted(glob.glob(str(R / run / f"load_{system}_{d}_r*.json")))
    out = [j(f) for f in fs]
    if len(out) != 3 or not all(x["correct"] for x in out):
        sys.exit(f"{run} {system} {d}: expected three correct loads")
    return out


def start_load_averages(run):
    """1-minute load average recorded before each load (the first line of each snapshot pair)."""
    vals = []
    for f in glob.glob(str(R / run / "machine" / "*.txt")):
        snaps = re.findall(r"load averages?: ([\d.]+)", Path(f).read_text())
        vals += [float(v) for v in snaps[0::2]]
    return vals


def pair(run, system, label, rk, ak):
    res = {"run": run, "system": system, "label": label, "ingest": {}, "memory_mib": {}}
    for who in (M, system):
        allm = []
        for d in ("D2", "D1"):
            ls = loads(run, who, d)
            v = [x["ingest"]["samples_per_s"] for x in ls]
            res["ingest"].setdefault(who, {})[d] = {"median": statistics.median(v), "min": min(v), "max": max(v)}
            allm += [x["ingest"]["server"]["memory_peak_mib"] for x in ls]
        res["memory_mib"][who] = statistics.median(allm)
    for d in ("D2", "D1"):
        a, b = res["ingest"][M][d], res["ingest"][system][d]
        res["ingest"][d + "_ratio"] = a["median"] / b["median"]
        res["ingest"][d + "_all_loads"] = (a["min"] / b["max"]) if a["median"] > b["median"] else (b["min"] / a["max"])
    m1, o1 = loads(run, M, "D2")[0], loads(run, system, "D2")[0]
    res["reads"] = {}
    for q in QUERIES:
        res["reads"][q] = {"morletdb": [m1["reads"][q]["p50_ms"], m1["reads"][q]["p95_ms"]],
                           "other": [o1[rk][q]["p50_ms"], o1[rk][q]["p95_ms"]]}
    res["reads"]["R5_qps"] = {"morletdb": m1["reads"]["R5"]["queries_per_s"], "other": o1[rk]["R5"]["queries_per_s"]}
    res["analytics"] = {"morletdb": [m1["analytics"]["p50_ms"], m1["analytics"]["p95_ms"]],
                        "other": [o1[ak]["p50_ms"], o1[ak]["p95_ms"]]}
    la = start_load_averages(run)
    res["load_average_before_load"] = {"max": max(la), "median": statistics.median(la), "n": len(la)} if la else None
    return res


def verdict(m, o):
    """Statement by B3's rule for latencies: >= 10% at p50 and p95."""
    r50, r95 = o[0] / m[0], o[1] / m[1]
    if r50 >= 1.1 and r95 >= 1.1:
        return f"MorletDB {r50:.1f}× / {r95:.1f}× faster"
    if r50 <= 1 / 1.1 and r95 <= 1 / 1.1:
        return f"other {1 / r50:.1f}× / {1 / r95:.1f}× faster"
    return f"no statement ({r50:.2f} / {r95:.2f})"


def md(p):
    i, M_ = p["ingest"], "morletdb-033"
    s = p["system"]
    f = lambda x: f"{x['median'] / 1e6:.2f} M [{x['min'] / 1e6:.2f}–{x['max'] / 1e6:.2f}]"
    out = [f"### {p['label']} (`results/{p['run']}/`)", "",
           "| | D2 | D1 | memory (median peak) |", "|---|---:|---:|---:|",
           f"| MorletDB | {f(i[M_]['D2'])} | {f(i[M_]['D1'])} | {p['memory_mib'][M_]:.0f} MiB |",
           f"| {p['label']} | {f(i[s]['D2'])} | {f(i[s]['D1'])} | {p['memory_mib'][s]:.0f} MiB |",
           f"| ratio of medians (all-loads bound) | {i['D2_ratio']:.2f} ({i['D2_all_loads']:.2f}) | "
           f"{i['D1_ratio']:.2f} ({i['D1_all_loads']:.2f}) | |", "",
           "| Query (p50 / p95 ms) | MorletDB | other | statement |", "|---|---:|---:|---|"]
    for q in QUERIES:
        r = p["reads"][q]
        out.append(f"| {q} | {r['morletdb'][0]:.2f} / {r['morletdb'][1]:.2f} | {r['other'][0]:.2f} / {r['other'][1]:.2f} | "
                   f"{verdict(r['morletdb'], r['other'])} |")
    q5 = p["reads"]["R5_qps"]
    out.append(f"| R5, 4 threads, queries/s | {q5['morletdb']:,.0f} | {q5['other']:,.0f} | "
               f"{q5['morletdb'] / q5['other']:.1f}× |")
    a = p["analytics"]
    out.append(f"| interval statistics | {a['morletdb'][0]:.2f} / {a['morletdb'][1]:.2f} | {a['other'][0]:.2f} / "
               f"{a['other'][1]:.2f} | {verdict(a['morletdb'], a['other'])} |")
    la = p["load_average_before_load"]
    if la:
        out += ["", f"1-minute load average before a load: median {la['median']:.1f}, max {la['max']:.1f} (n = {la['n']})"]
    return "\n".join(out)


if __name__ == "__main__":
    ps = [pair(*x) for x in PAIRS]
    print(json.dumps(ps, indent=1) if "--json" in sys.argv else "\n\n".join(md(p) for p in ps))
