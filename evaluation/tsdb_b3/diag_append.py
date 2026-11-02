#!/usr/bin/env python3
"""B3.1 development diagnosis (B3_SET=dev only): where does the server spend one morlet_append_chunk call
(4,096 samples, extractor none)? Times, as single statements on one connection, a round trip, the encoder
alone (morlet_encode_chunk), the checkpoint step (morlet_chunk_step), and whole append calls, each with the
same binary float8[] parameter; then EXPLAIN-free server timing via pg_stat_user_functions."""
import json
import os
import statistics
import time

import numpy as np

import common
import morletdb_adapter as mo
import run
import workload as wl

assert os.environ.get("B3_SET") == "dev"


def med(conn, sql, args, reps=40):
    conn.execute(sql, args).fetchone()
    t = []
    for _ in range(reps):
        a = time.perf_counter()
        conn.execute(sql, args).fetchone()
        t.append(time.perf_counter() - a)
    return round(statistics.median(t) * 1000, 3)


def main():
    m = common.manifest()
    data, _ = run.preload(m, [0])
    x = data[0]["vibration_1"]
    q = wl.QUANTUM["vibration_1"]
    conn = mo.start("b3-dev-append", "D2")
    mo.create(conn)
    import pg_numpy
    pg_numpy.register(conn)
    conn.execute("SET track_functions = 'all'")
    chunk = np.ascontiguousarray(x[:4096])
    out = {"note": "exploratory development diagnosis; medians of 40 statements, ms, round trip included"}
    out["select_1"] = med(conn, "SELECT 1", ())
    out["encode_chunk"] = med(conn, "SELECT length(morlet_encode_chunk(%s::float8[], %s))", (chunk, q))
    out["parse_array_only"] = med(conn, "SELECT cardinality(%s::float8[])", (chunk,))
    conn.execute("SELECT morlet_ensure_recording('b3'::regclass, 'diag', 64000, 'vibration_1', 'g', '{}'::jsonb, "
                 "512, 256, 'zscore', 'none', %s)", (q,))
    times = []
    for s in range(0, 4096 * 40, 4096):
        a = time.perf_counter()
        conn.execute("SELECT morlet_append_chunk('b3'::regclass, 'diag', %s, %s::float8[])", (s, np.ascontiguousarray(x[s:s + 4096])))
        times.append(time.perf_counter() - a)
    out["append_chunk_autocommit"] = round(statistics.median(times) * 1000, 3)
    out["function_times_ms"] = [
        (f, c, round(tt, 3), round(st, 3)) for f, c, tt, st in conn.execute(
            "SELECT funcname, calls, total_time, self_time FROM pg_stat_user_functions ORDER BY total_time DESC LIMIT 12").fetchall()]
    conn.close()
    common.remove(mo.NAME, "b3-dev-append")
    (common.RESULTS / "diag_append.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
