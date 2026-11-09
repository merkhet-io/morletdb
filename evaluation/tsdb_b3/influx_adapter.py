"""B3.19 InfluxDB 3 Core adapter (PROTOCOL.md, "Amendment B3.19"): InfluxDB 3 Core 3.12.2, its HTTP API.

One table (measurement) per group, the acquisition as a tag, the group's channels as float fields, nanosecond
timestamps. Writes: line protocol over one persistent HTTP connection (`/api/v3/write_lp`, precision nanosecond),
requests of at most ROWS_PER_REQUEST rows (the server's default request limit is 10 MB). Floats are written as their
shortest round-trip decimal (Arrow's float-to-string cast), so every value is exact. D1 acknowledges after the WAL is
persisted (the default, flushed every second); D2 sets no_sync=true. Reads: SQL over `/api/v3/query_sql` with
format=parquet, exact float64. Default server settings; local file object store; no authentication (local only).
"""
import http.client
import io
import json
import time
import urllib.parse

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

import common
import workload as wl

IMAGE = "influxdb@sha256:26875ba5ae6e843fdbd208b1ac4a55fa1d2ec3aa7faf2d0d1b5035cb1f998990"   # 3.12.2-core, arm64
NAME = "b3-influxdb3"
PORT = 58181
MOUNT = "/var/lib/influxdb3"   # the image creates it for its user; a fresh volume inherits the owner
DB = "b3"
CMD = ["influxdb3", "serve", "--node-id", "b3", "--object-store", "file", "--data-dir", MOUNT + "/data", "--without-auth"]
ROWS_PER_REQUEST = 50_000


class Conn:
    """One persistent HTTP connection; durability decides no_sync on writes."""

    def __init__(self, durability="D1"):
        self.durability = durability
        self.http = http.client.HTTPConnection("127.0.0.1", PORT, timeout=600)

    def request(self, method, path, body=None, headers=None):
        for attempt in range(2):
            try:
                self.http.request(method, path, body=body, headers=headers or {})
                r = self.http.getresponse()
                return r.status, r.read()
            except (http.client.HTTPException, ConnectionError, OSError):
                self.http.close()
                self.http = http.client.HTTPConnection("127.0.0.1", PORT, timeout=600)
                if attempt:
                    raise

    def sql(self, q, fmt="json"):
        status, body = self.request("GET", "/api/v3/query_sql?" + urllib.parse.urlencode({"db": DB, "q": q, "format": fmt}))
        if status != 200:
            raise RuntimeError(f"query {status}: {body[:300]!r}")
        return body

    def close(self):
        self.http.close()


def start(volume, durability="D2"):
    common.fresh(NAME, IMAGE, volume, MOUNT, [f"{PORT}:8181"], {}, CMD)
    _wait()
    return Conn(durability)


def restart(volume, durability="D2"):
    common.run(NAME, IMAGE, volume, MOUNT, [f"{PORT}:8181"], {}, CMD)
    _wait()
    return Conn(durability)


def _wait():
    for _ in range(240):
        try:
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
            c.request("GET", "/health")
            if c.getresponse().status == 200:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError("InfluxDB did not start")


def connect(durability="D1"):
    return Conn(durability)


def server_settings(conn):
    status, body = conn.request("GET", "/ping")
    return {"ping": body.decode(errors="replace")[:200], "durability": conn.durability,
            "no_sync": conn.durability == "D2", "command": " ".join(CMD)}


def create(conn):
    pass   # databases and tables are created by the first write


