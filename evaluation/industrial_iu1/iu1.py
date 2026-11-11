"""IU1 harness: bounded-lag envelope search on Bosch CNC milling vibration. See PROTOCOL.md.

Usage (inside the runner container):  python -I iu1.py load | rms | t12 DEV|CONF | t4 | t5 | t6 | analyze
"""
import json, os, sys, time, hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import h5py
import numpy as np
import psycopg

DSN = os.environ["DATABASE_URL"]
COLL = "public.bosch"
BASE = Path("/data/bosch/data")
OUT = Path("/spike/results")
RATE, WIN, STRIDE, BL, ZL = 2000.0, 4096, 2048, 0.075, 0.0
AXES = ("x", "y", "z")
SEED = 20261111
DEV, CONF = ("M01",), ("M02", "M03")


def runs():
    for p in sorted(BASE.glob("M0*/OP*/*/*.h5")):
        m, op, label = p.parts[-4], p.parts[-3], p.parts[-2]
        stem = p.stem  # e.g. M02_Aug_2019_OP03_000
        period = "_".join(stem.split("_")[1:3])
        # file numbers restart per label folder (61 names occur as good and as bad), so the label is part of the id
        yield dict(path=str(p), machine=m, op=op, label=label, period=period, run=f"{stem}_{label}")


def rid(run, ax):
    return f"{run}_{ax}"


def conn():
    return psycopg.connect(DSN, autocommit=True)


# ------------------------------------------------------------------ load
def _load_one(r):
    with h5py.File(r["path"], "r") as h:
        v = h["vibration_data"][:]
    n = v.shape[0]
    with conn() as c:
        for i, ax in enumerate(AXES):
            meta = {k: r[k] for k in ("machine", "op", "period", "label", "run")} | {"axis": ax}
            c.execute("SELECT morlet_start_recording(%s,%s,%s,%s,'counts',%s::jsonb,512,256,'zscore','none')",
                      (COLL, rid(r["run"], ax), RATE, f"acc_{ax}", json.dumps(meta)))
            x = np.ascontiguousarray(v[:, i].astype("<f8"))
            assert np.array_equal(x.astype(np.int64), v[:, i]), "int64 -> float64 not exact"
            for off in range(0, n, 262144):  # product limit per call
                c.execute("SELECT morlet_append_chunks_raw(%s,%s,%s,%s,16384)",
                          (COLL, rid(r["run"], ax), off, x[off:off + 262144].tobytes()))
            while c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,10000)",
                            (COLL, rid(r["run"], ax), WIN, STRIDE)).fetchone()[0]:
                pass
    return n


