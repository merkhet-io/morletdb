#!/usr/bin/env python3
"""X1: does MorletDB run inside edge-sized resource limits? (PROTOCOL.md)

One configuration per call, on a fresh volume, with B3's waveforms (evaluation/tsdb_b3 manifest and data):

  .venv-b3/bin/python -I evaluation/edge_x1/x1.py CONFIG IMAGE REP     # CONFIG in ref, e1g, e512

Ingests every acquisition with search features and chunk summaries (one connection, durable commits), builds
envelopes, reads every channel back bit for bit, then times exact reads, interval statistics, an overview and
similarity searches. Container memory is sampled every second (docker stats) and its cgroup peak read at the
end of every phase; an out-of-memory kill or any failed statement fails the run.
"""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation/tsdb_b3"))
sys.path.insert(0, str(ROOT / "tools/ingest"))
import common  # noqa: E402  (B3: manifest, read plan, percentiles)
import workload as wl  # noqa: E402
from parquet_import import Config, import_files  # noqa: E402
from morlet_import import build  # noqa: E402
import pg_numpy  # noqa: E402

HERE = Path(__file__).resolve().parent
NAME, PORT, COLLECTION = "x1-morletdb", 54350, "x1"
PASSWORD = os.environ.setdefault("X1_PG_PASSWORD", secrets.token_hex(16))   # per run, never written anywhere
DSN = f"host=127.0.0.1 port={PORT} user=morlet dbname=morlet password={PASSWORD}"
CONFIGS = {
    # B3's container limits, PostgreSQL defaults of the image
    "ref": {"cpus": "8", "memory": "16g", "settings": {}},
    # a 2-core, 1 GB device; image defaults (shared_buffers 128 MB)
    "e1g": {"cpus": "2", "memory": "1g", "settings": {}},
    # a 2-core, 512 MB device; smaller buffers and fewer connections
    "e512": {"cpus": "2", "memory": "512m",
             "settings": {"shared_buffers": "64MB", "max_connections": "20", "maintenance_work_mem": "32MB"}},
}
SEARCHES, READ_REPS = 20, 20
# X2 (evaluation/edge_x2): X1_ARM=raw stores samples only (no features, summaries, envelopes or search);
# X1_STUDY names the result files and directory; vcgencmd, when present, adds the device's temperature,
# ARM clock and throttle flags to every phase.
ARM = os.environ.get("X1_ARM", "searchable")
STUDY = os.environ.get("X1_STUDY", "x1")
VCGENCMD = shutil.which("vcgencmd")


