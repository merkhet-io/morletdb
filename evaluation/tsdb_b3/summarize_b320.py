#!/usr/bin/env python3
"""B3.20 summary: MorletDB 0.3.10-alpha.1 and CedarDB v2026-11-04 with 4 and 8 parallel writers (PROTOCOL.md,
"Amendment B3.20"), by B3's statement rule.   python summarize_b320.py results/b320-parallel"""
import glob, json, os, re, statistics as st, sys

d = sys.argv[1]
cells = {}
for f in sorted(glob.glob(os.path.join(d, "parallel_*.json"))):
    sysname, dur, w, r = re.match(r"parallel_(.+)_(D\d)_w(\d+)_r(\d+)\.json", os.path.basename(f)).groups()
    x = json.load(open(f)); ing = x.get("ingest", x); srv = ing.get("server", {})
    cells.setdefault((dur, int(w)), {}).setdefault(sysname, []).append(
        {"sps": ing["samples_per_s"] / 1e6, "ok": x.get("correct"), "cpu": srv.get("cpu_percent_mean"),
         "ws": (srv.get("kernel_memory") or {}).get("working_set_peak_mib_50ms")})
print("| Cell | MorletDB, median [range] M samples/s | CedarDB | Statement | Server CPU (M / C) | Working set peak (M / C) |")
print("|---|---|---|---|---|---|")
for (dur, w), v in sorted(cells.items(), key=lambda k: (k[0][0] != "D2", k[0][1])):
    m, c = v["morletdb-i1"], v["cedardb"]
    if not all(x["ok"] for x in m + c):
        print(f"| {dur}, {w} writers | correctness failed: no statement |"); continue
    ms, cs = [x["sps"] for x in m], [x["sps"] for x in c]
    if min(ms) >= 1.1 * max(cs):
        stm = f"MorletDB **{st.median(ms) / st.median(cs):.1f}×** faster (all-loads bound {min(ms) / max(cs):.2f})"
    elif min(cs) >= 1.1 * max(ms):
        stm = f"CedarDB **{st.median(cs) / st.median(ms):.1f}×** faster (all-loads bound {min(cs) / max(ms):.2f})"
    else:
        stm = "no statement"
    med = lambda xs, k: st.median([x[k] or 0 for x in xs])
    print(f"| {dur}, {w} writers | {st.median(ms):.2f} [{min(ms):.2f}–{max(ms):.2f}] | {st.median(cs):.2f} "
          f"[{min(cs):.2f}–{max(cs):.2f}] | {stm} | {med(m, 'cpu'):.0f}% / {med(c, 'cpu'):.0f}% | "
          f"{med(m, 'ws'):,.0f} / {med(c, 'ws'):,.0f} MiB |")
n = sum(len(s) for v in cells.values() for s in v.values()); ok = sum(x["ok"] is True for v in cells.values() for s in v.values() for x in s)
print(f"\nCorrectness: {ok} of {n} loads passed (every channel by SHA-256, exact times).")
