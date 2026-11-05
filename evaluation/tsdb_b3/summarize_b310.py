#!/usr/bin/env python3
"""B3.10 summary: morletdb-i1 against QuestDB and against the released MorletDB (morletdb-033), one session.

  python3 evaluation/tsdb_b3/summarize_b310.py            # markdown
  python3 evaluation/tsdb_b3/summarize_b310.py --json

Statements by B3's rule: ingestion needs >= 10% in all loads (every load of the faster system against every load
of the slower); reads and analytics (D2 r1) need >= 10% at both p50 and p95. Acceptance criteria as frozen in
PROTOCOL.md, Amendment B3.10.
"""
import json
import sys
from pathlib import Path

from summarize_b39 import R, QUERIES, j, start_load_averages, verdict

RUN = "b310"
ARMS = {"morletdb-i1": ("reads", "analytics"), "questdb": ("reads_native", "analytics"),
        "morletdb-033": ("reads", "analytics")}


def loads(system, d):
    out = [j(R / RUN / f"load_{system}_{d}_r{r}.json") for r in (1, 2, 3)]
    return out


def ingest_statement(a, b):
    """a, b: lists of samples/s. Ratio of medians and the all-loads bound (faster's min over slower's max)."""
    ma, mb = sorted(a)[1], sorted(b)[1]
    bound = min(a) / max(b) if ma > mb else min(b) / max(a)
    who = "first" if ma > mb else "second"
    return {"ratio_of_medians": ma / mb, "all_loads_bound": bound,
            "statement": f"{who} faster" if bound >= 1.1 else "no statement"}


def main():
    res = {"run": RUN, "arms": {}, "criteria": {}}
    for s, (rk, ak) in ARMS.items():
        a = {"ingest": {}, "memory_peak_mib": [], "correct": []}
        for d in ("D2", "D1"):
            ls = loads(s, d)
            a["ingest"][d] = [x["ingest"]["samples_per_s"] for x in ls]
            a["memory_peak_mib"] += [x["ingest"]["server"]["memory_peak_mib"] for x in ls]
            a["correct"] += [x["correct"] for x in ls]
            if s != "questdb":
                a.setdefault("chunks", {})[d] = [x["ingest"]["chunks"] for x in ls]
        r1 = loads(s, "D2")[0]
        a["reads"] = {q: [r1[rk][q]["p50_ms"], r1[rk][q]["p95_ms"]] for q in QUERIES}
        a["R5_qps"] = r1[rk]["R5"]["queries_per_s"]
        a["R5_p50_p95"] = [r1[rk]["R5"]["p50_ms"], r1[rk]["R5"]["p95_ms"]]
        a["analytics"] = [r1[ak]["p50_ms"], r1[ak]["p95_ms"]]
        a["storage"] = r1.get("storage") or r1.get("storage_native")
        res["arms"][s] = a
    i1, q, m0 = (res["arms"][k] for k in ("morletdb-i1", "questdb", "morletdb-033"))
    res["ingest"] = {d: {"i1_vs_questdb": ingest_statement(i1["ingest"][d], q["ingest"][d]),
                         "i1_vs_033": ingest_statement(i1["ingest"][d], m0["ingest"][d]),
                         "questdb_vs_033": ingest_statement(q["ingest"][d], m0["ingest"][d])} for d in ("D2", "D1")}
    res["reads_i1_vs_questdb"] = {k: verdict(i1["reads"][k], q["reads"][k]) for k in QUERIES}
    res["reads_i1_vs_questdb"]["analytics"] = verdict(i1["analytics"], q["analytics"])
    res["criteria"]["1_all_correct"] = all(i1["correct"] + q["correct"] + m0["correct"])
    res["criteria"]["2_chunks_identical"] = all(a == b for d in ("D2", "D1")
                                                for a, b in zip(i1["chunks"][d], m0["chunks"][d]))
    ratios = {k: i1["reads"][k][0] / m0["reads"][k][0] for k in QUERIES}
    ratios["R5"] = i1["R5_p50_p95"][0] / m0["R5_p50_p95"][0]
    ratios["analytics"] = i1["analytics"][0] / m0["analytics"][0]
    res["criteria"]["3_p50_vs_033"] = {k: round(v, 3) for k, v in ratios.items()}
    res["criteria"]["3_reads_within_1.1"] = all(v <= 1.1 for v in ratios.values())
    la = start_load_averages(RUN)
    res["load_average_before_load"] = {"max": max(la), "median": sorted(la)[len(la) // 2], "n": len(la)} if la else None
    if "--json" in sys.argv:
        print(json.dumps(res, indent=1))
        return
    f = lambda xs: f"{sorted(xs)[1] / 1e6:.2f} M [{min(xs) / 1e6:.2f}–{max(xs) / 1e6:.2f}]"
    print("| | D2 | D1 | memory, median peak |\n|---|---:|---:|---:|")
    for s, a in res["arms"].items():
        mem = sorted(a["memory_peak_mib"])[len(a["memory_peak_mib"]) // 2]
        print(f"| {s} | {f(a['ingest']['D2'])} | {f(a['ingest']['D1'])} | {mem:.0f} MiB |")
    for d in ("D2", "D1"):
        for k, v in res["ingest"][d].items():
            print(f"{d} {k}: ratio {v['ratio_of_medians']:.2f}, all-loads bound {v['all_loads_bound']:.2f}, "
                  f"{v['statement']}")
    print("\n| Query p50 / p95 ms | i1 | QuestDB | 033 | i1 vs QuestDB |\n|---|---:|---:|---:|---|")
    for k in QUERIES:
        print(f"| {k} | {i1['reads'][k][0]:.2f} / {i1['reads'][k][1]:.2f} | {q['reads'][k][0]:.2f} / {q['reads'][k][1]:.2f}"
              f" | {m0['reads'][k][0]:.2f} / {m0['reads'][k][1]:.2f} | {res['reads_i1_vs_questdb'][k]} |")
    print(f"| R5 q/s | {i1['R5_qps']:,.0f} | {q['R5_qps']:,.0f} | {m0['R5_qps']:,.0f} | |")
    print(f"| analytics | {i1['analytics'][0]:.2f} / {i1['analytics'][1]:.2f} | {q['analytics'][0]:.2f} / "
          f"{q['analytics'][1]:.2f} | {m0['analytics'][0]:.2f} / {m0['analytics'][1]:.2f} | "
          f"{res['reads_i1_vs_questdb']['analytics']} |")
    print("\ncriteria:", json.dumps(res["criteria"]))
    print("load average before loads:", res["load_average_before_load"])


if __name__ == "__main__":
    main()