def sh(args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def start(cfg, image, volume):
    sh(["docker", "rm", "-f", NAME], check=False)
    sh(["docker", "volume", "rm", volume], check=False)
    c = CONFIGS[cfg]
    cmd = ["postgres"] + [x for k, v in c["settings"].items() for x in ("-c", f"{k}={v}")]
    sh(["docker", "run", "-d", "--name", NAME, "--cpus", c["cpus"], "--memory", c["memory"],
        "--memory-swap", c["memory"], "-v", f"{volume}:/var/lib/postgresql/data", "-p", f"127.0.0.1:{PORT}:5432",
        "-e", "POSTGRES_USER=morlet", "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", "POSTGRES_DB=morlet", image] + cmd)
    for _ in range(240):
        try:
            psycopg.connect(DSN, connect_timeout=2).execute("SELECT 1")
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("server did not start")
    time.sleep(3)          # the entrypoint restarts the server once after init
    for _ in range(60):
        try:
            conn = psycopg.connect(DSN, autocommit=True)
            pg_numpy.register(conn)
            return conn
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("cannot connect")


def container_state():
    st = json.loads(sh(["docker", "inspect", NAME, "--format", "{{json .State}}"]))
    out = {"oom_killed": st["OOMKilled"], "running": st["Running"], "restarts": st.get("RestartCount", 0)}
    if not st["Running"]:
        return out
    cg = sh(["docker", "exec", NAME, "sh", "-c", "cd /sys/fs/cgroup && cat memory.peak && echo == && cat memory.events "
             "&& echo == && grep -E '^(anon|file) ' memory.stat"], check=False).split("==")
    if len(cg) == 3:
        # memory.peak counts page cache too, which the kernel reclaims under the limit; anon is process memory.
        out["cgroup_memory_peak_mib"] = round(int(cg[0]) / 2**20, 1)
        out["memory_events"] = {k: int(v) for k, v in (l.split() for l in cg[1].strip().splitlines())}
        stat = {k: int(v) for k, v in (l.split() for l in cg[2].strip().splitlines())}
        out["anon_mib"], out["file_cache_mib"] = round(stat["anon"] / 2**20, 1), round(stat["file"] / 2**20, 1)
    return out


class Memory:
    """docker stats every second while a phase runs: memory (MiB) and CPU percent."""

    def __init__(self):
        self.samples, self.device, self._stop = [], [], threading.Event()
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}};{{.MemUsage}}", NAME],
                                 capture_output=True, text=True).stdout.strip()
            if out:
                cpu, mem = out.split(";")
                used = mem.split("/")[0].strip()
                scale = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(used[-3:], 1)
                self.samples.append((float(cpu.rstrip("%")), float(used[:-3]) * scale))
            if VCGENCMD:
                self.device.append(device_state())
            self._stop.wait(1.0)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self.t.join(timeout=5)

    def summary(self):
        if not self.samples:
            return {}
        dev = {}
        if self.device:
            dev = {"device": {"temp_c_max": max(d["temp_c"] for d in self.device),
                              "arm_mhz_min": min(d["arm_mhz"] for d in self.device),
                              "arm_mhz_median": float(np.median([d["arm_mhz"] for d in self.device])),
                              "throttled_or": hex(int(np.bitwise_or.reduce([d["throttled"] for d in self.device]))),
                              "samples": len(self.device)}}
        return {**dev, "memory_peak_mib": round(max(m for _, m in self.samples), 1),
                "memory_median_mib": round(float(np.median([m for _, m in self.samples])), 1),
                "cpu_percent_mean": round(float(np.mean([c for c, _ in self.samples])), 1)}


def device_state():
    """Raspberry Pi firmware readings: SoC temperature, ARM clock, throttle flags (bits per vcgencmd docs)."""
    t = sh([VCGENCMD, "measure_temp"]).split("=")[1].rstrip("'C")
    f = sh([VCGENCMD, "measure_clock", "arm"]).split("=")[1]
    th = sh([VCGENCMD, "get_throttled"]).split("=")[1]
    return {"temp_c": float(t), "arm_mhz": round(int(f) / 1e6), "throttled": int(th, 16)}


def host_info():
    """Machine, OS, Docker and client library versions (recorded with every result)."""
    import platform
    import pyarrow
    model = Path("/proc/device-tree/model")
    return {"machine": platform.machine(), "system": platform.platform(), "python": platform.python_version(),
            "model": model.read_text().strip("\x00") if model.exists() else None,
            "docker": sh(["docker", "version", "--format", "{{.Server.Version}}"], check=False),
            "numpy": np.__version__, "psycopg": psycopg.__version__, "pyarrow": pyarrow.__version__}


def extractor(rate):
    if ARM == "raw":
        return "none"
    return "morlet-energy-v1" if rate >= 128 else ("morlet-energy-lowrate-v1" if rate >= 1 else "none")


def rid(a, c):
    return f"{a['acquisition']}.{c}"


