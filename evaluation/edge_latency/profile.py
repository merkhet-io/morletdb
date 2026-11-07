#!/usr/bin/env python3
"""Where does an exact envelope search spend its time? (N1 gallery, desktop vs Raspberry Pi)

  python -I evaluation/edge_latency/profile.py --windows windows.npz --dsn "..." --out profile_<machine>.json

Loads N1's 1,341 primary windows (16,384 samples at 30 kHz; evaluation/nilm_n1) into a fresh collection, the same
way N1 did, and then times, for six fixed queries, the parts of one search:

  call          morlet_search_envelopes as N1 called it (top_k = count at the query's location + 5)
  call_top5     the same with top_k = 5 (the screen keeps m = 200 windows instead of 20 * top_k)
  call_noscreen the N1 call with morlet.exact_screen = off (every eligible window scored)
  call_serial   the N1 call with max_parallel_workers_per_gather = 0
  score_all     the exact distance of every stored window, nothing else (one scan, one function per row)
  screen_all    the screening aggregate over every window (the screened path's first scan)
  envelope      morlet_envelope of the query alone (Hilbert envelope, decimation)
  send          sending the 16,384 query samples and returning their count (client and protocol cost)

Every part runs three times per query; the median is reported. Descriptive only; nothing here is a hypothesis.
"""
import argparse
import json
import platform
import time
from collections import Counter
from pathlib import Path

import numpy as np
import psycopg

RATE, WINDOW, MAX_LAG, QUANTUM, C = 30000, 16384, 0.075, 0.001, "lat"
QUERIES = [0, 200, 400, 700, 1000, 1340]   # positions among the primary records


def drop(c):
    names = [r[0] for r in c.execute("SELECT quote_ident(tablename) FROM pg_tables WHERE schemaname='public' "
                                     "AND (tablename=%s OR tablename LIKE %s)", (C, C + "\\_%"))]
    if names:
        c.execute(f"DROP TABLE {', '.join(names)} CASCADE")


def timed(c, sql, args, reps=3, setup=None):
    out = []
    for _ in range(reps):
        with c.transaction():
            if setup:
                c.execute(setup)
            t = time.perf_counter()
            c.execute(sql, args).fetchall()
            out.append(time.perf_counter() - t)
    return float(np.median(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", required=True)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    z = np.load(a.windows)
    w = {k: z[k] for k in z.files}
    prim = np.flatnonzero(w["primary"])
    loc = w["location"]
    per_loc = Counter(loc[prim])
    res = {"machine": platform.node(), "platform": platform.platform(), "queries": {}}
    with psycopg.connect(a.dsn, autocommit=True) as c:
        res["server"] = c.execute("SELECT version()").fetchone()[0]
        res["settings"] = dict(c.execute("SELECT name, setting FROM pg_settings WHERE name IN "
                                         "('max_parallel_workers_per_gather','shared_buffers','work_mem','jit')").fetchall())
        res["load_before"] = list(__import__("os").getloadavg())
        drop(c)
        c.execute("SELECT morlet_create_stream_collection('public', %s)", (C,))
        c.execute("SELECT morlet_enable_envelopes(%s::regclass)", (C,))
        t0 = time.perf_counter()
        for j in prim:
            rid = f"p{int(w['ids'][j]):04d}"
            meta = json.dumps({"type": str(w["type"][j]), "location": str(loc[j])})
            with c.transaction():
                c.execute("SELECT morlet_start_recording(%s::regclass, %s, %s, 'i', 'A', %s::jsonb, 512, 256, "
                          "'zscore', 'morlet-energy-v1', %s)", (C, rid, RATE, meta, QUANTUM))
                c.execute("SELECT morlet_append_chunk(%s::regclass, %s, 0, %s)",
                          (C, rid, np.asarray(w["i"][j], dtype=np.float64).tolist()))
                c.execute("SELECT morlet_build_envelopes(%s::regclass, %s, %s, %s)", (C, rid, WINDOW, WINDOW))
        res["ingest_s"] = time.perf_counter() - t0
        c.execute(f"VACUUM ANALYZE {C}, {C}_envelopes")
        lag = int(np.floor(MAX_LAG * len(c.execute("SELECT morlet_envelope(%s)",
                                                    (np.zeros(WINDOW).tolist(),)).fetchone()[0])))
        res["lag"] = lag
        search = ("SELECT * FROM morlet_search_envelopes(%s::regclass, %s, %s, 'i', 'A', '{}'::jsonb, %s, %s)")
        for qi in QUERIES:
            j = prim[qi]
            q = np.asarray(w["i"][j], dtype=np.float64).tolist()
            top = per_loc[loc[j]] + 5
            qenv = c.execute("SELECT morlet_envelope(%s)", (q,)).fetchone()[0]
            r = {"top_k": top}
            r["call"] = timed(c, search, (C, q, RATE, top, MAX_LAG))
            r["call_top5"] = timed(c, search, (C, q, RATE, 5, MAX_LAG))
            r["call_noscreen"] = timed(c, search, (C, q, RATE, top, MAX_LAG), setup="SET LOCAL morlet.exact_screen = off")
            r["call_serial"] = timed(c, search, (C, q, RATE, top, MAX_LAG),
                                     setup="SET LOCAL max_parallel_workers_per_gather = 0")
            r["score_all"] = timed(c, f"SELECT sum(morlet_envelope_distance(%s::float8[], envelope, %s::integer)) FROM {C}_envelopes",
                                   (qenv, lag))
            r["screen_all"] = timed(c, f"SELECT morlet_envelope_screen(e.envelope, e.recording_id, e.start_sample, "
                                       f"morlet_float8_le_bytes(%s::float8[]), %s::integer, %s::integer, 1000000::bigint) FROM {C}_envelopes e",
                                    (qenv, lag, max(200, 20 * top)))
            r["envelope"] = timed(c, "SELECT cardinality(morlet_envelope(%s))", (q,))
            r["send"] = timed(c, "SELECT cardinality(%s::float8[])", (q,))
            res["queries"][f"p{int(w['ids'][j]):04d}"] = r
            print(qi, {k: round(v, 4) if isinstance(v, float) else v for k, v in r.items()}, flush=True)
        res["load_after"] = list(__import__("os").getloadavg())
        drop(c)
    keys = [k for k in next(iter(res["queries"].values())) if k != "top_k"]
    res["median_s"] = {k: float(np.median([q[k] for q in res["queries"].values()])) for k in keys}
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res["median_s"], indent=1))


if __name__ == "__main__":
    main()
