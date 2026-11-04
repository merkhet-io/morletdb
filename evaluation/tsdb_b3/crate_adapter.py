"""B3.4 CrateDB adapter (PROTOCOL.md, "Amendment B3.4"): single node, three arms.

- row:      one row per sample instant, channels as columns, default codec (LZ4)
- row-best: the same with codec = 'best_compression'
- array:    one row per (acquisition, channel, 4,096-sample block), DOUBLE array with INDEX OFF, best_compression

CrateDB timestamps have millisecond resolution, so every arm stores the sample index n; time is start + n x period.
Statements go through the official Python client over HTTP (bulk inserts via executemany -> bulk_args).
"""
import time

import numpy as np
from crate import client

import common
import workload as wl

IMAGE = "crate:6.4.6"   # linux/arm64 sha256:865ed64fd4e7ffc31dccd79ec96d374f7da458e23e65520699e31ad4f21775ef
NAME = "b3-crate"
PORT = 4201             # host port for CrateDB's HTTP endpoint (4200 in the container)
URL = f"http://127.0.0.1:{PORT}"
ROW_TABLES = {g: f"b3_{g}" for g in wl.GROUPS}
ARRAY_TABLE = "b3_blocks"
BLOCK = 4096
ROW_BATCH = 10_000


def start(volume, durability="D2"):
    common.fresh(NAME, IMAGE, volume, "/data", [f"{PORT}:4200"], {"CRATE_HEAP_SIZE": "8g"},
                 ["crate", "-Cdiscovery.type=single-node"])
    return _wait()


def restart(volume):
    """Same volume, server started again; returns once every shard has recovered (state STARTED)."""
    common.run(NAME, IMAGE, volume, "/data", [f"{PORT}:4200"], {"CRATE_HEAP_SIZE": "8g"},
               ["crate", "-Cdiscovery.type=single-node"])
    conn = _wait()
    cur = conn.cursor()
    for _ in range(600):
        cur.execute("SELECT count(*), count(*) FILTER (WHERE state <> 'STARTED') FROM sys.shards "
                    "WHERE schema_name = 'doc'")
        total, pending = cur.fetchone()
        if total > 0 and pending == 0:
            return conn
        time.sleep(0.5)
    raise RuntimeError("CrateDB shards did not recover")


def connect():
    return client.connect(URL, timeout=600)


def _wait():
    for _ in range(240):
        try:
            conn = connect()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            return conn
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("CrateDB did not start")


def _with(arm, durability):
    opts = ["number_of_replicas = 0", f"\"translog.durability\" = '{'REQUEST' if durability == 'D1' else 'ASYNC'}'"]
    if arm in ("row-best", "array"):
        opts.append("codec = 'best_compression'")
    return "WITH (" + ", ".join(opts) + ")"


def create(conn, arm, durability):
    cur = conn.cursor()
    if arm == "array":
        cur.execute(f"CREATE TABLE {ARRAY_TABLE} (acquisition TEXT, channel TEXT, block INTEGER, "
                    f"samples ARRAY(DOUBLE PRECISION) INDEX OFF, PRIMARY KEY (acquisition, channel, block)) "
                    f"{_with(arm, durability)}")
        return
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(f"{c} DOUBLE PRECISION" for c in spec["channels"])
        cur.execute(f"CREATE TABLE {ROW_TABLES[g]} (acquisition TEXT, n BIGINT, ts TIMESTAMP WITH TIME ZONE, "
                    f"part TIMESTAMP WITH TIME ZONE GENERATED ALWAYS AS date_trunc('day', ts), {cols}, "
                    f"PRIMARY KEY (acquisition, n, part)) PARTITIONED BY (part) {_with(arm, durability)}")


def tables(arm):
    return [ARRAY_TABLE] if arm == "array" else list(ROW_TABLES.values())


def rows(acq, data):
    """Per group: the parameter rows (acquisition, n, ts in ms, channels...). Built before the timed calls."""
    out = {}
    for g, spec in wl.GROUPS.items():
        n = len(data[spec["channels"][0]])
        idx = np.arange(n, dtype=np.int64)
        ts_ms = (acq["start_ns"] + idx * spec["period_ns"]) // 1_000_000
        cols = [data[c].tolist() for c in spec["channels"]]
        out[g] = list(zip([acq["acquisition"]] * n, idx.tolist(), ts_ms.tolist(), *cols))
    return out


