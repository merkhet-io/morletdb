"""B3.18 CedarDB adapter (PROTOCOL.md, "Amendment B3.18"): CedarDB, PostgreSQL wire protocol.

One table per group with an integer time column in nanoseconds (`t BIGINT`, exact sample times), a primary key on
(acquisition, t), loaded with binary COPY over psycopg, as the TimescaleDB stage (B3.7) loads PostgreSQL. CedarDB's
own storage layout and defaults; no tuning. Storage is the data directory on disk (CedarDB's size functions return
a constant).
"""
import os
import secrets
import time

import numpy as np
import psycopg

import common
import workload as wl

IMAGE = "cedardb/cedardb@sha256:737ce1bf2ef3c9f2ba7e599ae4b2a0ae0399125e3f63a417a51141976a40518e"   # v2026-11-04, arm64
NAME = "b3-cedardb"
PORT = 54343
MOUNT = "/var/lib/cedardb/data"
TABLES = {g: f"b3_{g}" for g in wl.GROUPS}
_PASSWORD = os.environ.setdefault("B3_PG_PASSWORD", secrets.token_hex(16))   # per run, never written anywhere
DSN = f"host=127.0.0.1 port={PORT} user=b3 dbname=b3 password={_PASSWORD}"
ENV = {"CEDAR_USER": "b3", "CEDAR_PASSWORD": _PASSWORD, "CEDAR_DB": "b3"}


def start(volume, durability="D2"):
    common.fresh(NAME, IMAGE, volume, MOUNT, [f"{PORT}:5432"], ENV, [])
    _wait()
    return connect(durability)


def restart(volume, durability="D2"):
    common.run(NAME, IMAGE, volume, MOUNT, [f"{PORT}:5432"], ENV, [])
    _wait()
    return connect(durability)


def _wait():
    for _ in range(240):
        try:
            c = psycopg.connect(DSN, connect_timeout=2)
            c.execute("SELECT 1")
            c.close()
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("CedarDB did not start")


def connect(durability="D1"):
    """D2 asks for asynchronous commits per session with CedarDB's own setting (SET async_commit = on; CedarDB refuses
    to change synchronous_commit). Its effect is visible only in the measurements and is reported, not assumed."""
    for _ in range(60):
        try:
            c = psycopg.connect(DSN, autocommit=True)
            if durability == "D2":
                c.execute("SET async_commit = on")
            return c
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("cannot connect")


def server_settings(conn):
    q = lambda s: conn.execute(s).fetchone()[0]
    return {"version": q("SELECT version()"), "synchronous_commit": q("SHOW synchronous_commit"),
            "async_commit": q("SHOW async_commit"), "fsync": q("SHOW fsync")}


def create(conn):
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(f"{c} DOUBLE PRECISION NOT NULL" for c in spec["channels"])
        t = TABLES[g]
        with conn.cursor() as cur:
            cur.execute(f"CREATE TABLE {t} (acquisition TEXT NOT NULL, t BIGINT NOT NULL, {cols}, PRIMARY KEY (acquisition, t))")


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


def vacuum(conn):
    pass   # CedarDB manages its storage itself


def storage(conn):
    return {}


def volume(volume_name):
    files = common.volume_files(volume_name, IMAGE)
    tot = [sum(a for a, _, _ in files), sum(b for _, b, _ in files)]
    parts = {}
    for a, b, p in files:
        top = p.lstrip("./").split("/")[0] if "/" in p.lstrip("./") else "(files)"
        x = parts.setdefault(top, [0, 0, 0])
        x[0] += a
        x[1] += b
        x[2] += 1
    return {"apparent": tot[0], "allocated": tot[1],
            "by_directory": {k: {"apparent": v[0], "allocated": v[1], "files": v[2]} for k, v in sorted(parts.items())}}
