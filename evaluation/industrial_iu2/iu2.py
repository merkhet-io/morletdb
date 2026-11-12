"""IU2 harness: bounded-lag envelope distance to a tool's own first layer, milling tools run to failure.
See PROTOCOL.md.  Usage (in the runner container):  python iu2.py plan | load | score
"""
import csv, json, os, sys, time, threading, zipfile, collections
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import psycopg

DSN = os.environ["DATABASE_URL"]
ZIP = Path("/w/data/toollife/raw_data.zip")
META = Path("/w/iu2/tools.json")
FEAT = Path("/w/data/toollife/FeatureAndMetadata_Milling.csv")
OUT = Path("/w/iu2/results")
SMOKE = bool(os.environ.get("SMOKE"))  # harness check on 8 cycles of development tool 3, separate collection
COLL = "public.toollife_smoke" if SMOKE else "public.toollife"
if SMOKE:
    OUT = Path("/w/iu2/results_smoke")
RATE, WIN, STRIDE, BL, ZL = 25000.0, 4096, 2048, 0.075, 0.0
AXES = {"y": "Accelerometer - Spindle +Y", "z": "Accelerometer - Spindle -Z", "x": "Accelerometer - Spindle -X"}
DEV = {2, 3, 7, 8, 101, 102}
CONF = {4, 5, 6, 9, 10, 103, 105}
NQ = 16


def conn():
    return psycopg.connect(DSN, autocommit=True)


def plan():
    """Cycle table from the metadata only: order in time, position, baseline flag."""
    T = json.loads(META.read_text())
    cycles = []
    notes = []
    for tool, e in T.items():
        tool = int(tool)
        if tool == 11:
            continue
        files = sorted(e["files"], key=lambda f: -f[1])  # by CycleToFailure, descending = order in time
        ctfs = [f[1] for f in files]
        if len(set(ctfs)) != len(ctfs):
            notes.append(f"tool {tool}: CycleToFailure not unique")
        b = 4 if float(e["rdoc"]) == 8 else 7
        seen = {}
        for i, (name, ctf, size) in enumerate(files):
            cpos = int(name.rsplit("_C", 1)[1])
            base = cpos not in seen
            if base:
                seen[cpos] = name
                if i >= b:
                    notes.append(f"tool {tool}: baseline for C{cpos} is cycle {i + 1}, outside the first layer of {b}")
            cycles.append(dict(file=name, tool=tool, type=e["type"], adoc=e["adoc"], rdoc=e["rdoc"], cpos=cpos, tiu=i,
                               ctf=ctf, life=len(files), ctfn=ctf / max(ctfs) if max(ctfs) else 0.0, baseline=base,
                               baseline_file=seen[cpos], split="dev" if tool in DEV else "conf"))
    OUT.mkdir(exist_ok=True)
    (OUT / "cycles.json").write_text(json.dumps(dict(cycles=cycles, notes=notes)))
    c = collections.Counter((x["split"], x["baseline"]) for x in cycles)
    print("cycles", len(cycles), dict(c)); print("notes", notes)
    return cycles