def ingest(m):
    """All acquisitions through the importer core (pipelined, raw appends), one connection, durable commits."""
    def reader_for(a, data):
        def read(_path, config, _mapping):
            for c in wl.UNITS:
                g = next(g for g, s in wl.GROUPS.items() if c in s["channels"])
                rate = wl.GROUPS[g]["rate"]
                spec = (rate, c, wl.UNITS[c], {"acquisition": a["acquisition"], "group": g, "start_ns": a["start_ns"]},
                        wl.QUANTUM.get(c, 1.0), extractor(rate))
                x = data[c]
                for s in range(0, x.size, config.chunk_size):
                    yield rid(a, c), spec, s, x[s:s + config.chunk_size]
        return read
    acks, total = [], 0.0
    for a in m["acquisitions"]:
        data = wl.load(ROOT / a["path"])
        rep = import_files([ROOT / a["path"]], COLLECTION, Config(), dsn=DSN, reader=reader_for(a, data))
        dt = rep["elapsed_s"]          # excludes the importer's hashing of its source file
        acks.append(dt)
        total += dt
    return total, acks


def check_all(conn, m):
    """Every channel read back whole (raw bytes, pieces of 1 M samples) against the manifest's SHA-256."""
    bad = 0
    for a in m["acquisitions"]:
        for c, spec in a["channels"].items():
            h, n = hashlib.sha256(), spec["samples"]
            for s in range(0, n, 1 << 20):
                h.update(conn.execute("SELECT morlet_read_interval_raw(%s::regclass, %s, %s, %s)",
                                      (COLLECTION, rid(a, c), s, min(n, s + (1 << 20))), binary=True,
                                      prepare=True).fetchone()[0])
            bad += h.hexdigest() != spec["sha256"]
    return bad


def timed(f, reps):
    f()
    lat = []
    for _ in range(reps):
        t = time.perf_counter()
        f()
        lat.append(time.perf_counter() - t)
    return lat


