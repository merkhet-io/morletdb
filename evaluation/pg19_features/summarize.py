#!/usr/bin/env python3
"""Tables from the result files of same_session.py and notify_check.py (Markdown on stdout).

  python evaluation/pg19_features/summarize.py results/mac_same_session.json results/mac_notify.json ...
"""
import json
import statistics
import sys

TASKS = ["load_s", "envelopes_s", "full_read", "random_reads", "search", "latest", "verify"]
LABEL = {"load_s": "load (append)", "envelopes_s": "build envelopes", "full_read": "read every sample",
         "random_reads": "random reads", "search": "exact search, 20 queries", "latest": "latest",
         "verify": "verify"}


def same_session(r):
    servers = list(dict.fromkeys(x["server"] for x in r["rounds"]))
    vals = {s: {t: [] for t in TASKS} for s in servers}
    for x in r["rounds"]:
        for t in TASKS:
            v = x[t] if t.endswith("_s") else x["tasks"][t]["s"]
            vals[x["server"]][t].append(v)
    base = servers[0]
    last = servers[-1]
    head = "| Task | " + " | ".join(f"{s} median (min–max), s" for s in servers) + f" | {last} / {base} |"
    print(head)
    print("|---|" + "---:|" * (len(servers) + 1))
    for t in TASKS:
        cells = [f"{statistics.median(vals[s][t]):.3f} ({min(vals[s][t]):.3f}–{max(vals[s][t]):.3f})" for s in servers]
        ratio = statistics.median(vals[last][t]) / statistics.median(vals[base][t])
        print(f"| {LABEL[t]} | " + " | ".join(cells) + f" | {ratio:.2f} |")
    n = len(r["rounds"]) // len(servers)
    print(f"\n{n} rounds per server; {r['recordings']} recordings, {r['data']['samples']:,} samples; "
          f"results identical in every run: {r.get('identical_results')}")
    for s, st in r["servers"].items():
        print(f"- {s}: {st['version'].split(' on ')[0]}, jit={st['jit']}, shared_memory_size={st['shared_memory_size']}")


def notify(r):
    rows = {}
    for x in r["runs"]:
        rows.setdefault((x["server"], x["listeners"]), []).append(x)
    print("| Server | Idle listeners | Commits/s, median (min–max) | p99 latency, ms | postgres CPU s per 1,000 appends |")
    print("|---|---:|---:|---:|---:|")
    for (s, k), xs in sorted(rows.items()):
        cps = [x["commits_per_s"] for x in xs]
        p99 = statistics.median(x["latency_ms"]["p99"] for x in xs)
        cpu = statistics.median(x["postgres_cpu_s"] / x["appends"] * 1000 for x in xs)
        print(f"| {s} | {k} | {statistics.median(cps):.0f} ({min(cps):.0f}–{max(cps):.0f}) | {p99:.2f} | {cpu:.1f} |")
    print("\nCPU from `ps` (macOS: hundredths of a second per process; Linux: whole seconds, so coarse).")


for path in sys.argv[1:]:
    r = json.load(open(path))
    print(f"\n### {path}\n")
    if "rounds" in r:
        same_session(r)
    elif "runs" in r:
        notify(r)
