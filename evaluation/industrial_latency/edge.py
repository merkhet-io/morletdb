"""IU1-E: search latency in one configuration. Usage: python edge.py <config-name>   (see EDGE.md)"""
import json, os, sys, time
import numpy as np, psycopg
from pathlib import Path
DSN = os.environ["DATABASE_URL"]; OUT = Path("/spike/results"); NAME = sys.argv[1]
C, TL, WIN, BL, SEED = "public.bosch", "public.toollife", 4096, 0.075, 20261111
W = json.loads((OUT / "windows.json").read_text())
rng = np.random.default_rng(SEED)
conf = [w for w in W if w[1] in ("M02", "M03")]
q1 = [conf[i] for i in rng.choice(len(conf), 200, replace=False)]
wx = [w for w in W if w[4] == "x"]; q2 = [wx[i] for i in rng.choice(len(wx), 100, replace=False)]
runs = sorted({w[0] for w in conf}); q4 = [runs[i] for i in rng.choice(len(runs), 30, replace=False)]
starts = {}
for run, m, op, label, ax, s, v in W:
    if run in q4: starts.setdefault((run, ax), []).append(s)
meta = {w[0]: (w[1], w[2]) for w in W}
def pick(ss, k=8):
    ss = sorted(ss); return ss if len(ss) <= k else [ss[i] for i in sorted(set(int(round(i)) for i in np.linspace(0, len(ss) - 1, k)))]
res = {}
def pct(v): return [float(np.percentile(v, 50)), float(np.percentile(v, 95))]
with psycopg.connect(DSN, autocommit=True) as c:
    def timed(sql, args):
        t = time.perf_counter(); c.execute(sql, args).fetchall(); return (time.perf_counter() - t) * 1000
    S = "SELECT recording_id,start_sample,distance FROM morlet_search_envelope_interval(%s,%s,%s,%s,%s::jsonb,%s,%s)"
    P = "SELECT recording_id,start_sample,distance FROM morlet_search_envelope_probes(%s,%s,%s,%s,%s::jsonb,%s,%s)"
    cyc = json.loads(Path("/w/iu2/results/cycles.json").read_text())["cycles"]; ln = json.loads(Path("/w/iu2/results/load.json").read_text())["lengths"]
    tq = [x for x in cyc if not x["baseline"] and x["split"] == "conf"]; tq = [tq[i] for i in rng.choice(len(tq), 100, replace=False)]
    for rnd in (1, 2):
        e1 = [timed(S, (C, f"{r}_{ax}", s, s + WIN, json.dumps({"machine": m, "op": op, "axis": ax, "label": "good"}), 40, BL)) for r, m, op, _, ax, s, _ in q1]
        e2x = [timed(S, (C, f"{r}_x", s, s + WIN, "{}", 10, BL)) for r, m, op, _, ax, s, _ in q2[:50]]
        e2p = [timed(P, (C, f"{r}_x", s, s + WIN, "{}", 10, BL)) for r, m, op, _, ax, s, _ in q2[50:]]
        e3 = [timed("SELECT * FROM morlet_interval_stats(%s,%s,%s,%s)", (C, f"{r}_{ax}", s, s + WIN)) for r, m, op, _, ax, s, _ in q1]
        e4 = []
        for r in q4:
            m, op = meta[r]; t = time.perf_counter()
            for ax in ("x", "y", "z"):
                for s in pick(starts[(r, ax)]):
                    c.execute(S, (C, f"{r}_{ax}", s, s + WIN, json.dumps({"machine": m, "op": op, "axis": ax, "label": "good"}), 40, BL)).fetchall()
            e4.append((time.perf_counter() - t) * 1000)
        e5 = []
        for x in tq:
            n = ln[x["file"]]; s = ((n // 2) // 2048) * 2048
            e5.append(timed("SELECT distance FROM morlet_search_envelope_interval(%s,%s,%s,%s,%s::jsonb,1,%s)",
                            (TL, f"{x['file']}_x", s, s + WIN, json.dumps({"tool": x["tool"], "cpos": x["cpos"], "axis": "x", "baseline": True}), BL)))
        res[f"round{rnd}"] = dict(E1_filtered_search=pct(e1), E2_exact_85k=pct(e2x), E2_probes_85k=pct(e2p), E3_window_stats=pct(e3),
                                  E4_decision_per_run_24_searches=pct(e4), E5_toollife_one_cycle=pct(e5))
        print(NAME, "round", rnd, {k: [round(x, 1) for x in v] for k, v in res[f"round{rnd}"].items()}, flush=True)
    res["settings"] = {k: c.execute("SELECT current_setting(%s)", (k,)).fetchone()[0] for k in ("shared_buffers", "work_mem", "max_parallel_workers_per_gather", "max_connections")}
(OUT / f"edge_{NAME}.json").write_text(json.dumps(res, indent=1))