def load():
    with conn() as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        c.execute("SELECT morlet_create_stream_collection('public','bosch')")
        c.execute("SELECT morlet_enable_envelopes(%s)", (COLL,))
        ver = c.execute("SELECT morlet_version(), morlet_envelope_version()").fetchone()
    rs = list(runs())
    if os.environ.get("SMOKE"):  # harness check only: a few runs of one operation
        rs = [r for r in rs if r["machine"] == "M01" and r["op"] == "OP05"][:12]
    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        ns = list(ex.map(_load_one, rs))
    dt = time.time() - t0
    with conn() as c:
        cur = c.execute("SELECT * FROM morlet_collection_storage(%s)", (COLL,))
        cols = [d.name for d in cur.description]
        st = cur.fetchall()
    res = dict(version=ver, runs=len(rs), samples_per_axis=int(sum(ns)), seconds=dt,
               storage=[dict(zip(cols, map(str, row))) for row in st])
    (OUT / "load.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


# ------------------------------------------------------------------ gallery windows and RMS
def rms_table():
    """Every envelope-grid window with its RMS from morlet_interval_stats."""
    rows = []
    with conn() as c:
        loaded = {x[0] for x in c.execute("SELECT DISTINCT metadata->>'run' FROM public.bosch").fetchall()}
    rs = [r for r in runs() if r["run"] in loaded]

    def one(r):
        out = []
        with conn() as c:
            for ax in AXES:
                n = c.execute("SELECT received_samples FROM morlet_stream_status(%s,%s)",
                              (COLL, rid(r["run"], ax))).fetchone()
                q = c.execute(
                    "SELECT s, (morlet_interval_stats(%s,%s,s,s+%s)).rms FROM generate_series(0,%s-%s,%s) s",
                    (COLL, rid(r["run"], ax), WIN, n[0], WIN, STRIDE)).fetchall()
                out += [(r["run"], r["machine"], r["op"], r["label"], ax, int(s), float(v)) for s, v in q]
        return out

    with ThreadPoolExecutor(6) as ex:
        for part in ex.map(one, rs):
            rows += part
    (OUT / "windows.json").write_text(json.dumps(rows))
    print(len(rows), "windows")


def windows():
    return json.loads((OUT / "windows.json").read_text())


def query_starts(starts, k=8):
    starts = sorted(starts)
    if len(starts) <= k:
        return starts
    idx = sorted(set(int(round(i)) for i in np.linspace(0, len(starts) - 1, k)))
    return [starts[i] for i in idx]


# ------------------------------------------------------------------ search helpers
def search_interval(c, run, ax, s, flt, lag, top=40):
    return c.execute(
        "SELECT recording_id,start_sample,distance FROM morlet_search_envelope_interval(%s,%s,%s,%s,%s::jsonb,%s,%s)",
        (COLL, rid(run, ax), s, s + WIN, json.dumps(flt), top, lag)).fetchall()


def drop_run(hits, run, k=10, key=lambda h: h[0].rsplit("_", 1)[0]):
    return [h for h in hits if key(h) != run][:k]


# ------------------------------------------------------------------ T1 + T2
def t12(which):
    machines = DEV if which == "DEV" else CONF
    W = windows()
    meta = {}
    by_run = {}
    for run, m, op, label, ax, s, v in W:
        meta[run] = (m, op, label)
        by_run.setdefault((run, ax), []).append(s)
    bad_runs = {r for r, (m, op, l) in meta.items() if l == "bad"}
    # RMS arrays per (machine, op, axis)
    gal = {}
    for run, m, op, label, ax, s, v in W:
        gal.setdefault((m, op, ax), []).append((run, s, v, label))
    jobs = [(run, ax, s) for (run, ax), ss in sorted(by_run.items()) if meta[run][0] in machines
            for s in query_starts(ss)]

    def one(job):
        run, ax, s = job
        m, op, label = meta[run]
        base = {"machine": m, "op": op, "axis": ax}
        out = dict(run=run, ax=ax, s=s, m=m, op=op, label=label)
        with conn() as c:
            for arm, lag in (("BL", BL), ("ZL", ZL)):
                t = time.perf_counter()
                h = drop_run(search_interval(c, run, ax, s, base | {"label": "good"}, lag), run, 1)
                out[f"t2_{arm}"] = h[0][2] if h else None
                out[f"t2_{arm}_ms"] = (time.perf_counter() - t) * 1000
                if label == "bad":
                    h = drop_run(search_interval(c, run, ax, s, base, lag), run)
                    out[f"t1_{arm}"] = [[x[0].rsplit("_", 1)[0], int(x[1]), x[2]] for x in h]
        g = gal[(m, op, ax)]
        qv = next(v for r2, s2, v, _ in g if r2 == run and s2 == s)
        others = [(abs(v - qv), r2, s2, l2) for r2, s2, v, l2 in g if r2 != run]
        others.sort()
        goods = [o for o in others if o[3] == "good"]
        out["t2_RMS"] = goods[0][0] if goods else None
        if label == "bad":
            out["t1_RMS"] = [[o[1], o[2], o[0]] for o in others[:10]]
            out["chance"] = sum(o[3] == "bad" for o in others) / max(1, len(others))
        return out

    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, jobs))
    (OUT / f"t12_{which.lower()}.json").write_text(json.dumps(dict(seconds=time.time() - t0, rows=res)))
    print(which, len(res), "queries", round(time.time() - t0), "s")


