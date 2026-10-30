#!/usr/bin/env python3
"""P2 step 2: the same samples in pg_morlet (M) and compressed TimescaleDB hypertables (T-f, T-i, T-a).

  TSDB_PASSWORD=... .venv-seismic/bin/python -I evaluation/plain_postgres/p2_bench.py DUMP_DIR evaluation/plain_postgres/results

MORLET_DSN defaults to the local pgrx server (database morlet_p2_bench, created if missing);
TSDB_DSN to the throwaway container on 127.0.0.1:55432.
"""
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

MORLET_DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_p2_bench")
TSDB_DSN = os.environ.get("TSDB_DSN", "host=127.0.0.1 port=55432 dbname=postgres user=postgres")
RATES = {"cx": 100.0, "ims": 20000.0, "paderborn-counts": 64000.0, "paderborn-physical": 64000.0}
TS_CHUNK = 1 << 20                    # samples per Timescale chunk
READ_AT = (0, 4095, 2097169, 4194304 - 20000)
READ_N = 20000


def ensure_db(dsn):
    name = dict(p.split("=") for p in dsn.split())["dbname"]
    admin = dsn.replace(f"dbname={name}", "dbname=postgres")
    with psycopg.connect(admin, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            c.execute(f"CREATE DATABASE {name}")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")


def bitwise(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return a.shape == b.shape and np.array_equal(a.view(np.uint64), b.view(np.uint64))


def run_m(conn, name, d):
    coll = "m_" + name.replace("-", "_")
    conn.execute(f"DROP TABLE IF EXISTS {coll}_chunks, {coll}_segments, {coll}")
    conn.execute("SELECT morlet_create_stream_collection('public', %s)", (coll,))
    conn.execute("SELECT morlet_start_recording(%s,'r',%s,'x','u','{}',512,256,'zscore','none',%s)",
                 (coll, RATES[name], float(d["quantum"])))
    x, starts = d["floats"], np.r_[0, np.cumsum(d["lengths"])]
    t0 = time.perf_counter()
    for i in range(0, len(d["lengths"]), 16):
        with conn.transaction():
            for j in range(i, min(i + 16, len(d["lengths"]))):
                conn.execute("SELECT morlet_append_chunk(%s,'r',%s,%s::float8[])",
                             (coll, int(starts[j]), x[starts[j]:starts[j + 1]].tolist()))
    secs = time.perf_counter() - t0
    conn.execute(f"VACUUM ANALYZE {coll}_chunks")
    conn.execute(f"VACUUM ANALYZE {coll}")
    st = conn.execute("SELECT total_bytes, chunk_table_bytes, chunk_table_bits_per_sample, total_bits_per_sample"
                      " FROM morlet_collection_storage(%s)", (coll,)).fetchone()
    kinds = conn.execute("SELECT grid_chunks, float_chunks, raw_chunks FROM morlet_recording_storage(%s,'r')", (coll,)).fetchone()
    ok = all(bitwise(conn.execute("SELECT samples FROM morlet_read_interval(%s,'r',%s,%s)", (coll, a, a + READ_N)).fetchone()[0],
                     x[a:a + READ_N]) for a in READ_AT)
    return {"total_bytes": st[0], "bits_per_sample": st[3], "chunk_table_bits_per_sample": st[2],
            "grid_float_raw_chunks": list(kinds), "ingest_s": secs, "read_back_bitwise": ok}


def hypertable(conn, table, cols, interval):
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(f"CREATE TABLE {table} ({cols})")
    conn.execute(f"SELECT create_hypertable('{table}', by_range('t', {interval}))")
    conn.execute(f"ALTER TABLE {table} SET (timescaledb.compress, timescaledb.compress_segmentby = 'rec',"
                 " timescaledb.compress_orderby = 't')")


def compress_and_size(conn, table, n):
    conn.execute(f"VACUUM ANALYZE {table}")
    before = conn.execute(f"SELECT hypertable_size('{table}')").fetchone()[0]
    t0 = time.perf_counter()
    conn.execute(f"SELECT compress_chunk(c) FROM show_chunks('{table}') c")
    secs = time.perf_counter() - t0
    conn.execute(f"VACUUM ANALYZE {table}")
    after = conn.execute(f"SELECT hypertable_size('{table}')").fetchone()[0]
    stats = conn.execute(f"SELECT total_chunks, number_compressed_chunks, before_compression_total_bytes,"
                         f" after_compression_total_bytes FROM hypertable_compression_stats('{table}')").fetchone()
    return {"uncompressed_bytes": before, "uncompressed_bits_per_sample": 8 * before / n,
            "total_bytes": after, "bits_per_sample": 8 * after / n, "compress_s": secs,
            "chunks_compressed": f"{stats[1]}/{stats[0]}", "stats_after_bytes": stats[3]}


def run_t_rows(conn, table, values, as_int):
    n = len(values)
    hypertable(conn, table, f"rec int NOT NULL, t bigint NOT NULL, v {'bigint' if as_int else 'float8'} NOT NULL", TS_CHUNK)
    fmt = (lambda v: str(int(v))) if as_int else repr
    t0 = time.perf_counter()
    with conn.cursor() as cur, cur.copy(f"COPY {table} (rec, t, v) FROM STDIN") as cp:
        for lo in range(0, n, 1 << 18):
            buf = io.StringIO()
            for i in range(lo, min(lo + (1 << 18), n)):
                buf.write(f"1\t{i}\t{fmt(values[i].item())}\n")
            cp.write(buf.getvalue())
    secs = time.perf_counter() - t0
    r = compress_and_size(conn, table, n)
    r["ingest_s"] = secs
    r["read_back_bitwise"] = all(
        (np.array_equal if as_int else bitwise)(
            np.array([v for (v,) in conn.execute(f"SELECT v FROM {table} WHERE t >= %s AND t < %s ORDER BY t",
                                                 (a, a + READ_N))], np.int64 if as_int else float),
            values[a:a + READ_N]) for a in READ_AT)
    return r


def run_t_arrays(conn, table, d):
    x, starts = d["floats"], np.r_[0, np.cumsum(d["lengths"])]
    hypertable(conn, table, "rec int NOT NULL, t bigint NOT NULL, samples float8[] NOT NULL", TS_CHUNK)
    t0 = time.perf_counter()
    with conn.cursor() as cur, cur.copy(f"COPY {table} (rec, t, samples) FROM STDIN") as cp:
        for j in range(len(d["lengths"])):
            cp.write_row((1, int(starts[j]), x[starts[j]:starts[j + 1]].tolist()))
    secs = time.perf_counter() - t0
    r = compress_and_size(conn, table, len(x))
    r["ingest_s"] = secs
    flat = np.concatenate([np.asarray(s, float) for (s,) in conn.execute(f"SELECT samples FROM {table} ORDER BY t")])
    r["read_back_bitwise"] = bitwise(flat, x)
    return r


def main():
    dump, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    ensure_db(MORLET_DSN)
    res = {}
    with psycopg.connect(MORLET_DSN, autocommit=True) as m, \
            psycopg.connect(TSDB_DSN, password=os.environ["TSDB_PASSWORD"], autocommit=True) as ts:
        res["versions"] = {"pg_morlet": m.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0],
                           "timescaledb": ts.execute("SELECT extversion FROM pg_extension WHERE extname='timescaledb'").fetchone()[0],
                           "timescaledb_license": ts.execute("SHOW timescaledb.license").fetchone()[0]}
        for name in RATES:
            d = dict(np.load(dump / f"{name}.npz"))
            r = {"samples": int(len(d["floats"])), "quantum": float(d["quantum"])}
            r["M"] = run_m(m, name, d)
            tag = name.replace("-", "_")
            r["T-f"] = run_t_rows(ts, f"tf_{tag}", d["floats"], as_int=False)
            if name != "paderborn-physical":
                r["T-i"] = run_t_rows(ts, f"ti_{tag}", d["ints"], as_int=True)
            r["T-a"] = run_t_arrays(ts, f"ta_{tag}", d)
            res[name] = r
            print(name, {k: round(v["bits_per_sample"], 2) for k, v in r.items() if isinstance(v, dict)},
                  {k: v["read_back_bitwise"] for k, v in r.items() if isinstance(v, dict)}, flush=True)
    (out / "timescale.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
