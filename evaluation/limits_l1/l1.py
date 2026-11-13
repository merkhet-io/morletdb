#!/usr/bin/env python3
"""L1-beta: the operating envelope of MorletDB (PROTOCOL.md, stage L1-beta).

One measurement point per call, each in a fresh container of the release image, synthetic data only:

  python -I evaluation/limits_l1/l1.py POINT REP [--image IMAGE] [--out DIR] [--dev]

POINT is one of the names `points()` lists (`python l1.py list`), for example d1-rate-44100, d2-len-1e8,
d4-kind-adc, d5-chunk-64, d5-writers-4, d5-pattern, d7-mem-512m, d7-cpu-1, d7-disk-full. --dev uses the
development sizes (a tenth of the samples) for building and checking the harness; their results are never
reported. Every point checks correctness first (every stored sample read back bit for bit against the
generator by SHA-256, derived times against an exact reference, refused writes refused with an error) and
then measures; a failed check is recorded as a finding, not retried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import secrets
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
IMAGE = "ghcr.io/merkhet-io/morletdb:0.3.9-alpha.1"
NAME, PORT, COLL = "l1-morletdb", 54360, "l1"
PASSWORD = secrets.token_hex(16)                       # per run, never written anywhere
DSN = f"host=127.0.0.1 port={PORT} user=morlet dbname=morlet password={PASSWORD}"
BASE = {"cpus": "8", "memory": "16g", "rate": 64_000.0, "samples": 3_840_000, "chunk": 4096, "kind": "adc",
        "writers": 1}
BATCH_CHUNKS = 64                                      # chunks per append call and per durable commit
READ_PIECE = 32_768
QUANTUM = 100 / 32768                                  # an ADC step, as in B3's vibration channel
START_NS = 1_767_225_600_000_000_000                   # 2026-01-01T00:00:00Z
STARTED = [None]                                       # the server's start time after setup


# ---- the points -----------------------------------------------------------------------------------------------
def points():
    p = {}
    for r in ("1/60", "1/3", "1", "1000", "44100", "64000", "1000000"):
        p[f"d1-rate-{r.replace('/', 'over')}"] = {"rate": float(Fraction(r)), "rate_exact": r, "samples": 1_048_576}
    for n in ("1e4", "1e6", "1e8", "1e9"):
        p[f"d2-len-{n}"] = {"samples": int(float(n))}
    for k in ("int-counts", "decimals", "adc", "random-floats", "constant", "denormals", "extremes", "nan-inf"):
        p[f"d4-kind-{k}"] = {"kind": k, "samples": 1_048_576}
    for c in (1, 64, 4096, 65536):
        p[f"d5-chunk-{c}"] = {"chunk": c, "samples": 65_536 if c == 1 else 1_048_576}
    for w in (1, 4, 16):
        p[f"d5-writers-{w}"] = {"writers": w, "samples": 1_048_576}
    p["d5-pattern"] = {"pattern": True, "samples": 262_144}
    for m in ("2g", "512m", "256m"):
        p[f"d7-mem-{m}"] = {"memory": m, "samples": 100_000_000}
    p["d7-cpu-1"] = {"cpus": "1", "samples": 100_000_000}
    p["d7-disk-full"] = {"disk": "400m", "samples": 1_000_000_000}
    return p


# ---- data --------------------------------------------------------------------------------------------------------
def generate(kind, start, n, seed=1):
    """Deterministic samples [start, start+n) of a recording: any range can be regenerated."""
    i = np.arange(start, start + n, dtype=np.float64)
    if kind == "adc":                                   # bursts plus noise on an ADC grid
        burst = np.where((i % 64000) < 6400, 3000.0, 300.0)
        counts = np.round(burst * np.sin(i * 0.031) + _noise(seed, start, n) * 40.0)
        return counts * QUANTUM
    if kind == "int-counts":
        return np.round(2000 * np.sin(i * 0.013) + _noise(seed, start, n) * 50.0)
    if kind == "decimals":
        return np.round(12.5 + 3 * np.sin(i * 0.007) + _noise(seed, start, n) * 0.2, 3)
    if kind == "random-floats":                          # full mantissa entropy
        return _noise(seed, start, n) * 1000.0
    if kind == "constant":
        return np.full(n, 42.125)
    if kind == "denormals":
        return (np.round(_noise(seed, start, n) * 1000) * 5e-324).astype(np.float64)
    if kind == "extremes":                               # the documented range is ±1e9
        return np.where(np.arange(n) % 2 == 0, 1e9, -1e9) * (1 - (np.arange(n) % 7) * 1e-16)
    if kind == "nan-inf":
        x = _noise(seed, start, n)
        x[::1000] = np.nan
        x[1::1000] = np.inf
        return x
    raise ValueError(kind)


def _noise(seed, start, n):
    """Standard normal noise addressed by sample index (independent of how ranges are cut)."""
    out = np.empty(n)
    pos = 0
    while pos < n:
        block = (start + pos) // 65536
        off = (start + pos) % 65536
        take = min(n - pos, 65536 - off)
        out[pos:pos + take] = np.random.default_rng([seed, block]).standard_normal(65536)[off:off + take]
        pos += take
    return out


# ---- the container ---------------------------------------------------------------------------------------------
def sh(args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def start(image, cpus, memory, disk=None):
    sh(["docker", "rm", "-f", NAME], check=False)
    sh(["docker", "volume", "rm", f"{NAME}-data"], check=False)
    store = (["--mount", f"type=tmpfs,destination=/var/lib/postgresql/data,tmpfs-size={_bytes(disk)}"] if disk
             else ["-v", f"{NAME}-data:/var/lib/postgresql/data"])
    sh(["docker", "run", "-d", "--name", NAME, "--cpus", cpus, "--memory", memory, "--memory-swap", memory,
        *store, "-p", f"127.0.0.1:{PORT}:5432", "-e", "POSTGRES_USER=morlet", "-e", f"POSTGRES_PASSWORD={PASSWORD}",
        "-e", "POSTGRES_DB=morlet", "-e", "POSTGRES_INITDB_ARGS=--data-checksums", image,
        "postgres", "-c", "max_wal_size=1GB"])
    for _ in range(240):
        try:
            with psycopg.connect(DSN, connect_timeout=2) as c:
                c.execute("SELECT 1")
            break
        except psycopg.Error:
            time.sleep(0.5)
    else:
        raise RuntimeError("server did not start")
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        c.execute(f"SELECT morlet_create_stream_collection('public','{COLL}')")
        c.execute(f"SELECT morlet_enable_chunk_summaries('{COLL}')")
        STARTED[0] = c.execute("SELECT pg_postmaster_start_time()").fetchone()[0]
        return c.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]


def _bytes(s):
    return int(float(s[:-1]) * {"k": 2**10, "m": 2**20, "g": 2**30}[s[-1].lower()])


class Memory:
    """Container memory: docker stats once a second, and the cgroup's own peak at the end."""

    def __init__(self):
        self.peak_mib, self._stop = 0.0, threading.Event()

    def _run(self):
        while not self._stop.wait(1.0):
            out = sh(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", NAME], check=False)
            try:
                v, unit = out.split("/")[0].strip()[:-3], out.split("/")[0].strip()[-3:]
                self.peak_mib = max(self.peak_mib, float(v) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[unit])
            except (ValueError, KeyError, IndexError):
                pass

    def __enter__(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()

    def summary(self):
        peak = sh(["docker", "exec", NAME, "cat", "/sys/fs/cgroup/memory.peak"], check=False)
        events = sh(["docker", "exec", NAME, "cat", "/sys/fs/cgroup/memory.events"], check=False)
        oom = {k: int(v) for k, v in (line.split() for line in events.splitlines() if line)} if events else {}
        state = sh(["docker", "inspect", NAME, "--format", "{{.State.OOMKilled}} {{.State.Status}}"], check=False)
        return {"stats_peak_mib": round(self.peak_mib, 1),
                "cgroup_peak_mib": round(int(peak) / 2**20, 1) if peak.isdigit() else None,
                "oom_events": oom.get("oom_kill", None), "container": state}


# ---- operations ------------------------------------------------------------------------------------------------
def create(conn, rid, rate, kind):
    q = QUANTUM if kind == "adc" else 1.0
    conn.execute(f"SELECT morlet_start_recording('{COLL}',%s,%s,'x','V','{{\"start_ns\": {START_NS}}}',"
                 "512,256,'zscore','none',%s)", (rid, rate, q))


def ingest(conn, rid, kind, n, chunk, lat=None):
    """Durable batches of BATCH_CHUNKS chunks through morlet_append_chunks_raw; the hash of what was sent."""
    h = hashlib.sha256()
    per = chunk * BATCH_CHUNKS if chunk <= 32768 else chunk
    per = min(per, 262_144) if chunk <= 4096 else per
    pos = 0
    while pos < n:
        m = min(per, n - pos)
        x = generate(kind, pos, m)
        h.update(x.astype("<f8").tobytes())
        t = time.perf_counter()
        with conn.transaction():
            conn.execute(f"SELECT morlet_append_chunks_raw('{COLL}',%s,%s,%b,%s)",
                         (rid, pos, x.astype("<f8").tobytes(), chunk))
        if lat is not None:
            lat.append((pos, time.perf_counter() - t))
        pos += m
    return h.hexdigest()


def read_hash(conn, rid, n):
    h = hashlib.sha256()
    for a in range(0, n, READ_PIECE * 8):
        b = min(n, a + READ_PIECE * 8)
        h.update(bytes(conn.execute(f"SELECT morlet_read_interval_raw('{COLL}',%s,%s,%s)", (rid, a, b),
                                    binary=True).fetchone()[0]))
    return h.hexdigest()


def stored_end(conn, rid):
    return int(conn.execute(f"SELECT (checkpoint->>'end')::bigint FROM {COLL} WHERE recording_id=%s",
                            (rid,)).fetchone()[0])


def timed(f, reps=20):
    t = []
    for _ in range(reps):
        a = time.perf_counter()
        f()
        t.append(1e3 * (time.perf_counter() - a))
    t.sort()
    return {"p50_ms": round(t[len(t) // 2], 3), "p95_ms": round(t[int(0.95 * (len(t) - 1))], 3)}


def queries(conn, rid, n):
    out = {}
    for where, a in (("start", 0), ("middle", n // 2), ("end", max(0, n - 4096))):
        b = min(n, a + 4096)
        out[f"read4096_{where}"] = timed(lambda a=a, b=b: conn.execute(
            f"SELECT morlet_read_interval_raw('{COLL}',%s,%s,%s)", (rid, a, b), binary=True).fetchone())
        s = min(n, a + 1_000_000)
        out[f"stats1e6_{where}"] = timed(lambda a=a, s=s: conn.execute(
            f"SELECT * FROM morlet_interval_stats('{COLL}',%s,%s,%s)", (rid, a, s)).fetchone())
    out["overview_whole"] = timed(lambda: conn.execute(
        f"SELECT count(*) FROM morlet_interval_overview('{COLL}',%s,0,%s,100)", (rid, n)).fetchone(), reps=5)
    return out


def sizes(conn):
    r = conn.execute(f"SELECT pg_total_relation_size('{COLL}_chunks'), pg_relation_size('{COLL}_chunks'), "
                     f"coalesce(pg_total_relation_size((SELECT reltoastrelid FROM pg_class WHERE oid='{COLL}_chunks'::regclass)),0), "
                     f"(SELECT max(pg_column_size(checkpoint)) FROM {COLL}), pg_database_size(current_database())").fetchone()
    return dict(zip(("chunks_total_bytes", "chunks_heap_bytes", "chunks_toast_bytes", "checkpoint_bytes",
                     "database_bytes"), r))


def time_check(conn, rate_exact, n, k=1000):
    """Derived times against an exact reference: the first sample at or after t is ceil((t-start)*rate)."""
    rate = Fraction(rate_exact)
    rng = np.random.default_rng(7)
    bad = []
    for s in [0, n - 1, *rng.integers(0, n, k).tolist()]:
        t = START_NS + -(-Fraction(s * 10**9) // rate)      # ceil of sample s's time in ns
        got = conn.execute("SELECT * FROM morlet_time_to_samples(%s,%s,%s,%s)",
                           (float(rate), START_NS, int(t), int(t) + 1)).fetchone()[0]
        want = -(-((t - START_NS) * rate) // 10**9)          # ceil((t - start) * rate / 1e9)
        if got != want:
            bad.append({"sample": s, "t_ns": int(t), "got": got, "want": int(want)})
    return {"checked": k + 2, "mismatches": len(bad), "first": bad[:5]}


# ---- the dimensions --------------------------------------------------------------------------------------------
def run_point(name, cfg, dev):
    n = cfg["samples"] // 10 if dev else cfg["samples"]
    kind, chunk, rate = cfg["kind"], cfg["chunk"], cfg["rate"]
    res = {"point": name, "samples": n, "config": {k: cfg[k] for k in ("cpus", "memory", "chunk", "kind", "writers")},
           "problems": [], "refusals": []}
    with psycopg.connect(DSN, autocommit=True) as conn:
        if cfg.get("pattern"):
            return {**res, **pattern(conn, n)}
        if cfg["writers"] > 1:
            return {**res, **writers(cfg["writers"], n, kind, chunk, rate)}
        try:
            create(conn, "r", rate, kind)
        except psycopg.Error as e:
            res["refusals"].append({"step": "start_recording", "error": e.diag.message_primary})
            return res
        lat = []
        t = time.perf_counter()
        try:
            sent = ingest(conn, "r", kind, n, chunk, lat)
        except psycopg.Error as e:
            res["refusals"].append({"step": "append", **after_failed_append("r", e, kind)})
            if any(not r.get("stored_intact", True) for r in res["refusals"]):
                res["problems"].append("stored samples not intact after a failed append")
            return res
        res["ingest_s"] = round(time.perf_counter() - t, 3)
        res["ingest_samples_per_s"] = round(n / res["ingest_s"])
        ls = [x for _, x in lat]
        k = max(1, len(ls) // 10)
        res["commit_ms"] = {"p50": round(1e3 * float(np.median(ls)), 3), "p95": round(1e3 * float(np.percentile(ls, 95)), 3),
                            "first_tenth_p50": round(1e3 * float(np.median(ls[:k])), 3),
                            "last_tenth_p50": round(1e3 * float(np.median(ls[-k:])), 3)}
        if stored_end(conn, "r") != n:
            res["problems"].append(f"stored end {stored_end(conn, 'r')} != {n}")
        t = time.perf_counter()
        if read_hash(conn, "r", n) != sent:
            res["problems"].append("read-back SHA-256 differs from the generated samples")
        res["read_all_s"] = round(time.perf_counter() - t, 3)
        res["read_samples_per_s"] = round(n / res["read_all_s"])
        res["queries"] = queries(conn, "r", n)
        res["sizes"] = sizes(conn)
        res["bits_per_sample"] = round(8 * res["sizes"]["chunks_total_bytes"] / n, 3)
        if "rate_exact" in cfg:
            res["times"] = time_check(conn, cfg["rate_exact"], n)
            if res["times"]["mismatches"]:
                res["problems"].append(f"{res['times']['mismatches']} derived times differ")
    return res


def writers(w, n, kind, chunk, rate):
    """w concurrent writers, one recording each, n samples each; wall time from a common start."""
    with psycopg.connect(DSN, autocommit=True) as c:
        for i in range(w):
            create(c, f"w{i}", rate, kind)
    out, errs, barrier = {}, [], threading.Barrier(w + 1)

    def run(i):
        try:
            with psycopg.connect(DSN, autocommit=True) as c:
                barrier.wait()
                out[i] = ingest(c, f"w{i}", kind, n, chunk)
        except Exception as e:  # noqa: BLE001 - reported
            errs.append(f"w{i}: {e}")
    ts = [threading.Thread(target=run, args=(i,)) for i in range(w)]
    for t in ts:
        t.start()
    barrier.wait()
    t0 = time.perf_counter()
    for t in ts:
        t.join()
    wall = time.perf_counter() - t0
    problems = list(errs)
    with psycopg.connect(DSN, autocommit=True) as c:
        for i, h in out.items():
            if read_hash(c, f"w{i}", n) != h:
                problems.append(f"w{i}: read-back differs")
    return {"ingest_s": round(wall, 3), "ingest_samples_per_s": round(w * n / wall), "problems": problems}


def pattern(conn, n):
    """Gaps, late, out-of-order and overlapping chunks: each must be refused or stored, never silently merged."""
    create(conn, "p", 1000.0, "adc")
    half = n // 2
    good = generate("adc", 0, n)
    ingest(conn, "p", "adc", half, 4096)
    kept = hashlib.sha256(good[:half].astype("<f8").tobytes()).hexdigest()
    cases = {}

    def attempt(name, start, x):
        try:
            with conn.transaction():
                conn.execute(f"SELECT morlet_append_chunks_raw('{COLL}','p',%s,%b,4096)",
                             (start, x.astype("<f8").tobytes()))
            outcome = "stored"
        except psycopg.Error as e:
            outcome = f"refused: {e.diag.message_primary}"
        cases[name] = {"outcome": outcome, "stored_end": stored_end(conn, "p"),
                       "earlier_samples_unchanged": read_hash(conn, "p", half) == kept}

    attempt("gap: 4096 samples skipped", half + 4096, good[half + 4096:half + 8192])
    attempt("identical replay of stored samples", 0, good[0:8192])
    altered = good[4096:8192].copy()
    altered[10] += QUANTUM
    attempt("late chunk with one altered sample", 4096, altered)
    attempt("overlap: last stored chunk plus new samples", half - 4096, good[half - 4096:half + 4096])
    attempt("out of order: a later range before the next", cases["overlap: last stored chunk plus new samples"]
            ["stored_end"] + 4096, good[half + 8192:half + 12288])
    problems = [k for k, v in cases.items() if not v["earlier_samples_unchanged"]]
    end = stored_end(conn, "p")
    if read_hash(conn, "p", end) != hashlib.sha256(good[:end].astype("<f8").tobytes()).hexdigest():
        problems.append("stored samples differ from the generator after the attempts")
    return {"cases": cases, "problems": problems}


def reconnect(timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        try:
            c = psycopg.connect(DSN, autocommit=True, connect_timeout=3)
            c.execute("SELECT 1")
            return c
        except psycopg.Error:
            time.sleep(1)
    raise RuntimeError("server did not come back")


def after_failed_append(rid, err, kind):
    """After an append failed (for example on a full disk): the error was visible to the client; the server
    answers again (perhaps after restarting); every stored sample is intact and readable."""
    out = {"error_visible": True, "error": (err.diag.message_primary if getattr(err, "diag", None)
                                             and err.diag.message_primary else str(err))[:300]}
    t = time.time()
    try:
        with reconnect() as c:
            out["server_answers_after_s"] = round(time.time() - t, 1)
            have = stored_end(c, rid)
            out["stored_end"] = have
            want = hashlib.sha256(generate(kind, 0, have).astype("<f8").tobytes()).hexdigest()
            out["stored_intact"] = read_hash(c, rid, have) == want
            out["server_restarted"] = c.execute("SELECT pg_postmaster_start_time()").fetchone()[0] != STARTED[0]
    except Exception as e:  # noqa: BLE001 - reported
        out["after_error"] = f"{type(e).__name__}: {e}"[:300]
    logs = subprocess.run(["docker", "logs", "--tail", "200", NAME], capture_output=True, text=True)
    out["server_log"] = [x for x in (logs.stdout + logs.stderr).splitlines()
                         if any(w in x for w in ("PANIC", "FATAL", "No space", "could not"))][-8:]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("point")
    ap.add_argument("rep", nargs="?", type=int, default=1)
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--dev", action="store_true")
    a = ap.parse_args()
    pts = points()
    if a.point == "list":
        print("\n".join(pts))
        return
    cfg = {**BASE, **pts[a.point]}
    out = Path(a.out) / ("dev" if a.dev else "")
    out.mkdir(parents=True, exist_ok=True)
    rec = {"point": a.point, "rep": a.rep, "image": a.image, "dev": a.dev,
           "host": {"machine": platform.machine(), "load_avg": os.getloadavg()},
           "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        rec["extension"] = start(a.image, cfg["cpus"], cfg["memory"], cfg.get("disk"))
        rec["image_id"] = sh(["docker", "image", "inspect", a.image, "--format", "{{.Id}}"])
        with Memory() as mem:
            rec.update(run_point(a.point, cfg, a.dev))
        rec["memory"] = mem.summary()
    except Exception as e:  # noqa: BLE001 - a crash is a finding
        rec["error"] = f"{type(e).__name__}: {e}"[:500]
        rec["memory"] = Memory().summary() if sh(["docker", "ps", "-q", "-f", f"name={NAME}"], check=False) else None
    finally:
        sh(["docker", "rm", "-f", NAME], check=False)
        sh(["docker", "volume", "rm", f"{NAME}-data"], check=False)
    rec["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = out / f"{a.point}_r{a.rep}.json"
    path.write_text(json.dumps(rec, indent=1, default=str))
    print(json.dumps({k: rec.get(k) for k in ("point", "samples", "ingest_samples_per_s", "bits_per_sample",
                                               "problems", "refusals", "error")}, default=str))


if __name__ == "__main__":
    main()