def blocks(acq, data):
    out = []
    for c in wl.UNITS:
        x = data[c]
        for b in range(0, max(1, -(-len(x) // BLOCK))):
            out.append((acq["acquisition"], c, b, x[b * BLOCK:(b + 1) * BLOCK].tolist()))
    return out


def load_one(conn, arm, acq, data):
    """Ingest one acquisition; returns (conversion seconds, [per-call acknowledgement seconds])."""
    cur = conn.cursor()
    t = time.perf_counter()
    payload = blocks(acq, data) if arm == "array" else rows(acq, data)
    conv = time.perf_counter() - t
    acks = []
    if arm == "array":
        t = time.perf_counter()
        res = cur.executemany(f"INSERT INTO {ARRAY_TABLE} (acquisition, channel, block, samples) VALUES (?, ?, ?, ?) "
                              f"ON CONFLICT DO NOTHING", payload)
        acks.append(time.perf_counter() - t)
        _check_bulk(res)
        return conv, acks
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(spec["channels"])
        marks = ", ".join("?" for _ in range(3 + len(spec["channels"])))
        stmt = f"INSERT INTO {ROW_TABLES[g]} (acquisition, n, ts, {cols}) VALUES ({marks}) ON CONFLICT DO NOTHING"
        rs = payload[g]
        for i in range(0, len(rs), ROW_BATCH):
            t = time.perf_counter()
            res = cur.executemany(stmt, rs[i:i + ROW_BATCH])
            acks.append(time.perf_counter() - t)
            _check_bulk(res)
    return conv, acks


def _check_bulk(res):
    """A bulk insert reports -2 for a row that failed; ON CONFLICT DO NOTHING reports 0 for a skipped one."""
    bad = [r for r in res if r.get("rowcount", 0) == -2]
    if bad:
        raise RuntimeError(f"{len(bad)} rows failed in a bulk insert")


def refresh(conn, arm):
    cur = conn.cursor()
    for t in tables(arm):
        cur.execute(f"REFRESH TABLE {t}")


def count(conn, arm):
    cur = conn.cursor()
    if arm == "array":
        cur.execute(f"SELECT channel, sum(array_length(samples, 1)) FROM {ARRAY_TABLE} GROUP BY channel")
        return {c: int(v) for c, v in cur.fetchall()}
    out = {}
    for g in wl.GROUPS:
        cur.execute(f"SELECT count(*) FROM {ROW_TABLES[g]}")
        out[g] = int(cur.fetchone()[0])
    return out


def read(conn, arm, acq, channels, offset, length, group="high"):
    """Exact samples [offset, offset+length) of the channels as float64 arrays, and the sample indexes (row arms)."""
    cur = conn.cursor()
    if arm == "array":
        b0, b1 = offset // BLOCK, (offset + length - 1) // BLOCK
        out = {}
        for c in channels:
            cur.execute(f"SELECT block, samples FROM {ARRAY_TABLE} WHERE acquisition = ? AND channel = ? "
                        f"AND block >= ? AND block <= ? ORDER BY block", (acq["acquisition"], c, b0, b1))
            got = cur.fetchall()
            if [r[0] for r in got] != list(range(b0, b1 + 1)):
                raise RuntimeError(f"missing blocks for {acq['acquisition']} {c}")
            x = np.concatenate([np.asarray(r[1], dtype=np.float64) for r in got])
            s = offset - b0 * BLOCK
            out[c] = x[s:s + length]
        return None, out
    cur.execute(f"SELECT n, {', '.join(channels)} FROM {ROW_TABLES[group]} WHERE acquisition = ? AND n >= ? "
                f"AND n < ? ORDER BY n LIMIT ?", (acq["acquisition"], offset, offset + length, length))
    got = cur.fetchall()
    a = np.asarray(got, dtype=object)
    if len(got) == 0:
        return np.empty(0, dtype=np.int64), {c: np.empty(0) for c in channels}
    n = np.asarray(a[:, 0], dtype=np.int64)
    return n, {c: np.asarray(a[:, i + 1], dtype=np.float64) for i, c in enumerate(channels)}


def analytics(conn, arm, acq, offset, length):
    if arm == "array":   # no SQL over array slices: computed in the client after the read (reported as such)
        _, v = read(conn, arm, acq, ["vibration_1"], offset, length)
        x = v["vibration_1"]
        return [float(x.min()), float(x.max()), float(x.mean()), float(np.sqrt(np.mean(x * x))), x.size]
    cur = conn.cursor()
    cur.execute(f"SELECT min(vibration_1), max(vibration_1), avg(vibration_1), sqrt(avg(vibration_1 * vibration_1)), "
                f"count(*) FROM {ROW_TABLES['high']} WHERE acquisition = ? AND n >= ? AND n < ?",
                (acq["acquisition"], offset, offset + length))
    return [float(v) for v in cur.fetchone()]


def optimize(conn, arm, single_segment=False):
    cur = conn.cursor()
    t = time.perf_counter()
    for tb in tables(arm):
        cur.execute(f"OPTIMIZE TABLE {tb}" + (" WITH (max_num_segments = 1)" if single_segment else ""))
    return time.perf_counter() - t


def shard_paths(conn):
    """Table of every shard directory (container path), to attribute volume files to tables."""
    cur = conn.cursor()
    cur.execute("SELECT table_name, id, path, size, num_docs FROM sys.shards WHERE schema_name = 'doc'")
    return [dict(zip(("table", "shard", "path", "size", "docs"), r)) for r in cur.fetchall()]


def storage(volume, shards):
    """Per table: Lucene segment files (data), translog, shard state; plus cluster metadata and volume totals."""
    files = common.volume_files(volume, IMAGE)
    roots = sorted(((s["path"].removeprefix("/data/"), s["table"]) for s in shards), key=lambda x: -len(x[0]))
    per, total, other = {}, [0, 0], [0, 0]
    for app, alloc, path in files:
        total[0] += app
        total[1] += alloc
        hit = next((t for root, t in roots if path.startswith(root + "/")), None)
        if hit is None:
            other[0] += app
            other[1] += alloc
            continue
        rest = path.split("/")
        cls = "translog" if "translog" in rest else "data" if "index" in rest else "shard_state"
        d = per.setdefault(hit, {}).setdefault(cls, [0, 0, 0])
        d[0] += app
        d[1] += alloc
        d[2] += 1
    return {"tables": {t: {c: {"apparent": v[0], "allocated": v[1], "files": v[2]} for c, v in cl.items()}
                       for t, cl in per.items()},
            "other": {"apparent": other[0], "allocated": other[1]},
            "volume": {"apparent": total[0], "allocated": total[1]}}