def lines(acq, data):
    """Per group: line-protocol request bodies (bytes) of at most ROWS_PER_REQUEST rows. Built in the timed call."""
    out = {}
    tag = acq["acquisition"].replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
    for g, spec in wl.GROUPS.items():
        chans = spec["channels"]
        n = len(data[chans[0]])
        ts = pc.cast(pa.array(acq["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]), pa.string())
        fields = [pc.binary_join_element_wise(f"{ch}=", pc.cast(pa.array(np.asarray(data[ch], dtype=np.float64)),
                                                                pa.string()), "") for ch in chans]
        body = fields[0] if len(fields) == 1 else pc.binary_join_element_wise(*fields, ",")
        row = pc.binary_join_element_wise(f"{g},acquisition={tag} ", body, " ", ts, "")
        out[g] = [("\n".join(row[i:i + ROWS_PER_REQUEST].to_pylist()) + "\n").encode()
                  for i in range(0, n, ROWS_PER_REQUEST)]
    return out


def load_one(conn, acq, data):
    """Ingest one acquisition; returns (conversion seconds, [per-request seconds])."""
    t = time.perf_counter()
    payload = lines(acq, data)
    conv = time.perf_counter() - t
    q = urllib.parse.urlencode({"db": DB, "precision": "nanosecond", "accept_partial": "false",
                                **({"no_sync": "true"} if conn.durability == "D2" else {})})
    acks = []
    for g in wl.GROUPS:
        for body in payload[g]:
            t = time.perf_counter()
            status, resp = conn.request("POST", f"/api/v3/write_lp?{q}", body, {"Content-Type": "text/plain"})
            if status not in (200, 204):
                raise RuntimeError(f"write {status}: {resp[:300]!r}")
            acks.append(time.perf_counter() - t)
    return conv, acks


def count(conn):
    out = {}
    for g in wl.GROUPS:
        try:
            out[g] = int(json.loads(conn.sql(f"SELECT count(*) AS n FROM {g}"))[0]["n"])
        except RuntimeError:
            out[g] = 0   # the table does not exist yet
    return out


def _window(acq, offset, length, group):
    period = wl.GROUPS[group]["period_ns"]
    t0 = acq["start_ns"] + offset * period
    t1 = acq["start_ns"] + (offset + length) * period
    tag = acq["acquisition"].replace("'", "''")
    return (f"acquisition = '{tag}' AND time >= arrow_cast({t0}, 'Timestamp(Nanosecond, None)') "
            f"AND time < arrow_cast({t1}, 'Timestamp(Nanosecond, None)')")


def read(conn, acq, channels, offset, length, group="high"):
    t = pq.read_table(io.BytesIO(conn.sql(f"SELECT time, {', '.join(channels)} FROM {group} WHERE "
                                          f"{_window(acq, offset, length, group)} ORDER BY time", "parquet")))
    if t.num_rows == 0:
        return np.empty(0, dtype=np.int64), {c: np.empty(0) for c in channels}
    times = t.column("time").cast(pa.timestamp("ns")).cast(pa.int64()).to_numpy()
    return times, {c: t.column(c).to_numpy().astype(np.float64, copy=False) for c in channels}


def analytics(conn, acq, offset, length):
    r = json.loads(conn.sql(f"SELECT min(vibration_1) AS a, max(vibration_1) AS b, avg(vibration_1) AS c, "
                            f"sqrt(avg(vibration_1 * vibration_1)) AS d, count(*) AS n FROM high WHERE "
                            f"{_window(acq, offset, length, 'high')}"))[0]
    return [float(r["a"]), float(r["b"]), float(r["c"]), float(r["d"]), float(r["n"])]


def vacuum(conn, volume_name=None, timeout_s=1800, poll_s=30):
    """Bring InfluxDB to its persisted state before storage is measured. It writes Parquet only at a snapshot, which
    its WAL triggers after 600 WAL files (about 10 minutes of writes); an idle server never snapshots (B3.18 smoke:
    none after 75 minutes idle). So, as in continuous operation, one point per second goes to a separate table
    (`heartbeat`, a few hundred rows) until Parquet bytes exist and are unchanged over two polls, at most timeout_s.
    Returns the seconds waited, whether it settled, and the heartbeat rows written."""
    if volume_name is None:
        return None
    hb, t0, last, stable, i, next_poll = Conn("D1"), time.time(), None, 0, 0, 0.0
    while time.time() - t0 < timeout_s:
        hb.request("POST", f"/api/v3/write_lp?db={DB}&precision=nanosecond",
                   f"heartbeat v={i} {time.time_ns()}\n".encode(), {"Content-Type": "text/plain"})
        i += 1
        if time.time() - t0 >= next_poll:
            next_poll += poll_s
            v = volume(volume_name)
            if v["parquet_apparent"] > 0 and v["parquet_apparent"] == last:
                stable += 1
                if stable >= 2:
                    hb.close()
                    return {"waited_s": round(time.time() - t0), "settled": True, "heartbeat_rows": i}
            else:
                stable = 0
            last = v["parquet_apparent"]
        time.sleep(1)
    hb.close()
    return {"waited_s": round(time.time() - t0), "settled": False, "heartbeat_rows": i}


def storage(conn):
    return {}


def volume(volume_name):
    files = common.volume_files(volume_name, "debian:trixie-slim")
    tot = [sum(a for a, _, _ in files), sum(b for _, b, _ in files)]
    part = lambda key: [sum(a for a, _, p in files if f"/{key}/" in f"/{p}"), sum(b for _, b, p in files if f"/{key}/" in f"/{p}")]
    pqf = [sum(a for a, _, p in files if p.endswith(".parquet")), sum(b for _, b, p in files if p.endswith(".parquet"))]
    return {"apparent": tot[0], "allocated": tot[1], "wal_apparent": part("wal")[0], "wal_allocated": part("wal")[1],
            "parquet_apparent": pqf[0], "parquet_allocated": pqf[1], "files": len(files)}
