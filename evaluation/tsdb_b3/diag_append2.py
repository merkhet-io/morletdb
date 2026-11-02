#!/usr/bin/env python3
"""B3.2 development diagnosis (B3_SET=dev only): per-function server time inside morlet_append_chunk_raw for
4,096-sample chunks (extractor none), from pg_stat_user_functions with track_functions = 'all', plus the
components called alone with the same samples."""
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


def med(conn, sql, args, reps=60):
    conn.execute(sql, args).fetchone()
    t = []
    for _ in range(reps):
        a = time.perf_counter()
        conn.execute(sql, args).fetchone()
        t.append(time.perf_counter() - a)
    return round(statistics.median(t) * 1000, 3)


def main(chunk=4096):
    m = common.manifest()
    data, _ = run.preload(m, [0])
    x = data[0]["vibration_1"]
    q = wl.QUANTUM["vibration_1"]
    conn = mo.start("b3-dev-append2", "D2")
    mo.create(conn)
    conn.execute("SET track_functions = 'all'")
    conn.execute("SELECT morlet_ensure_recording('b3'::regclass, 'diag', 64000, 'vibration_1', 'g', '{}'::jsonb, "
                 "512, 256, 'zscore', 'none', %s)", (q,))
    state = conn.execute("SELECT checkpoint FROM b3 WHERE recording_id='diag'").fetchone()[0]
    raw = np.ascontiguousarray(x[:chunk], "<f8").tobytes()
    out = {"note": "exploratory development diagnosis; ms", "chunk": chunk, "checkpoint_bytes": len(json.dumps(state))}
    out["select_1"] = med(conn, "SELECT 1", ())
    arr = "public.morlet_float8_from_le_bytes(%b)"
    out["from_bytes"] = med(conn, f"SELECT cardinality({arr})", (raw,))
    out["valid_chunk"] = med(conn, f"SELECT public.morlet_valid_chunk({arr})", (raw,))
    out["chunk_step"] = med(conn, f"SELECT length(public.morlet_chunk_step(%s::jsonb, 0, {arr})::text)",
                            (json.dumps(state), raw))
    out["encode_chunk"] = med(conn, f"SELECT length(public.morlet_encode_chunk({arr}, %s))", (raw, q))
    out["stream_relations"] = med(conn, "SELECT * FROM public.morlet_stream_relations('b3'::regclass)", ())
    conn.execute("SELECT pg_stat_reset()")
    times = []
    n = 200
    for i in range(n):
        s = i * chunk
        b = np.ascontiguousarray(x[s % (len(x) - chunk):][:chunk], "<f8").tobytes()
        a = time.perf_counter()
        conn.execute("SELECT morlet_append_chunk_raw('b3'::regclass, 'diag', %s, %b)", (s, b))
        times.append(time.perf_counter() - a)
    out["append_raw_autocommit"] = round(statistics.median(times) * 1000, 3)
    conn.execute("SELECT pg_stat_force_next_flush()")
    time.sleep(1.5)
    out["per_call_ms"] = {f: round(st / n, 4) for f, c, tt, st in conn.execute(
        "SELECT funcname, calls, total_time, self_time FROM pg_stat_user_functions ORDER BY self_time DESC").fetchall()}
    out["per_call_total_ms"] = {f: round(tt / n, 4) for f, c, tt, st in conn.execute(
        "SELECT funcname, calls, total_time, self_time FROM pg_stat_user_functions ORDER BY total_time DESC").fetchall()}
    conn.close()
    common.remove(mo.NAME, "b3-dev-append2")
    (common.RESULTS / "diag_append2.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
