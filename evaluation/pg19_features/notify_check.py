#!/usr/bin/env python3
"""Commit notifications (#205) with many idle listeners on other channels (development measurement).

  python evaluation/pg19_features/notify_check.py --servers servers.json --listeners 0,200 --appends 3000 \
      --rounds 3 --out results.json

Up to PostgreSQL 18, a NOTIFY at commit signals every backend of the database that listens on any channel; 19
signals only the listeners of the notified channel. MorletDB sends one notification per committed append when
notifications are enabled, and an edge box has idle LISTEN connections on other channels (dashboards, other
collections, the steward). Per arm: K connections LISTEN on unrelated channels and stay idle; one consumer listens
on the collection's channel and counts what arrives; one writer commits N small appends (1,024 samples, one
transaction each). Measured: commits per second, append latency, and the CPU time of all postgres processes on
this host during the appends (ps; run it where the server's processes are visible and no other postgres runs).
It fails if the consumer does not receive exactly one notification per append.
"""
import argparse
import json
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import psycopg

from same_session import SCHEMA, drop_schema, fresh_schema, sh, wait_ready

NAME = "nt19"
C = f"{SCHEMA}.{NAME}"


def postgres_cpu_s():
    out = subprocess.run(["ps", "-A", "-o", "time=,comm="], capture_output=True, text=True, check=True).stdout
    total = 0.0
    for line in out.splitlines():
        t, _, comm = line.strip().partition(" ")
        if "postgres" not in comm:
            continue
        parts = t.replace("-", ":").split(":")
        secs = 0.0
        for p in parts:
            secs = secs * 60 + float(p)
        total += secs
    return total


def arm(dsn, k, n):
    idle = []
    for i in range(k):
        c = psycopg.connect(dsn, autocommit=True)
        c.execute(f"LISTEN idle_{i}")
        idle.append(c)
    received = [0]
    with psycopg.connect(dsn, autocommit=True) as w:
        w.execute("SELECT set_config('client_min_messages','warning',false)")
        fresh_schema(w)
        w.execute("SELECT morlet_create_stream_collection(%s, %s)", (SCHEMA, NAME))
        ch = w.execute("SELECT morlet_enable_notifications(%s::regclass)", (C,)).fetchone()[0]
        for r in range(8):
            w.execute("SELECT morlet_start_recording(%s::regclass,%s,1000,'x','u','{}'::jsonb,512,256,'zscore','none')",
                      (C, f"r{r}"))
        consumer = psycopg.connect(dsn, autocommit=True)
        consumer.execute(f'LISTEN "{ch}"')
        done = threading.Event()

        def consume():
            while not done.is_set() or received[0] < n:
                for _ in consumer.notifies(timeout=0.5):
                    received[0] += 1
                if done.is_set() and received[0] >= n:
                    break

        th = threading.Thread(target=consume, daemon=True)
        th.start()
        x = (np.arange(1024) % 97).astype("<f8").tobytes()
        lat = []
        offsets = [0] * 8
        cpu0, t0 = postgres_cpu_s(), time.perf_counter()
        for i in range(n):
            r = i % 8
            t = time.perf_counter()
            w.execute("SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%s)", (C, f"r{r}", offsets[r], x))
            lat.append(time.perf_counter() - t)
            offsets[r] += 1024
        elapsed = time.perf_counter() - t0
        cpu = postgres_cpu_s() - cpu0
        deadline = time.time() + 30
        while received[0] < n and time.time() < deadline:
            time.sleep(0.1)
        done.set()
        th.join(timeout=5)
        consumer.close()
        drop_schema(w)
    for c in idle:
        c.close()
    if received[0] != n:
        raise SystemExit(f"listeners={k}: {received[0]} notifications for {n} appends")
    lat = np.array(lat)
    return {"listeners": k, "appends": n, "elapsed_s": elapsed, "commits_per_s": n / elapsed,
            "latency_ms": {"p50": float(np.percentile(lat, 50) * 1e3), "p99": float(np.percentile(lat, 99) * 1e3),
                           "max": float(lat.max() * 1e3)},
            "postgres_cpu_s": cpu, "notifications": received[0]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--servers", required=True)
    ap.add_argument("--listeners", default="0,200")
    ap.add_argument("--appends", type=int, default=3000)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    servers = json.loads(Path(a.servers).read_text())
    ks = [int(k) for k in a.listeners.split(",")]
    out = {"host": platform.platform(), "cpus": os.cpu_count(), "runs": []}
    for rnd in range(a.rounds):
        for srv in (servers if rnd % 2 == 0 else servers[::-1]):
            sh(srv["start"])
            try:
                wait_ready(srv["dsn"])
                with psycopg.connect(srv["dsn"], autocommit=True) as c:
                    c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
                    version = c.execute("SHOW server_version").fetchone()[0]
                for k in (ks if rnd % 2 == 0 else ks[::-1]):
                    r = {"round": rnd + 1, "server": srv["name"], "version": version, "load_avg": os.getloadavg(),
                         **arm(srv["dsn"], k, a.appends)}
                    out["runs"].append(r)
                    print(json.dumps({x: r[x] for x in ("round", "server", "listeners", "commits_per_s",
                                                         "postgres_cpu_s")}), flush=True)
                    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
            finally:
                sh(srv["stop"])


if __name__ == "__main__":
    main()
