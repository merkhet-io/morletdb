"""B3.6 TDengine adapter (PROTOCOL.md, "Amendment B3.6"): single node, supertables, three compression arms.

- default: DOUBLE with TDengine's defaults (encoding bss, compression lz4, level medium)
- zstd:    encoding bss, compression zstd, level high
- tsz:     encoding disabled, compression tsz (TDengine's float compressor)

Database precision is nanoseconds, so every sample's time is exact. Writes use the official Python WebSocket
connector (taos-ws-py) with stmt2 parameter binding through the image's taosAdapter.
"""
import time

import numpy as np
import taosws

import common
import workload as wl

IMAGE = "tdengine/tsdb:3.4.3.3"   # linux/arm64 sha256:a35f623203ffcbafea6817812080b54cbfa3e42aba3c4878d7713d67d814e877
NAME = "b3-tdengine"
PORT = 6042                # host port for taosAdapter's 6041 (WebSocket)
MOUNT = "/var/lib/taos"
DB = "b3"
STABLES = {g: f"b3_{g}" for g in wl.GROUPS}
BIND_ROWS = 32_767         # rows per stmt2 bind: the maximum one bind accepts (found in development)
USER, PASSWORD = "root", "taosdata"   # image default; the container port is bound to localhost only
ARMS = {"default": "", "zstd": " ENCODE 'bss' COMPRESS 'zstd' LEVEL 'high'",
        "tsz": " ENCODE 'disabled' COMPRESS 'tsz'"}
_state = {}


def start(volume, arm, durability):
    _state.update(arm=arm, durability=durability)
    common.fresh(NAME, IMAGE, volume, MOUNT, [f"{PORT}:6041"])
    _wait()


def restart(volume, arm, durability):
    _state.update(arm=arm, durability=durability)
    common.run(NAME, IMAGE, volume, MOUNT, [f"{PORT}:6041"])
    _wait(DB)


def connect(database=DB):
    conn = taosws.connect(user=USER, password=PASSWORD, host="127.0.0.1", port=PORT)
    if database:
        conn.execute(f"USE {database}")
    return conn


def _wait(database=None):
    for _ in range(240):
        try:
            conn = connect(None)
            list(conn.query("SHOW DATABASES"))
            if database:
                list(conn.query(f"SELECT COUNT(*) FROM {database}.{STABLES['process']}"))
            conn.close()
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("TDengine did not start")


def server_version(conn):
    return [list(r) for r in conn.query("SELECT SERVER_VERSION()")]


def server_settings():
    conn = connect(None)
    out = [list(r) for r in conn.query(f"SHOW CREATE DATABASE {DB}")]
    cols = [list(r) for r in conn.query(f"DESCRIBE {DB}.{STABLES['high']}")]
    conn.close()
    return {"database": out, "high_columns": cols}


def create(conn):
    wal = "WAL_LEVEL 2 WAL_FSYNC_PERIOD 0" if _state["durability"] == "D1" else "WAL_LEVEL 1"
    conn.execute(f"CREATE DATABASE IF NOT EXISTS {DB} PRECISION 'ns' {wal}")
    conn.execute(f"USE {DB}")
    opt = ARMS[_state["arm"]]
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(f"`{c}` DOUBLE{opt}" for c in spec["channels"])
        conn.execute(f"CREATE STABLE {STABLES[g]} (ts TIMESTAMP, {cols}) TAGS (acquisition VARCHAR(64))")


def _sub(acq, g):
    return f"{STABLES[g]}_{acq['k']}"


