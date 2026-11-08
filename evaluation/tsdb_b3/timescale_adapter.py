"""B3.7 TimescaleDB adapter (PROTOCOL.md, "Amendment B3.7"): PostgreSQL 17 with TimescaleDB 2.31.0.

One hypertable per group with an integer time dimension in nanoseconds (`t BIGINT`, exact sample times; PostgreSQL
timestamps resolve microseconds only), a unique index on (acquisition, t), loaded with binary COPY. Two states of
one arm on the same load: TS-row (rowstore after ingestion) and TS-col (columnstore after compress_chunk with
segmentby acquisition, orderby t, default per-type algorithms).
"""
import os
import secrets
import time

import numpy as np
import psycopg

import common
import workload as wl

IMAGE = "timescale/timescaledb:2.31.0-pg17"   # linux/arm64 sha256:d971568f9af05e218866181151117f517282f05f299a0e4010dc6a97704f6f2c
NAME = "b3-timescale"
PORT = 54341
MOUNT = "/var/lib/postgresql/data"
TABLES = {g: f"b3_{g}" for g in wl.GROUPS}
CHUNK_NS = 86_400 * 10**9   # one day in nanoseconds
_PASSWORD = os.environ.setdefault("B3_PG_PASSWORD", secrets.token_hex(16))   # per run, never written anywhere
DSN = f"host=127.0.0.1 port={PORT} user=b3 dbname=b3 password={_PASSWORD}"


def start(volume, durability="D2"):
    cmd = ["postgres"] + (["-c", "synchronous_commit=off"] if durability == "D2" else [])
    common.fresh(NAME, IMAGE, volume, MOUNT, [f"{PORT}:5432"],
                 {"POSTGRES_USER": "b3", "POSTGRES_PASSWORD": _PASSWORD, "POSTGRES_DB": "b3",
                  "NO_TS_TUNE": "true"}, cmd)   # PostgreSQL defaults, as for MorletDB in B3.3 (no timescaledb-tune)
    _wait()
    time.sleep(3)   # the entrypoint restarts the server once after init: wait for the final one
    conn = connect()
    conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    sc = conn.execute("SHOW synchronous_commit").fetchone()[0]
    if sc != ("off" if durability == "D2" else "on"):
        raise RuntimeError(f"synchronous_commit is {sc}")
    return conn


def restart(volume, durability="D2"):
    cmd = ["postgres"] + (["-c", "synchronous_commit=off"] if durability == "D2" else [])
    common.run(NAME, IMAGE, volume, MOUNT, [f"{PORT}:5432"],
               {"POSTGRES_USER": "b3", "POSTGRES_PASSWORD": _PASSWORD, "POSTGRES_DB": "b3", "NO_TS_TUNE": "true"}, cmd)
    _wait()
    return connect()


def _wait():
    for _ in range(240):
        try:
            c = psycopg.connect(DSN, connect_timeout=2)
            c.execute("SELECT 1")
            c.close()
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("TimescaleDB did not start")


def connect():
    for _ in range(60):
        try:
            return psycopg.connect(DSN, autocommit=True)
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("cannot connect")


def server_settings(conn):
    q = lambda s: conn.execute(s).fetchone()[0]
    return {"server_version": q("SHOW server_version"),
            "timescaledb": q("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"),
            "synchronous_commit": q("SHOW synchronous_commit"), "shared_buffers": q("SHOW shared_buffers"),
            "max_wal_size": q("SHOW max_wal_size")}


def create(conn):
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(f"{c} DOUBLE PRECISION NOT NULL" for c in spec["channels"])
        t = TABLES[g]
        conn.execute(f"CREATE TABLE {t} (acquisition TEXT NOT NULL, t BIGINT NOT NULL, {cols})")
        conn.execute(f"SELECT create_hypertable('{t}', by_range('t', {CHUNK_NS}))")
        conn.execute(f"CREATE UNIQUE INDEX ON {t} (acquisition, t)")


