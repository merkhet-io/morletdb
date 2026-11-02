#!/usr/bin/env python3
"""B3.2 development diagnosis (B3_SET=dev only): split the server time of a 128,000-sample raw read into
chunk fetch (with detoasting), array aggregation, the Rust decode of pre-aggregated chunks, and per-chunk
decodes, and time the raw append path's pieces on the same server."""
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


def med(conn, sql, args=(), reps=40):
    conn.execute(sql, args).fetchone()
    t = []
    for _ in range(reps):
        a = time.perf_counter()
        conn.execute(sql, args).fetchone()
        t.append(time.perf_counter() - a)
    return round(statistics.median(t) * 1000, 3)


def main():
    m = common.manifest()
    ks = list(range(4))
    data, _ = run.preload(m, ks)
    mo.VARIANT.update({"raw_append": True, "pipeline": True})
    conn = mo.start("b3-dev-server2", "D2")
    mo.create(conn)
    for k in ks:
        mo.load_one(m["acquisitions"][k], data[k])
    conn.execute("VACUUM ANALYZE b3_chunks")
    a = m["acquisitions"][2]
    rid, lo, hi = mo.rid(a, "vibration_1"), 50_000, 178_000
    where = "c.recording_id=%s AND c.start_sample<%s AND c.start_sample>%s-32768 AND c.start_sample+c.sample_count>%s"
    args = (rid, hi, lo, lo)
    conn.execute(f"CREATE TEMP TABLE agg AS SELECT array_agg(c.encoded ORDER BY c.start_sample) e, "
                 f"array_agg(c.start_sample ORDER BY c.start_sample) s FROM b3_chunks c WHERE {where}", args)
    q = wl.QUANTUM["vibration_1"]
    x = np.ascontiguousarray(data[0]["vibration_1"][:4096], "<f8")
    out = {"note": "exploratory development diagnosis; medians, ms, round trip included", "image": mo.IMAGE,
           "select_1": med(conn, "SELECT 1"),
           "fetch_detoast_concat": med(conn, f"SELECT length(string_agg(c.encoded,''::bytea ORDER BY c.start_sample)) FROM b3_chunks c WHERE {where}", args),
           "fetch_and_array_agg": med(conn, f"SELECT cardinality(array_agg(c.encoded ORDER BY c.start_sample)) FROM b3_chunks c WHERE {where}", args),
           "decode_raw_preaggregated": med(conn, "SELECT length(public.morlet_decode_interval_raw(e, s, %s, %s)) FROM agg", (lo, hi)),
           "decode_stats_preaggregated": med(conn, "SELECT (public.morlet_decode_interval_stats(e, s, %s, %s))[1] FROM agg", (lo, hi)),
           "decode_each_chunk": med(conn, f"SELECT sum(cardinality(public.morlet_decode_chunk(c.encoded))) FROM b3_chunks c WHERE {where}", args),
           "valid_encoded_each_chunk": med(conn, f"SELECT count(*) FROM b3_chunks c WHERE {where} AND public.morlet_valid_encoded_chunk(c.encoded, c.sample_count)", args),
           "read_interval_raw": med(conn, "SELECT length(morlet_read_interval_raw('b3', %s, %s, %s))", (rid, lo, hi)),
           "from_le_bytes_4096": med(conn, "SELECT cardinality(public.morlet_float8_from_le_bytes(%b))", (x.tobytes(),)),
           "encode_4096": med(conn, "SELECT length(public.morlet_encode_chunk(public.morlet_float8_from_le_bytes(%b), %s))", (x.tobytes(), q)),
           }
    conn.close()
    common.remove(mo.NAME, "b3-dev-server2")
    (common.RESULTS / "diag_server2.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
