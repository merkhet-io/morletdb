#!/usr/bin/env python3
"""B3.1 development diagnosis (B3_SET=dev only): where does the server spend an R2 read (128,000 samples)?
Times, server-side via EXPLAIN ANALYZE-free SQL wrappers, the chunk fetch alone (array_agg of encoded chunks),
the fetch plus Rust decode (morlet_decode_interval_raw), and the whole raw-read function; reports the chunk
column's storage and compression."""
import json
import os
import statistics
import time

import common
import morletdb_adapter as mo
import run

assert os.environ.get("B3_SET") == "dev"


def timed(conn, sql, args, reps=30):
    conn.execute(sql, args).fetchone()
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        conn.execute(sql, args).fetchone()
        out.append(time.perf_counter() - t)
    return round(statistics.median(out) * 1000, 3)


def main():
    m = common.manifest()
    ks = list(range(6))
    data, _ = run.preload(m, ks)
    conn = mo.start("b3-dev-server", "D2")
    mo.create(conn)
    for k in ks:
        mo.load_one(m["acquisitions"][k], data[k])
    conn.execute("VACUUM ANALYZE b3_chunks")
    a = m["acquisitions"][2]
    rid, lo, hi = mo.rid(a, "vibration_1"), 50_000, 178_000
    fetch = ("SELECT length(string_agg(c.encoded, ''::bytea ORDER BY c.start_sample)) FROM b3_chunks c "
             "WHERE c.recording_id=%s AND c.start_sample<%s AND c.start_sample>%s-32768 AND c.start_sample+c.sample_count>%s")
    decode = ("SELECT length(public.morlet_decode_interval_raw(array_agg(c.encoded ORDER BY c.start_sample), "
              "array_agg(c.start_sample ORDER BY c.start_sample), %s, %s)) FROM b3_chunks c "
              "WHERE c.recording_id=%s AND c.start_sample<%s AND c.start_sample>%s-32768 AND c.start_sample+c.sample_count>%s")
    out = {
        "note": "exploratory development diagnosis; medians of 30, ms, server round trip included",
        "select_1": timed(conn, "SELECT 1", ()),
        "fetch_chunks_only": timed(conn, fetch, (rid, hi, lo, lo)),
        "fetch_and_decode": timed(conn, decode, (lo, hi, rid, hi, lo, lo)),
        "read_interval_raw_length_only": timed(conn, "SELECT length(morlet_read_interval_raw('b3', %s, %s, %s))", (rid, lo, hi)),
        "read_interval_raw_transfer": timed(conn, "SELECT morlet_read_interval_raw('b3', %s, %s, %s)", (rid, lo, hi)),
        "chunks_in_interval": conn.execute("SELECT count(*), sum(length(encoded)), avg(pg_column_size(encoded)) FROM b3_chunks "
                                           "WHERE recording_id=%s AND start_sample<%s AND start_sample+sample_count>%s",
                                           (rid, hi, lo)).fetchone(),
        "encoded_storage": conn.execute("SELECT attstorage, attcompression FROM pg_attribute "
                                        "WHERE attrelid='b3_chunks'::regclass AND attname='encoded'").fetchone(),
        "compressed_values": conn.execute("SELECT pg_column_compression(encoded), count(*) FROM b3_chunks GROUP BY 1").fetchall(),
    }
    conn.close()
    common.remove(mo.NAME, "b3-dev-server")
    (common.RESULTS / "diag_server_read.json").write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
