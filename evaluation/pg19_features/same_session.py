#!/usr/bin/env python3
"""PostgreSQL 17 against 19 on MorletDB's own tasks, in one session (development measurement, not a benchmark).

  python evaluation/pg19_features/same_session.py --data DIR --servers servers.json --rounds 3 --out results.json

servers.json lists two servers: [{"name": "pg17", "dsn": "...", "start": "<shell>", "stop": "<shell>"}, ...].
Only one server runs at a time. Each round starts each server in turn (the order alternates: AB, BA, AB, ...),
loads the data (prepare_data.py) into a fresh collection, builds envelopes, runs every task once to warm up and
once timed, and stops the server. Timings are the client's wall-clock seconds around the database calls (reading
the .npy files is excluded; slicing the array into chunk payloads during the load is included). All tables live in
the schema morlet_pg19exp, which the script drops and recreates. Every round also records digests of the stored chunks and envelopes and of the search results:
the script fails if they differ between rounds or servers, so a timing is never reported for different results.

Tasks: load (morlet_start_recording + morlet_append_chunk_raw, one transaction per recording), envelopes
(morlet_build_envelopes 4096/2048), full_read (every sample, 32,768 at a time, in SQL), random_reads (fixed seed,
4,096 samples each), search (exact morlet_search_similar, top 10, fixed queries), latest (morlet_latest),
verify (morlet_verify over every recording). Python 3.9 or later; numpy and psycopg 3.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

SCHEMA = "morlet_pg19exp"   # every script works only in this schema, which it creates and drops
NAME = "ss19"
C = f"{SCHEMA}.{NAME}"
CHUNK = 32768
N_RANDOM = 2000
N_QUERIES = 20


def sh(cmd):
    subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL)


def wait_ready(dsn, timeout=120):
    t = time.time()
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=3) as c:
                c.execute("SELECT 1")
                return
        except psycopg.OperationalError:
            if time.time() - t > timeout:
                raise
            time.sleep(1)


def fresh_schema(c):
    """Drop and recreate the scripts' own schema; nothing outside it is touched."""
    c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    c.execute(f"CREATE SCHEMA {SCHEMA}")


def drop_schema(c):
    c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


def settings(c):
    out = {"version": c.execute("SELECT version()").fetchone()[0],
           "extension": c.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]}
    for s in ("jit", "shared_buffers", "work_mem", "max_wal_size", "default_toast_compression", "io_method",
              "shared_memory_size", "max_locks_per_transaction", "max_connections"):
        r = c.execute("SELECT setting, unit FROM pg_settings WHERE name=%s", (s,)).fetchone()
        out[s] = None if r is None else (r[0] + (r[1] or ""))
    return out


def load(c, data, recs, coll=C):
    t_load = t_env = 0.0
    for r in recs:
        x = np.load(data / r["file"])
        t = time.perf_counter()
        c.execute("SELECT morlet_start_recording(%s::regclass,%s,%s,%s,%s,'{}'::jsonb,512,256,'zscore','none',%s)",
                  (coll, r["recording_id"], r["rate"], r["channel"], r["unit"], r["quantum"]))
        with c.transaction():
            for s in range(0, len(x), CHUNK):
                c.execute("SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%s)",
                          (coll, r["recording_id"], s, x[s:s + CHUNK].tobytes()))
        t_load += time.perf_counter() - t
        if len(x) >= 4096:
            t = time.perf_counter()
            c.execute("SELECT morlet_build_envelopes(%s::regclass,%s,4096,2048)", (coll, r["recording_id"]))
            t_env += time.perf_counter() - t
    return t_load, t_env


def digests(c):
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
    return {
        "chunks": q(f"SELECT md5(string_agg(recording_id||':'||start_sample||':'||md5(encoded),',' "
                    f"ORDER BY recording_id,start_sample)) FROM {C}_chunks"),
        "envelopes": q(f"SELECT md5(string_agg(recording_id||':'||start_sample||':'||md5(envelope),',' "
                       f"ORDER BY recording_id,start_sample)) FROM {C}_envelopes"),
        "chunk_bytes": q(f"SELECT sum(pg_column_size(encoded)) FROM {C}_chunks"),
        "chunks_relation_bytes": q(f"SELECT pg_total_relation_size('{C}_chunks')"),
        "envelopes_relation_bytes": q(f"SELECT pg_total_relation_size('{C}_envelopes')"),
    }


