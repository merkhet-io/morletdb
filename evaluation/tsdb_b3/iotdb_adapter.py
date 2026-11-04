"""B3.5 Apache IoTDB adapter (PROTOCOL.md, "Amendment B3.5"): standalone node, table model, two arms.

- table:      one table per group, `acquisition` TAG, channels as DOUBLE FIELDs; image defaults (GORILLA, LZ4)
- table-zstd: the same with the server's compressor set to ZSTD

Timestamps are stored at nanosecond precision (`timestamp_precision=ns`), so every sample's time is exact and
no sample index is stored. Writes go through the official Python client (`TableSession.insert(NumpyTablet)`).
"""
import time

import numpy as np
from iotdb.table_session import TableSession, TableSessionConfig
from iotdb.utils.IoTDBConstants import TSDataType
from iotdb.utils.NumpyTablet import NumpyTablet
from iotdb.utils.Tablet import ColumnType

import common
import workload as wl

IMAGE = "apache/iotdb:2.0.11-standalone"   # linux/arm64 sha256:a04c66a9f5bb72ed93fe9cc1a35160da2e398f75539afb1bd7b99cc0da891e1c
NAME = "b3-iotdb"
PORT = 6668                # host port for the native RPC port 6667
MOUNT = "/iotdb/data"
DB = "b3"
TABLES = {g: f"b3_{g}" for g in wl.GROUPS}
TABLET_ROWS = 100_000      # rows per tablet (fixed in development, before the freeze)
FETCH_SIZE = 65_536        # rows per result fetch (fixed in development: default 5,000 vs 65,536 vs 200,000)
USER, PASSWORD = "root", "root"   # image default; the container port is bound to localhost only


def env(arm, durability):
    e = {"timestamp_precision": "ns", "dn_rpc_address": "0.0.0.0",
         "wal_mode": "SYNC" if durability == "D1" else "ASYNC"}
    if arm == "table-zstd":
        e["compressor"] = "ZSTD"
    return e


def start(volume, arm, durability):
    common.fresh(NAME, IMAGE, volume, MOUNT, [f"{PORT}:6667"], env(arm, durability))
    return _wait()


def restart(volume, arm, durability):
    common.run(NAME, IMAGE, volume, MOUNT, [f"{PORT}:6667"], env(arm, durability))
    return _wait()


def connect(database=DB):
    return TableSession(TableSessionConfig(node_urls=[f"127.0.0.1:{PORT}"], username=USER, password=PASSWORD,
                                           database=database, fetch_size=FETCH_SIZE))


def _wait():
    for _ in range(240):
        try:
            s = connect(None)
            with s.execute_query_statement("SHOW DATABASES") as d:
                d.todf()
            s.close()
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("IoTDB did not start")


def server_settings():
    """The settings the server actually runs with (from its configuration file and JVM options)."""
    conf = common.sh(["docker", "exec", NAME, "sh", "-c",
                      "grep -E '^(timestamp_precision|wal_mode|compressor|default_double_encoding|"
                      "wal_async_mode_fsync_delay_in_ms)=' /iotdb/conf/iotdb-system.properties"], check=False)
    jvm = common.sh(["docker", "exec", NAME, "sh", "-c", "ps -eo args | grep -o -- '-Xmx[0-9]*[mMgG]' | sort -u"],
                    check=False)
    return {"properties": conf.split(), "xmx": jvm.split()}


def create(session):
    session.execute_non_query_statement(f"CREATE DATABASE IF NOT EXISTS {DB}")
    session.execute_non_query_statement(f"USE {DB}")
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(f"{c} DOUBLE FIELD" for c in spec["channels"])
        session.execute_non_query_statement(f"CREATE TABLE {TABLES[g]} (acquisition STRING TAG, {cols})")