def queries(conn, m):
    plan = common.read_plan(m)
    acq = {a["k"]: a for a in m["acquisitions"]}
    out = {}
    for name in ("R1", "R2"):
        n, lat = plan[name]["length"], []
        for k, off in plan[name]["pairs"]:
            lat += timed(lambda: np.frombuffer(conn.execute(
                "SELECT morlet_read_interval_raw(%s::regclass, %s, %s, %s)",
                (COLLECTION, rid(acq[k], "vibration_1"), off, off + n), binary=True, prepare=True).fetchone()[0], "<f8"),
                READ_REPS)
        out[name] = common.pcts(lat)
    lat = []
    for k, off in plan["R2"]["pairs"]:
        lat += timed(lambda: conn.execute("SELECT * FROM morlet_interval_stats(%s::regclass, %s, %s, %s)",
                                          (COLLECTION, rid(acq[k], "vibration_1"), off, off + 128000),
                                          prepare=True).fetchone(), READ_REPS)
    out["stats_128k"] = common.pcts(lat)
    lat = []
    for k, _ in plan["R2"]["pairs"]:
        n = acq[k]["channels"]["vibration_1"]["samples"]
        lat += timed(lambda: conn.execute("SELECT count(*) FROM morlet_interval_overview(%s::regclass, %s, 0, %s, 1000)",
                                          (COLLECTION, rid(acq[k], "vibration_1"), n), prepare=True).fetchone(),
                     READ_REPS)
    out["overview_whole_channel"] = common.pcts(lat)
    if ARM == "raw":
        return out
    rng, lists, lat = np.random.default_rng(20261103), [], []
    for _ in range(SEARCHES):
        a = m["acquisitions"][int(rng.integers(0, len(m["acquisitions"])))]
        s = int(rng.integers(0, a["channels"]["vibration_1"]["samples"] // 2048 - 2)) * 2048
        q = ("SELECT recording_id, start_sample, distance FROM morlet_search_similar(%s::regclass, %s, %s, %s, "
             "'{\"group\": \"high\"}', 10)")
        args = (COLLECTION, rid(a, "vibration_1"), s, s + 4096)
        rows = conn.execute(q, args).fetchall()
        lists.append([[r[0], int(r[1]), float(r[2]).hex()] for r in rows])
        lat += timed(lambda: conn.execute(q, args).fetchall(), 2)
    out["search_top10"] = {**common.pcts(lat), "queries": SEARCHES,
                           "lists_sha256": hashlib.sha256(json.dumps(lists).encode()).hexdigest(),
                           "hits": sum(len(x) for x in lists)}
    return out


def main(cfg, image, rep):
    m = common.manifest()
    volume = f"x1-{cfg}-r{rep}"
    res = {"config": cfg, "limits": CONFIGS[cfg], "image": image, "rep": int(rep),
           "image_id": sh(["docker", "image", "inspect", image, "--format", "{{.Id}}"]),
           "image_size_mib": round(int(sh(["docker", "image", "inspect", image, "--format", "{{.Size}}"])) / 2**20, 1),
           "architecture": sh(["docker", "image", "inspect", image, "--format", "{{.Architecture}}"]),
           "samples": sum(c["samples"] for a in m["acquisitions"] for c in a["channels"].values()),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "phases": {}, "errors": [], "study": STUDY, "arm": ARM,
           "host": host_info()}
    conn = start(cfg, image, volume)
    res["server"] = {k: conn.execute(f"SHOW {k}").fetchone()[0]
                     for k in ("server_version", "shared_buffers", "max_connections", "synchronous_commit")}
    try:
        conn.execute("CREATE EXTENSION pg_morlet")
        conn.execute("SELECT morlet_create_stream_collection('public', %s)", (COLLECTION,))
        if ARM != "raw":
            conn.execute("SELECT morlet_enable_chunk_summaries(%s::regclass)", (COLLECTION,))
            conn.execute("SELECT morlet_enable_envelopes(%s::regclass)", (COLLECTION,))
        res["phases"]["idle"] = container_state()
        with Memory() as mem:
            wall, acks = ingest(m)
        res["phases"]["ingest"] = {"wall_s": round(wall, 2), "samples_per_s": round(res["samples"] / wall),
                                   "ack_per_acquisition": common.pcts(acks), **mem.summary(), **container_state()}
        if ARM != "raw":
            with Memory() as mem:
                t = time.perf_counter()
                bconn = psycopg.connect(DSN)
                built = build(bconn, COLLECTION, [rid(a, c) for a in m["acquisitions"] for c in wl.UNITS], 4096, False)
                bconn.close()
            res["phases"]["envelopes"] = {"wall_s": round(time.perf_counter() - t, 2), "windows": sum(built.values()),
                                          **mem.summary(), **container_state()}
        with Memory() as mem:
            bad = check_all(conn, m)
        res["phases"]["read_back"] = {"channels": sum(len(a["channels"]) for a in m["acquisitions"]),
                                      "mismatches": bad, **mem.summary(), **container_state()}
        with Memory() as mem:
            q = queries(conn, m)
        res["phases"]["queries"] = {**q, **mem.summary(), **container_state()}
        res["database_mib"] = round(conn.execute("SELECT pg_database_size(current_database())").fetchone()[0] / 2**20, 1)
    except Exception as exc:
        res["errors"].append(str(exc)[:400])
    res["final"] = container_state()
    res["passed"] = (not res["errors"] and not res["final"]["oom_killed"] and res["final"]["running"]
                     and res["final"].get("memory_events", {}).get("oom_kill", 1) == 0 and res["final"]["restarts"] == 0
                     and res["phases"].get("read_back", {}).get("mismatches") == 0)
    sh(["docker", "rm", "-f", NAME], check=False)
    sh(["docker", "volume", "rm", volume], check=False)
    res["set"] = os.environ.get("B3_SET", "benchmark")
    base = HERE if STUDY == "x1" else HERE.parent / f"edge_{STUDY}"
    name = f"{STUDY}_{cfg}_r{rep}.json" if ARM == "searchable" else f"{STUDY}_{ARM}_{cfg}_r{rep}.json"
    out = base / "results" / ("dev" if res["set"] == "dev" else "") / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: res[k] for k in ("config", "passed", "errors")}, indent=None), flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