# ------------------------------------------------------------------ T4 probes vs exact
def t4():
    with conn() as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        t = time.time()
        c.execute("SELECT morlet_enable_envelope_probes(%s,%s)", (COLL, WIN))
        enable_s = time.time() - t
        pv = c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
    W = [w for w in windows() if w[4] == "x"]
    rng = np.random.default_rng(SEED)
    qs = [W[i] for i in rng.choice(len(W), 100, replace=False)]
    rows = {i: {} for i in range(len(qs))}
    with conn() as c:
        for rnd in (1, 2):
            for i, q in enumerate(qs):
                for arm, fn in (("exact", "morlet_search_envelope_interval"), ("probe", "morlet_search_envelope_probes")):
                    t = time.perf_counter()
                    h = c.execute(f"SELECT recording_id,start_sample FROM {fn}(%s,%s,%s,%s,'{{}}'::jsonb,10,%s)",
                                  (COLL, rid(q[0], "x"), q[5], q[5] + WIN, BL)).fetchall()
                    ms = (time.perf_counter() - t) * 1000
                    rows[i].setdefault(arm + "_ms", []).append(ms)
                    rows[i][arm] = [[a, int(b)] for a, b in h]
            load = os.getloadavg()
            rows[f"load_round{rnd}"] = load
    (OUT / "t4.json").write_text(json.dumps(dict(gallery=len(W), enable_s=enable_s, pgvector=pv, rows=rows)))
    print("t4 done, gallery", len(W))


# ------------------------------------------------------------------ T5 time warping
def t5():
    W = [w for w in windows() if w[1] in CONF and w[3] == "good"]
    ends = {}
    for run, m, op, label, ax, s, v in windows():
        ends[(run, ax)] = max(ends.get((run, ax), 0), s + WIN)
    W = [w for w in W if w[5] + int(np.ceil(WIN * 1.10)) + 1 <= ends[(w[0], w[4])]]
    rng = np.random.default_rng(SEED)
    qs = [W[i] for i in rng.choice(len(W), 100, replace=False)]
    out = []
    with conn() as c:
        for run, m, op, label, ax, s0, v in qs:
            L = int(np.ceil(WIN * 1.10)) + 1
            x = np.array(c.execute("SELECT samples FROM morlet_read_interval(%s,%s,%s,%s)",
                                   (COLL, rid(run, ax), s0, s0 + L)).fetchone()[0], float)
            for st in (0, 0.01, 0.02, 0.05, 0.10):
                span = WIN * (1 + st)
                q = np.interp(np.arange(WIN) * (span / WIN), np.arange(L), x)
                for arm, lag in (("BL", BL), ("ZL", ZL)):
                    h = c.execute(
                        "SELECT recording_id,start_sample,distance FROM morlet_search_envelopes(%s,%s,%s,%s,'counts',%s::jsonb,40,%s)",
                        (COLL, q.tolist(), RATE, f"acc_{ax}", json.dumps({"machine": m, "op": op, "axis": ax}), lag)).fetchall()
                    rank, dist = None, None
                    for k, (r2, s2, d) in enumerate(h):
                        if r2 == rid(run, ax) and min(s2 + WIN, s0 + WIN) - max(s2, s0) >= WIN // 2:
                            rank, dist = k + 1, d
                            break
                    out.append(dict(run=run, ax=ax, s0=s0, stretch=st, arm=arm, rank=rank, dist=dist,
                                    top1=h[0][2] if h else None))
    (OUT / "t5.json").write_text(json.dumps(out))
    print("t5 done")


# ------------------------------------------------------------------ T6 cross-machine
def t6():
    allw = windows()
    W = [w for w in allw if w[1] in CONF and w[3] == "good"]
    rng = np.random.default_rng(SEED + 6)
    qs = [W[i] for i in rng.choice(len(W), 200, replace=False)]
    out = []

    def one(q):
        run, m, op, label, ax, s, v = q
        others = [w for w in allw if w[4] == ax and w[1] != m]
        chance = sum(w[2] == op for w in others) / len(others)
        r = dict(run=run, m=m, op=op, ax=ax, s=s, chance=chance)
        with conn() as c:
            for arm, lag in (("BL", BL), ("ZL", ZL)):
                h = search_interval(c, run, ax, s, {"axis": ax}, lag, top=100)
                h = [x for x in h if not x[0].startswith(m + "_")][:10]
                r[arm] = [x[0] for x in h]
                r[arm + "_same_op"] = sum(f"_{op}_" in x[0] for x in h) / max(1, len(h))
        return r

    with ThreadPoolExecutor(4) as ex:
        out = list(ex.map(one, qs))
    (OUT / "t6.json").write_text(json.dumps(out))
    print("t6 done")


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"load": load, "rms": rms_table, "t4": t4, "t5": t5, "t6": t6}.get(cmd, lambda: t12(sys.argv[2]))()
