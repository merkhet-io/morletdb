"""B3.21 GreptimeDB adapter (PROTOCOL.md, "Amendment B3.21"): GreptimeDB 1.3.0 standalone, its HTTP API.

One table per group, created before the load: `ts TIMESTAMP(9) TIME INDEX`, `acquisition STRING PRIMARY KEY` (the
tag), the group's channels as DOUBLE. Writes: InfluxDB line protocol over one persistent HTTP connection
(`/v1/influxdb/api/v2/write`, precision ns), requests of at most ROWS_PER_REQUEST rows; floats as their shortest
round-trip decimal (Arrow's cast; exact). D1: `wal.sync_write = true`; D2: the default (false). Reads: SQL over
`/v1/sql` with format=arrow (exact float64, checked bit for bit with -0.0, the largest double and a subnormal).
Telemetry is switched off (`enable_telemetry = false`); default settings otherwise; no authentication (local only).
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

IMAGE = "greptime/greptimedb@sha256:d8a199398da2b105e6cb3a168f249ecbbc078893abff5ea668bd59e05f1027df"   # 1.3.0, arm64
NAME = "b3-greptimedb"
PORT = 54000
MOUNT = "/greptimedb_data"
DB = "public"
CMD = ["standalone", "start", "--http-addr", "0.0.0.0:4000", "--data-home", MOUNT]
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
        status, body = self.request("POST", "/v1/sql?" + urllib.parse.urlencode({"db": DB, **({"format": fmt} if fmt != "json" else {})}),
                                    urllib.parse.urlencode({"sql": q}).encode(),
                                    {"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            raise RuntimeError(f"query {status}: {body[:300]!r}")
        if fmt == "json":
            out = json.loads(body)
            if "error" in out:
                raise RuntimeError(f"query: {out['error'][:300]}")
            o = out["output"][0]
            if "records" not in o:
                return o
            names = [c["name"] for c in o["records"]["schema"]["column_schemas"]]
            return [dict(zip(names, row)) for row in o["records"]["rows"]]
        return body

    def close(self):
        self.http.close()


def env(durability):
    return {"GREPTIMEDB_STANDALONE__ENABLE_TELEMETRY": "false",
            "GREPTIMEDB_STANDALONE__WAL__SYNC_WRITE": "true" if durability == "D1" else "false"}


def start(volume, durability="D2"):
    common.fresh(NAME, IMAGE, volume, MOUNT, [f"{PORT}:4000"], env(durability), CMD)
    _wait()
    return Conn(durability)


def restart(volume, durability="D2"):
    common.run(NAME, IMAGE, volume, MOUNT, [f"{PORT}:4000"], env(durability), CMD)
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
    raise RuntimeError("GreptimeDB did not start")


def connect(durability="D1"):
    return Conn(durability)


def server_settings(conn):
    status, body = conn.request("GET", "/config")
    cfg = body.decode(errors="replace")
    pick = [ln.strip() for ln in cfg.splitlines() if ln.strip().split(" =")[0] in ("sync_write", "enable_telemetry")]
    return {"version": conn.sql("SELECT version() AS v")[0]["v"], "durability": conn.durability, "config": pick,
            "command": " ".join(CMD)}


def create(conn):
    for g, spec in wl.GROUPS.items():
        cols = ", ".join(f'"{ch}" DOUBLE' for ch in spec["channels"])   # quoted: force is a keyword
        conn.sql(f"CREATE TABLE IF NOT EXISTS {g} (ts TIMESTAMP(9) TIME INDEX, acquisition STRING PRIMARY KEY, {cols})")


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
    q = urllib.parse.urlencode({"db": DB, "precision": "ns"})
    acks = []
    for g in wl.GROUPS:
        for body in payload[g]:
            t = time.perf_counter()
            status, resp = conn.request("POST", f"/v1/influxdb/api/v2/write?{q}", body, {"Content-Type": "text/plain"})
            if status not in (200, 204):
                raise RuntimeError(f"write {status}: {resp[:300]!r}")
            acks.append(time.perf_counter() - t)
    return conv, acks


def count(conn):
    out = {}
    for g in wl.GROUPS:
        try:
            out[g] = int(conn.sql(f"SELECT count(*) AS n FROM {g}")[0]["n"])
        except RuntimeError:
            out[g] = 0   # the table does not exist yet
    return out


def _window(acq, offset, length, group):
    period = wl.GROUPS[group]["period_ns"]
    t0 = acq["start_ns"] + offset * period
    t1 = acq["start_ns"] + (offset + length) * period
    tag = acq["acquisition"].replace("'", "''")
    return (f"acquisition = '{tag}' AND ts >= arrow_cast({t0}, 'Timestamp(Nanosecond, None)') "
            f"AND ts < arrow_cast({t1}, 'Timestamp(Nanosecond, None)')")


def read(conn, acq, channels, offset, length, group="high"):
    t = pa.ipc.open_stream(io.BytesIO(conn.sql(f"SELECT ts, {', '.join(f'"{c}"' for c in channels)} FROM {group} WHERE "
                                               f"{_window(acq, offset, length, group)} ORDER BY ts", "arrow"))).read_all()
    if t.num_rows == 0:
        return np.empty(0, dtype=np.int64), {c: np.empty(0) for c in channels}
    times = t.column("ts").cast(pa.timestamp("ns")).cast(pa.int64()).to_numpy()
    return times, {c: t.column(c).to_numpy().astype(np.float64, copy=False) for c in channels}


def analytics(conn, acq, offset, length):
    r = conn.sql(f"SELECT min(vibration_1) AS a, max(vibration_1) AS b, avg(vibration_1) AS c, "
                 f"sqrt(avg(vibration_1 * vibration_1)) AS d, count(*) AS n FROM high WHERE "
                 f"{_window(acq, offset, length, 'high')}")[0]
    return [float(r["a"]), float(r["b"]), float(r["c"]), float(r["d"]), float(r["n"])]


def vacuum(conn, volume_name=None):
    """GreptimeDB's settled state before storage is measured: every table flushed from memory to Parquet
    (`ADMIN flush_table`), then compacted (`ADMIN compact_table`), timed; then the Parquet bytes are polled until
    unchanged over two polls 10 s apart (compaction runs in the background), at most 30 minutes."""
    if volume_name is None:
        return None
    c, t0 = Conn("D1"), time.time()
    for g in wl.GROUPS:
        c.sql(f"ADMIN flush_table('{g}')")
    flushed = time.time() - t0
    for g in wl.GROUPS:
        c.sql(f"ADMIN compact_table('{g}')")
    last, stable = None, 0
    while time.time() - t0 < 1800:
        v = volume(volume_name)
        if v["parquet_apparent"] > 0 and v["parquet_apparent"] == last:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        last = v["parquet_apparent"]
        time.sleep(10)
    c.close()
    return {"flush_s": round(flushed, 2), "waited_s": round(time.time() - t0), "settled": stable >= 2}


def storage(conn):
    return {}


def volume(volume_name):
    files = common.volume_files(volume_name, "debian:trixie-slim")
    tot = [sum(a for a, _, _ in files), sum(b for _, b, _ in files)]
    part = lambda key: [sum(a for a, _, p in files if f"/{key}/" in f"/{p}"), sum(b for _, b, p in files if f"/{key}/" in f"/{p}")]
    pqf = [sum(a for a, _, p in files if p.endswith(".parquet")), sum(b for _, b, p in files if p.endswith(".parquet"))]
    return {"apparent": tot[0], "allocated": tot[1], "wal_apparent": part("wal")[0], "wal_allocated": part("wal")[1],
            "parquet_apparent": pqf[0], "parquet_allocated": pqf[1], "files": len(files)}
