"""Writes the exact queries of edge.py (same seed and draw order) to results/edge_queries.json. No database needed."""
import json
import numpy as np
from pathlib import Path
OUT = Path("/spike/results"); WIN, SEED = 4096, 20261111
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
cyc = json.loads(Path("/w/iu2/results/cycles.json").read_text())["cycles"]; ln = json.loads(Path("/w/iu2/results/load.json").read_text())["lengths"]
tq = [x for x in cyc if not x["baseline"] and x["split"] == "conf"]; tq = [tq[i] for i in rng.choice(len(tq), 100, replace=False)]
f = lambda m, op, ax: {"machine": m, "op": op, "axis": ax, "label": "good"}
Q = dict(
    note="recording_id, start_sample, end_sample, metadata_filter; top_k and max_lag_fraction per task as in edge.py",
    E1=[[f"{r}_{ax}", s, s + WIN, f(m, op, ax)] for r, m, op, _, ax, s, _ in q1],
    E2_exact=[[f"{r}_x", s, s + WIN, {}] for r, m, op, _, ax, s, _ in q2[:50]],
    E2_probes=[[f"{r}_x", s, s + WIN, {}] for r, m, op, _, ax, s, _ in q2[50:]],
    E3=[[f"{r}_{ax}", s, s + WIN] for r, m, op, _, ax, s, _ in q1],
    E4=[[[f"{r}_{ax}", s, s + WIN, f(*meta[r], ax)] for ax in ("x", "y", "z") for s in pick(starts[(r, ax)])] for r in q4],
    E5=[[f"{x['file']}_x", ((ln[x["file"]] // 2) // 2048) * 2048, ((ln[x["file"]] // 2) // 2048) * 2048 + WIN,
         {"tool": x["tool"], "cpos": x["cpos"], "axis": "x", "baseline": True}] for x in tq])
(OUT / "edge_queries.json").write_text(json.dumps(Q))
print({k: len(v) for k, v in Q.items() if k != "note"}, "E4 searches per run", sorted({len(x) for x in Q["E4"]}))