def load():
    cycles = json.loads((OUT / "cycles.json").read_text())["cycles"]
    if SMOKE:
        cycles = [c for c in cycles if c["tool"] == 3][:8]
    import pyarrow as pa, pyarrow.csv as pc
    with conn() as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        if not c.execute("SELECT to_regclass(%s)", (COLL,)).fetchone()[0]:
            c.execute("SELECT morlet_create_stream_collection('public',%s)", (COLL.split('.')[1],))
            c.execute("SELECT morlet_enable_envelopes(%s)", (COLL,))
        ver = c.execute("SELECT morlet_version(), morlet_envelope_version()").fetchone()
        # resume: a cycle is complete if its three recordings exist with equal length and all envelope windows
        tbl = COLL.split(".")[1]
        env = dict(c.execute(f"SELECT recording_id, count(*) FROM public.{tbl}_envelopes GROUP BY 1").fetchall())
        have = [r[0] for r in c.execute(f"SELECT recording_id FROM public.{tbl}").fetchall()]
        got = {r: c.execute("SELECT received_samples FROM morlet_stream_status(%s,%s)", (COLL, r)).fetchone()[0] for r in have}
    local = threading.local()
    lengths = {}
    todo = []
    for cy in cycles:
        ns = [got.get(f"{cy['file']}_{ax}") for ax in AXES]
        if None not in ns and len(set(ns)) == 1 and ns[0] >= WIN and all(env.get(f"{cy['file']}_{ax}", 0) == (ns[0] - WIN) // STRIDE + 1 for ax in AXES):
            lengths[cy["file"]] = ns[0]
        else:
            todo.append(cy)
    print("already complete", len(lengths), "to load", len(todo), flush=True)

    def one(cy):
        if not hasattr(local, "z"):
            local.z = zipfile.ZipFile(ZIP)
        parts = {ax: [] for ax in AXES}
        with local.z.open(f"raw_data/{cy['file']}.csv") as f:
            rd = pc.open_csv(pa.PythonFile(f), read_options=pc.ReadOptions(block_size=8 << 20),
                             convert_options=pc.ConvertOptions(include_columns=list(AXES.values()),
                                                               column_types={v: pa.float64() for v in AXES.values()}))
            for batch in rd:  # streaming: only the three selected columns are kept
                for ax, col in AXES.items():
                    a = batch.column(col)
                    assert a.null_count == 0, f"{cy['file']} {col}: nulls"
                    parts[ax].append(a.to_numpy(zero_copy_only=False).astype("<f8"))
        n = sum(len(p) for p in parts["x"])
        with conn() as c:
            for ax in AXES:
                x = np.ascontiguousarray(np.concatenate(parts[ax])); parts[ax] = None
                assert np.all(np.isfinite(x)) and np.array_equal(x, np.round(x)), f"{cy['file']} {ax}: not integer counts"
                rid = f"{cy['file']}_{ax}"
                meta = {k: cy[k] for k in ("tool", "type", "adoc", "rdoc", "cpos", "tiu", "ctf", "baseline", "split")} | {"axis": ax, "cycle": cy["file"]}
                if rid not in got:
                    c.execute("SELECT morlet_start_recording(%s,%s,%s,%s,'counts',%s::jsonb,512,256,'zscore','none')",
                              (COLL, rid, RATE, f"spindle_{ax}", json.dumps(meta)))
                for off in range(0, n, 262144):  # an identical retry of stored chunks is verified and is a no-op
                    c.execute("SELECT morlet_append_chunks_raw(%s,%s,%s,%s,16384)", (COLL, rid, off, x[off:off + 262144].tobytes()))
                while c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,10000)", (COLL, rid, WIN, STRIDE)).fetchone()[0]:
                    pass
        return cy["file"], n

    t0 = time.time(); done = 0
    with ThreadPoolExecutor(int(os.environ.get("IU2_THREADS", "5"))) as ex:
        for name, n in ex.map(one, todo):
            lengths[name] = n; done += 1
            if done % 10 == 0:
                print("loaded", done, "of", len(todo), round(time.time() - t0), "s", flush=True)
    with conn() as c:
        cur = c.execute("SELECT * FROM morlet_collection_storage(%s)", (COLL,)); cols = [d.name for d in cur.description]
        st = dict(zip(cols, map(str, cur.fetchone())))
    (OUT / "load.json").write_text(json.dumps(dict(version=ver, cycles=len(cycles), seconds=time.time() - t0, storage=st, lengths=lengths)))
    print("LOADDONE", round(time.time() - t0), "s", st, flush=True)


def query_starts(n, k=NQ):
    starts = list(range(0, n - WIN + 1, STRIDE))
    if len(starts) <= k:
        return starts
    return [starts[i] for i in sorted(set(int(round(i)) for i in np.linspace(0, len(starts) - 1, k)))]


def score():
    cycles = json.loads((OUT / "cycles.json").read_text())["cycles"]
    lengths = json.loads((OUT / "load.json").read_text())["lengths"]
    jobs = [cy for cy in cycles if not cy["baseline"] and cy["file"] in lengths]
    base_rms = {}
    lock = threading.Lock()

    def rms_all(c, rid, n):
        q = c.execute("SELECT s, (morlet_interval_stats(%s,%s,s,s+%s)).rms FROM generate_series(0,%s-%s,%s) s", (COLL, rid, WIN, n, WIN, STRIDE)).fetchall()
        return np.array([v for _, v in q])

    def one(cy):
        out = dict(file=cy["file"], tool=cy["tool"], cpos=cy["cpos"], tiu=cy["tiu"], ctf=cy["ctf"], ctfn=cy["ctfn"], life=cy["life"],
                   split=cy["split"], n=lengths[cy["file"]], n_base=lengths[cy["baseline_file"]], BL=[], ZL=[], RMSW=[])
        with conn() as c:
            for ax in AXES:
                brid = f"{cy['baseline_file']}_{ax}"
                with lock:
                    have = brid in base_rms
                if not have:
                    v = rms_all(c, brid, lengths[cy["baseline_file"]])
                    with lock:
                        base_rms[brid] = v
                flt = json.dumps({"tool": cy["tool"], "cpos": cy["cpos"], "axis": ax, "baseline": True})
                rid = f"{cy['file']}_{ax}"
                for s in query_starts(lengths[cy["file"]]):
                    for arm, lag in (("BL", BL), ("ZL", ZL)):
                        h = c.execute("SELECT distance FROM morlet_search_envelope_interval(%s,%s,%s,%s,%s::jsonb,1,%s)",
                                      (COLL, rid, s, s + WIN, flt, lag)).fetchone()
                        out[arm].append(h[0] if h else None)
                    qv = c.execute("SELECT rms FROM morlet_interval_stats(%s,%s,%s,%s)", (COLL, rid, s, s + WIN)).fetchone()[0]
                    out["RMSW"].append(float(np.min(np.abs(base_rms[brid] - qv))))
        return out

    t0 = time.time(); res = []
    with ThreadPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(one, jobs)):
            res.append(r)
            if (i + 1) % 100 == 0:
                print("scored", i + 1, "of", len(jobs), round(time.time() - t0), "s", flush=True)
    (OUT / "scores.json").write_text(json.dumps(dict(seconds=time.time() - t0, rows=res)))
    print("SCOREDONE", len(res), round(time.time() - t0), "s", flush=True)


if __name__ == "__main__":
    {"plan": plan, "load": load, "score": score}[sys.argv[1]]()
