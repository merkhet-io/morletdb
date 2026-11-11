"""IU1 addendum A1: nearest good-history distance per half-year of the history. See PROTOCOL.md."""
import json, time
from concurrent.futures import ThreadPoolExecutor
import iu1
from iu1 import conn, windows, query_starts, search_interval, drop_run, OUT, CONF, BL, ZL

def period(run): return "_".join(run.split("_")[1:3])

def main():
    W = windows(); meta = {}; by_run = {}; gal = {}; periods = {}
    for run, m, op, label, ax, s, v in W:
        meta[run] = (m, op, label); by_run.setdefault((run, ax), []).append(s)
        gal.setdefault((m, op, ax, period(run)), []).append((run, s, v, label)); periods.setdefault(m, set()).add(period(run))
    jobs = [(run, ax, s) for (run, ax), ss in sorted(by_run.items()) if meta[run][0] in CONF for s in query_starts(ss)]

    def one(job):
        run, ax, s = job; m, op, label = meta[run]; own = period(run)
        out = dict(run=run, ax=ax, s=s, m=m, op=op, label=label, period=own, BL={}, ZL={}, RMS={}, n_good={})
        base = {"machine": m, "op": op, "axis": ax}
        qv = next(v for r2, s2, v, _ in gal[(m, op, ax, own)] if r2 == run and s2 == s)
        with conn() as c:
            for p in sorted(periods[m]):
                g = [x for x in gal.get((m, op, ax, p), []) if x[0] != run and x[3] == "good"]
                out["n_good"][p] = len({x[0] for x in g})
                if not g: continue
                out["RMS"][p] = min(abs(x[2] - qv) for x in g)
                for arm, lag in (("BL", BL), ("ZL", ZL)):
                    h = drop_run(search_interval(c, run, ax, s, base | {"label": "good", "period": p}, lag), run, 1)
                    if h: out[arm][p] = h[0][2]
            if label == "bad":
                g = [x for x in gal[(m, op, ax, own)] if x[0] != run]
                out["t1_chance"] = sum(x[3] == "bad" for x in g) / max(1, len(g))
                near = sorted(g, key=lambda x: abs(x[2] - qv))[:10]
                out["t1_RMS"] = [x[0] for x in near]
                for arm, lag in (("BL", BL), ("ZL", ZL)):
                    h = drop_run(search_interval(c, run, ax, s, base | {"period": own}, lag), run)
                    out["t1_" + arm] = [x[0].rsplit("_", 1)[0] for x in h]
        return out

    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, jobs))
    lengths = {}
    for (run, ax), ss in by_run.items():
        if ax == "x": lengths[run] = max(ss) + iu1.WIN
    (OUT / "drift.json").write_text(json.dumps(dict(seconds=time.time() - t0, rows=res, grid_length=lengths, meta=meta)))
    print("DRIFTDONE", len(res), "queries", round(time.time() - t0), "s")

main()