def binds(acq, data):
    """Per group: stmt2 bind parameters of at most BIND_ROWS rows. Built before the timed calls."""
    out = {}
    for g, spec in wl.GROUPS.items():
        chans = spec["channels"]
        n = len(data[chans[0]])
        ts = acq["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]
        parts = []
        for i in range(0, max(n, 1), BIND_ROWS):
            j = min(n, i + BIND_ROWS)
            cols = [taosws.nanos_timestamps_to_column(ts[i:j].tolist())]
            cols += [taosws.doubles_to_column(np.asarray(data[c][i:j], dtype=np.float64).tolist()) for c in chans]
            parts.append(taosws.stmt2_bind_param_view(table_name=_sub(acq, g),
                                                      tags=[taosws.varchar_to_tag(acq["acquisition"])],
                                                      columns=cols))
        out[g] = parts
    return out


def load_one(conn, acq, data):
    """Ingest one acquisition; returns (conversion seconds, [per-call acknowledgement seconds])."""
    t = time.perf_counter()
    payload = binds(acq, data)
    conv = time.perf_counter() - t
    acks = []
    for g, spec in wl.GROUPS.items():
        stmt = conn.stmt2_statement()
        stmt.prepare(f"INSERT INTO ? USING {STABLES[g]} TAGS(?) VALUES ({', '.join('?' * (1 + len(spec['channels'])))})")
        for p in payload[g]:
            t = time.perf_counter()
            stmt.bind([p])
            stmt.execute()
            acks.append(time.perf_counter() - t)
        stmt.close()
    return conv, acks


def count(conn):
    out = {}
    for g, spec in wl.GROUPS.items():
        out[g] = int(list(conn.query(f"SELECT COUNT(`{spec['channels'][0]}`) FROM {STABLES[g]}"))[0][0])
    return out


def read(conn, acq, channels, offset, length, group="high"):
    """Exact samples [offset, offset+length) of the channels as float64 arrays, and their times (int64 ns).
    Time is selected as BIGINT: the WebSocket client returns TIMESTAMP values as text."""
    period = wl.GROUPS[group]["period_ns"]
    t0 = acq["start_ns"] + offset * period
    t1 = acq["start_ns"] + (offset + length) * period
    rows = list(conn.query(f"SELECT CAST(ts AS BIGINT), {', '.join(f'`{c}`' for c in channels)} FROM {_sub(acq, group)} "
                           f"WHERE ts >= {t0} AND ts < {t1} ORDER BY ts"))
    if not rows:
        return np.empty(0, dtype=np.int64), {c: np.empty(0) for c in channels}
    cols = list(zip(*rows))
    return np.array(cols[0], dtype=np.int64), {c: np.array(cols[i + 1], dtype=np.float64)
                                               for i, c in enumerate(channels)}


def analytics(conn, acq, offset, length):
    period = wl.GROUPS["high"]["period_ns"]
    t0 = acq["start_ns"] + offset * period
    t1 = acq["start_ns"] + (offset + length) * period
    r = list(conn.query(f"SELECT MIN(vibration_1), MAX(vibration_1), AVG(vibration_1), "
                        f"SQRT(AVG(vibration_1 * vibration_1)), COUNT(vibration_1) FROM {_sub(acq, 'high')} "
                        f"WHERE ts >= {t0} AND ts < {t1}"))[0]
    return [float(v) for v in r]


def flush(conn):
    conn.execute(f"FLUSH DATABASE {DB}")


def compact(conn, timeout_s=1800):
    conn.execute(f"COMPACT DATABASE {DB}")
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        if not list(conn.query("SHOW COMPACTS")):
            return round(time.perf_counter() - t0, 1)
        time.sleep(2)
    raise RuntimeError("compaction did not finish")


def settle_files(volume, quiet_s=60, timeout_s=1800):
    """Wait until no file outside the write-ahead logs has changed for quiet_s seconds. An internal database's
    WAL grows continuously on an idle server (found in development), so WAL files are not part of the test."""
    def snapshot():
        return common.sh(["docker", "exec", NAME, "sh", "-c",
                          f"find {MOUNT} -type f -not -path '*/wal/*' -printf '%p %s %T@\\n' | sort | md5sum"])
    t0 = time.perf_counter()
    last, since = snapshot(), time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        time.sleep(5)
        cur = snapshot()
        if cur != last:
            last, since = cur, time.perf_counter()
        elif time.perf_counter() - since >= quiet_s:
            return round(time.perf_counter() - t0, 1)
    raise RuntimeError("TDengine data directory did not settle")


def storage(volume):
    """By file class: vnode time-series data (tsdb), WAL, vnode metadata, dnode/mnode and other files."""
    files = common.volume_files(volume, IMAGE)
    out = {}
    for app, alloc, path in files:
        p = path.split("/")
        if "vnode" in p and "tsdb" in p:
            cls = "vnode_tsdb"
        elif "wal" in p:
            cls = "wal"
        elif "vnode" in p:
            cls = "vnode_other"
        elif "mnode" in p:
            cls = "mnode"
        else:
            cls = "other"
        d = out.setdefault(cls, [0, 0, 0])
        d[0] += app
        d[1] += alloc
        d[2] += 1
    tot = [sum(v[0] for v in out.values()), sum(v[1] for v in out.values())]
    return {"classes": {k: {"apparent": v[0], "allocated": v[1], "files": v[2]} for k, v in sorted(out.items())},
            "volume": {"apparent": tot[0], "allocated": tot[1]}}