def tablets(acq, data):
    """Per group: NumpyTablets of at most TABLET_ROWS rows. Built before the timed calls."""
    out = {}
    for g, spec in wl.GROUPS.items():
        chans = spec["channels"]
        n = len(data[chans[0]])
        ts = (acq["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]).astype(">i8")
        cols = ["acquisition", *chans]
        types = [TSDataType.STRING] + [TSDataType.DOUBLE] * len(chans)
        kinds = [ColumnType.TAG] + [ColumnType.FIELD] * len(chans)
        parts = []
        for i in range(0, max(n, 1), TABLET_ROWS):
            j = min(n, i + TABLET_ROWS)
            vals = [np.array([acq["acquisition"]] * (j - i))] + [np.asarray(data[c][i:j], dtype=">f8") for c in chans]
            parts.append(NumpyTablet(TABLES[g], cols, types, vals, ts[i:j], column_types=kinds))
        out[g] = parts
    return out


def load_one(session, acq, data):
    """Ingest one acquisition; returns (conversion seconds, [per-call acknowledgement seconds])."""
    t = time.perf_counter()
    payload = tablets(acq, data)
    conv = time.perf_counter() - t
    acks = []
    for g in wl.GROUPS:
        for tab in payload[g]:
            t = time.perf_counter()
            session.insert(tab)
            acks.append(time.perf_counter() - t)
    return conv, acks


def count(session):
    out = {}
    for g, spec in wl.GROUPS.items():
        with session.execute_query_statement(f"SELECT count({spec['channels'][0]}) FROM {TABLES[g]}") as d:
            out[g] = int(d.todf().values[0][0])
    return out


def read(session, acq, channels, offset, length, group="high"):
    """Exact samples [offset, offset+length) of the channels as float64 arrays, and their times (int64 ns)."""
    period = wl.GROUPS[group]["period_ns"]
    t0 = acq["start_ns"] + offset * period
    t1 = acq["start_ns"] + (offset + length) * period
    # time as INT64: the client converts a TIMESTAMP column element by element into time-zone-aware datetimes
    # (10x slower in development), while INT64 arrives as a numpy array; both are exact nanoseconds.
    q = (f"SELECT CAST(time AS INT64) AS t, {', '.join(channels)} FROM {TABLES[group]} WHERE acquisition = '{acq['acquisition']}' "
         f"AND time >= {t0} AND time < {t1} ORDER BY time")
    with session.execute_query_statement(q) as d:
        df = d.todf()
    times = df.iloc[:, 0].to_numpy(dtype=np.int64) if len(df) else np.empty(0, dtype=np.int64)
    return times, {c: df[c].to_numpy(dtype=np.float64) if len(df) else np.empty(0) for c in channels}


def analytics(session, acq, offset, length):
    period = wl.GROUPS["high"]["period_ns"]
    t0 = acq["start_ns"] + offset * period
    t1 = acq["start_ns"] + (offset + length) * period
    q = (f"SELECT min(vibration_1), max(vibration_1), avg(vibration_1), sqrt(avg(vibration_1 * vibration_1)), "
         f"count(vibration_1) FROM {TABLES['high']} WHERE acquisition = '{acq['acquisition']}' "
         f"AND time >= {t0} AND time < {t1}")
    with session.execute_query_statement(q) as d:
        return [float(v) for v in d.todf().values[0]]


def flush(session):
    session.execute_non_query_statement("FLUSH")


def settle_files(volume, quiet_s=60, timeout_s=1800):
    """Wait until the data directory has not changed for quiet_s seconds (background compaction idle)."""
    def snapshot():
        return common.sh(["docker", "exec", NAME, "sh", "-c",
                          f"find {MOUNT} -type f -printf '%p %s %T@\\n' | sort | md5sum"])
    t0 = time.perf_counter()
    last, since = snapshot(), time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        time.sleep(5)
        cur = snapshot()
        if cur != last:
            last, since = cur, time.perf_counter()
        elif time.perf_counter() - since >= quiet_s:
            return round(time.perf_counter() - t0, 1)
    raise RuntimeError("IoTDB data directory did not settle")


def storage(volume):
    """By file class: TsFiles (sequence, unsequence) of database b3, WAL, ConfigNode, other DataNode files."""
    files = common.volume_files(volume, IMAGE)
    out = {}
    for app, alloc, path in files:
        p = path.split("/")
        if "confignode" in p:
            cls = "confignode"
        elif "wal" in p:
            cls = "wal"
        elif path.endswith(".tsfile") or path.endswith(".tsfile.resource") or path.endswith(".mods"):
            cls = ("tsfile_" + ("sequence" if "sequence" in p else "unsequence" if "unsequence" in p else "other")
                   + ("_" + DB if any(x == DB or x.startswith(DB + "_") or x == f"root.{DB}" for x in p) else "_system"))
        else:
            cls = "datanode_other"
        d = out.setdefault(cls, [0, 0, 0])
        d[0] += app
        d[1] += alloc
        d[2] += 1
    tot = [sum(v[0] for v in out.values()), sum(v[1] for v in out.values())]
    return {"classes": {k: {"apparent": v[0], "allocated": v[1], "files": v[2]} for k, v in sorted(out.items())},
            "volume": {"apparent": tot[0], "allocated": tot[1]}}