def tasks(c, recs):
    rng = np.random.RandomState(19)
    picks = []
    for i in rng.randint(0, len(recs), N_RANDOM):
        n = recs[i]["samples"]
        if n >= 4096:
            picks.append((recs[i]["recording_id"], int(rng.randint(0, n - 4096 + 1))))
    c.execute("CREATE TEMP TABLE IF NOT EXISTS ss_reads (rid text, s bigint)")
    c.execute("TRUNCATE ss_reads")
    with c.cursor().copy("COPY ss_reads FROM STDIN") as cp:
        for p in picks:
            cp.write_row(p)
    windows = c.execute(f"SELECT recording_id, start_sample FROM {C}_envelopes ORDER BY recording_id, start_sample"
                        ).fetchall()
    queries = [windows[i] for i in sorted(np.random.RandomState(1919).choice(len(windows), N_QUERIES, replace=False))]

    def full_read():
        return c.execute(f"SELECT sum(cardinality(x.samples)) FROM (SELECT recording_id, (checkpoint->>'end')::bigint n "
                         f"FROM {C}) r, generate_series(0, r.n - 1, {CHUNK}) s, "
                         f"LATERAL morlet_read_interval('{C}', r.recording_id, s, least(s + {CHUNK}, r.n)) x").fetchone()[0]

    def random_reads():
        return c.execute(f"SELECT sum(cardinality(x.samples)) FROM ss_reads r, "
                         f"LATERAL morlet_read_interval('{C}', r.rid, r.s, r.s + 4096) x").fetchone()[0]

    def search():
        out, times = [], []
        for rid, s in queries:
            t = time.perf_counter()
            rows = c.execute("SELECT recording_id, start_sample, distance FROM morlet_search_similar(%s::regclass,%s,%s,%s)",
                             (C, rid, s, s + 4096)).fetchall()
            times.append(time.perf_counter() - t)
            out.append([(r[0], r[1], float(r[2]).hex()) for r in rows])
        return out, times

    def latest():
        return c.execute(f"SELECT count(*) FROM morlet_latest('{C}', 1, '{{}}', 100000)").fetchone()[0]

    def verify():
        after, rows, problems = None, 0, 0
        while True:
            page = c.execute("SELECT recording_id, cardinality(problems) FROM morlet_verify(%s::regclass,%s,100)",
                             (C, after)).fetchall()
            rows += len(page)
            problems += sum(1 for p in page if p[1])
            if len(page) < 100:
                return rows, problems
            after = page[-1][0]

    res = {}
    for name, fn in (("full_read", full_read), ("random_reads", random_reads), ("search", search),
                     ("latest", latest), ("verify", verify)):
        fn()                                           # warm-up
        t = time.perf_counter()
        v = fn()
        res[name] = {"s": time.perf_counter() - t}
        if name == "search":
            hits, per_query = v
            res[name]["per_query_s"] = per_query
            res[name]["results_md5"] = hashlib.md5(json.dumps(hits).encode()).hexdigest()
        else:
            res[name]["value"] = v
    res["random_reads"]["reads"] = len(picks)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--servers", required=True)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = Path(a.data)
    manifest = json.loads((data / "manifest.json").read_text())
    recs = manifest["recordings"]
    servers = json.loads(Path(a.servers).read_text())
    out = {"data": {k: manifest[k] for k in ("source", "every", "offset", "samples", "samples_sha256")},
           "recordings": len(recs), "host": platform.platform(), "machine": platform.machine(),
           "cpus": os.cpu_count(), "servers": {}, "rounds": []}
    reference = None
    for rnd in range(a.rounds):
        order = servers if rnd % 2 == 0 else servers[::-1]
        for srv in order:
            sh(srv["start"])
            try:
                wait_ready(srv["dsn"])
                with psycopg.connect(srv["dsn"], autocommit=True) as c:
                    c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
                    out["servers"][srv["name"]] = settings(c)
                    fresh_schema(c)
                    c.execute("CHECKPOINT")
                    c.execute("SELECT morlet_create_stream_collection(%s, %s)", (SCHEMA, NAME))
                    c.execute("SELECT morlet_enable_envelopes(%s::regclass)", (C,))
                    load_avg = os.getloadavg()
                    t_load, t_env = load(c, data, recs)
                    c.execute(f"VACUUM ANALYZE {C}, {C}_chunks, {C}_envelopes")
                    d = digests(c)
                    r = {"round": rnd + 1, "server": srv["name"], "load_avg_before": load_avg,
                         "load_s": t_load, "envelopes_s": t_env, "digests": d, "tasks": tasks(c, recs)}
                    r["load_avg_after"] = os.getloadavg()
                    if r["tasks"]["full_read"]["value"] != manifest["samples"]:
                        raise SystemExit(f"{srv['name']}: full read returned {r['tasks']['full_read']['value']} samples")
                    if r["tasks"]["verify"]["value"][1]:
                        raise SystemExit(f"{srv['name']}: morlet_verify reported problems")
                    key = (d["chunks"], d["envelopes"], d["chunk_bytes"], r["tasks"]["search"]["results_md5"])
                    if reference is None:
                        reference = key
                    elif key != reference:
                        raise SystemExit(f"{srv['name']} round {rnd + 1}: results differ from the first run: {key} != {reference}")
                    drop_schema(c)
            finally:
                sh(srv["stop"])
            out["rounds"].append(r)
            print(json.dumps({"round": rnd + 1, "server": srv["name"], "load_s": round(t_load, 2),
                              "envelopes_s": round(t_env, 2),
                              **{k: round(v["s"], 3) for k, v in r["tasks"].items()}}), flush=True)
            Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    ran = {r["server"] for r in out["rounds"]}
    if len(servers) < 2 or ran != {s["name"] for s in servers}:
        raise SystemExit("needs at least two servers, each with at least one completed round")
    out["identical_results"] = True
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    sys.exit(main())
