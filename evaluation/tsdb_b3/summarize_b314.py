#!/usr/bin/env python3
"""B3.14 summary: QuestDB pair with a build of main (parallel decode), and the build against the release in the same
session. B3's statement rule as in summarize_b313.py (ingestion: >= 10% in all loads; latencies: at p50 and p95).

  python3 evaluation/tsdb_b3/summarize_b314.py [--json]
"""
import json
import statistics
import sys

import summarize_b313 as B

QUERIES = B.QUERIES


def side(run, system, rk, ak):
    out = {"ingest": {}, "memory": None}
    allm = []
    for d in ("D2", "D1"):
        ls = B.loads(run, system, d)
        v = [x["ingest"]["samples_per_s"] for x in ls]
        out["ingest"][d] = {"median": statistics.median(v), "min": min(v), "max": max(v)}
        allm += [x["ingest"]["server"]["memory_peak_mib"] for x in ls]
    out["memory"] = statistics.median(allm)
    r1 = B.loads(run, system, "D2")[0]
    out["reads"] = {q: [r1[rk][q]["p50_ms"], r1[rk][q]["p95_ms"]] for q in QUERIES}
    out["R5_qps"] = r1[rk]["R5"]["queries_per_s"]
    out["analytics"] = [r1[ak]["p50_ms"], r1[ak]["p95_ms"]]
    out["server_reads"] = r1.get("server_reads")
    out["image_id"] = r1.get("image_id")
    return out


def compare(a, b, label_a, label_b):
    lines = [f"### {label_a} against {label_b}", "",
             "| | D2 ingestion | D1 ingestion | memory |", "|---|---:|---:|---:|"]
    f = lambda x: f"{x['median'] / 1e6:.2f} M [{x['min'] / 1e6:.2f}–{x['max'] / 1e6:.2f}]"
    lines.append(f"| {label_a} | {f(a['ingest']['D2'])} | {f(a['ingest']['D1'])} | {a['memory']:.0f} MiB |")
    lines.append(f"| {label_b} | {f(b['ingest']['D2'])} | {f(b['ingest']['D1'])} | {b['memory']:.0f} MiB |")
    for d in ("D2", "D1"):
        x, y = a["ingest"][d], b["ingest"][d]
        ratio = x["median"] / y["median"]
        bound = (x["min"] / y["max"]) if ratio > 1 else (y["min"] / x["max"])
        lines.append(f"| {d}: ratio of medians {ratio:.2f}, all-loads bound {bound:.2f} | | | |")
    lines += ["", f"| Query (p50 / p95 ms) | {label_a} | {label_b} | statement (> 1 = {label_a} faster) |",
              "|---|---:|---:|---|"]
    for q in QUERIES:
        lines.append(f"| {q} | {a['reads'][q][0]:.2f} / {a['reads'][q][1]:.2f} | {b['reads'][q][0]:.2f} / "
                     f"{b['reads'][q][1]:.2f} | {B.verdict(a['reads'][q], b['reads'][q]).replace('MorletDB', label_a).replace('other', label_b)} |")
    lines.append(f"| R5 queries/s | {a['R5_qps']:,.0f} | {b['R5_qps']:,.0f} | {a['R5_qps'] / b['R5_qps']:.2f}× |")
    lines.append(f"| interval statistics | {a['analytics'][0]:.2f} / {a['analytics'][1]:.2f} | {b['analytics'][0]:.2f} / "
                 f"{b['analytics'][1]:.2f} | {B.verdict(a['analytics'], b['analytics']).replace('MorletDB', label_a).replace('other', label_b)} |")
    return "\n".join(lines)


if __name__ == "__main__":
    build = side("b314-questdb", "morletdb-i1", "reads", "analytics")
    build_sum = side("b314-questdb", "morletdb-i1-sum", "reads", "analytics")
    rel = side("b314-released", "morletdb-i1", "reads", "analytics")
    qdb = side("b314-questdb", "questdb", "reads_native", "analytics")
    la = B.start_load_averages("b314-questdb") + B.start_load_averages("b314-released")
    if "--json" in sys.argv:
        print(json.dumps({"build": build, "build_sum": build_sum, "released": rel, "questdb": qdb,
                          "load_average": la}, indent=1))
    else:
        print("\n\n".join([compare(build, qdb, "MorletDB build", "QuestDB"),
                           compare(build_sum, qdb, "MorletDB build + summaries", "QuestDB"),
                           compare(build, rel, "MorletDB build", "MorletDB 0.3.6-alpha.1"),
                           f"1-minute load average before a load: median {statistics.median(la):.2f}, max {max(la):.2f} (n = {len(la)})"]))
