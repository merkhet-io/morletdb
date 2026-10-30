#!/usr/bin/env python3
"""Envelope-search speed check on the R1b SQL-check database (CX 2022 windows, loaded by
sql_check.py). Every window is a query once (same station, itself excluded, top 10); the
ranking, exact distances (as hex of the float64 bits) and latencies are recorded.

  .venv-seismic/bin/python -I evaluation/materialized/compact/search_speed_check.py DBNAME OUT.json

Run before and after a change to the search path: the results must match exactly.
"""
import json
import struct
import sys
import time

import numpy as np
import psycopg

COLL = "envcheck_2022"


def main():
    db, out = sys.argv[1], sys.argv[2]
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as c:
        recs = c.execute(f"SELECT recording_id, metadata->>'station' FROM {COLL} ORDER BY 1").fetchall()
        sql = (f"SELECT recording_id, start_sample, distance FROM morlet_search_envelope_interval("
               f"'{COLL}', %s, 0, 4096, metadata_filter => %s::jsonb, top_k => 10)")
        for rid, st in recs[:50]:
            c.execute(sql, (rid, json.dumps({"station": st}))).fetchall()      # warm-up
        rows, ms = {}, []
        for rid, st in recs:
            t0 = time.perf_counter()
            r = c.execute(sql, (rid, json.dumps({"station": st}))).fetchall()
            ms.append(1e3 * (time.perf_counter() - t0))
            rows[rid] = [[h, s, struct.pack("<d", d).hex()] for h, s, d in r]
        full = []
        for rid, _ in recs[:200]:                                              # no metadata filter: all other windows
            t0 = time.perf_counter()
            c.execute(f"SELECT count(*) FROM morlet_search_envelope_interval('{COLL}', %s, 0, 4096)", (rid,)).fetchall()
            full.append(1e3 * (time.perf_counter() - t0))
        reads = []
        for rid, _ in recs[:500]:
            t0 = time.perf_counter()
            c.execute(f"SELECT cardinality(samples) FROM morlet_read_interval('{COLL}', %s, 0, 4096)", (rid,)).fetchall()
            reads.append(1e3 * (time.perf_counter() - t0))
    pct = lambda v: {"p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95))}
    res = {"queries": len(rows), "station_search_ms": pct(ms), "unfiltered_search_ms": pct(full),
           "read_interval_ms": pct(reads), "results": rows}
    json.dump(res, open(out, "w"))
    print({k: v for k, v in res.items() if k != "results"})


if __name__ == "__main__":
    main()