def rows(acq, data):
    """Per group: COPY rows (acquisition, t, channels...) as Python tuples. Built before the timed calls."""
    out = {}
    for g, spec in wl.GROUPS.items():
        chans = spec["channels"]
        n = len(data[chans[0]])
        ts = acq["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]
        cols = [ts.tolist()] + [np.asarray(data[c], dtype=np.float64).tolist() for c in chans]
        out[g] = list(zip([acq["acquisition"]] * n, *cols))
    return out


def load_one(conn, acq, data):
    """Ingest one acquisition with binary COPY; returns (conversion seconds, [per-call seconds])."""
    t = time.perf_counter()
    payload = rows(acq, data)
    conv = time.perf_counter() - t
    acks = []
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(["acquisition", "t", *spec["channels"]])
        t = time.perf_counter()
        with conn.cursor() as cur:
            with cur.copy(f"COPY {TABLES[g]} ({cols}) FROM STDIN (FORMAT BINARY)") as cp:
                cp.set_types(["text", "int8"] + ["float8"] * len(spec["channels"]))
                for r in payload[g]:
                    cp.write_row(r)
        acks.append(time.perf_counter() - t)
    return conv, acks


def count(conn):
    return {g: int(conn.execute(f"SELECT count(*) FROM {TABLES[g]}").fetchone()[0]) for g in wl.GROUPS}


def read(conn, acq, channels, offset, length, group="high"):
    period = wl.GROUPS[group]["period_ns"]
    t0 = acq["start_ns"] + offset * period
    t1 = acq["start_ns"] + (offset + length) * period
    with conn.cursor(binary=True) as cur:
        cur.execute(f"SELECT t, {', '.join(channels)} FROM {TABLES[group]} WHERE acquisition = %s AND t >= %s "
                    f"AND t < %s ORDER BY t", (acq["acquisition"], t0, t1))
        got = cur.fetchall()
    if not got:
        return np.empty(0, dtype=np.int64), {c: np.empty(0) for c in channels}
    cols = list(zip(*got))
    return np.array(cols[0], dtype=np.int64), {c: np.array(cols[i + 1], dtype=np.float64)
                                               for i, c in enumerate(channels)}


def analytics(conn, acq, offset, length):
    period = wl.GROUPS["high"]["period_ns"]
    t0 = acq["start_ns"] + offset * period
    t1 = acq["start_ns"] + (offset + length) * period
    r = conn.execute(f"SELECT min(vibration_1), max(vibration_1), avg(vibration_1), "
                     f"sqrt(avg(vibration_1 * vibration_1)), count(*) FROM {TABLES['high']} "
                     f"WHERE acquisition = %s AND t >= %s AND t < %s", (acq["acquisition"], t0, t1)).fetchone()
    return [float(v) for v in r]


def compress(conn):
    """Columnstore with the documented options; returns seconds and the number of chunks compressed."""
    t = time.perf_counter()
    n = 0
    for g in wl.GROUPS:
        conn.execute(f"ALTER TABLE {TABLES[g]} SET (timescaledb.compress, timescaledb.compress_segmentby = "
                     f"'acquisition', timescaledb.compress_orderby = 't')")
        n += len(conn.execute(f"SELECT compress_chunk(c) FROM show_chunks('{TABLES[g]}') c").fetchall())
    return round(time.perf_counter() - t, 3), n


def vacuum(conn):
    for g in wl.GROUPS:
        conn.execute(f"VACUUM ANALYZE {TABLES[g]}")


def storage(conn):
    """Per hypertable (table, index, toast, total bytes), the database, and pg_wal."""
    per = {}
    for g in wl.GROUPS:
        r = conn.execute(f"SELECT table_bytes, index_bytes, toast_bytes, total_bytes FROM "
                         f"hypertable_detailed_size('{TABLES[g]}')").fetchone()
        per[g] = dict(zip(("table", "index", "toast", "total"), (int(v or 0) for v in r)))
    wal = conn.execute("SELECT coalesce(sum(size), 0) FROM pg_ls_waldir()").fetchone()[0]
    db = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    return {"hypertables": per, "hypertables_total": sum(v["total"] for v in per.values()),
            "database": int(db), "pg_wal": int(wal)}


def volume(volume_name):
    files = common.volume_files(volume_name, IMAGE)
    tot = [sum(a for a, _, _ in files), sum(b for _, b, _ in files)]
    wal = [sum(a for a, _, p in files if p.startswith("pg_wal/")), sum(b for _, b, p in files if p.startswith("pg_wal/"))]
    return {"apparent": tot[0], "allocated": tot[1], "pg_wal_apparent": wal[0], "pg_wal_allocated": wal[1]}
