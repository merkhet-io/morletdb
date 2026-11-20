#!/usr/bin/env python3
"""pg_upgrade from PostgreSQL 17 to 19 with a pg_morlet collection (development check, not a benchmark).

  python evaluation/pg19_features/upgrade_check.py --bin17 DIR --bin19 DIR --data DIR --work DIR --out results.json

Both installations need the same pg_morlet version. The old cluster gets a collection with chunks, envelopes, time
anchors (SQL 0.3.13), row security and commit notifications. For each transfer mode (copy, clone, link), a fresh
copy of the old cluster is upgraded; the new cluster must hold byte for byte the same chunks, envelopes and anchors,
give the same search results and anchor times, keep row security and notifications, and pass morlet_verify.
It also records what a default `initdb` of 19 does to the upgrade (data checksums are on by default since 18)
and whether optimizer statistics come across (pg_upgrade transfers them since 18).
"""
import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import psycopg

from same_session import load

C = "up19"
PORT_OLD, PORT_NEW = 28937, 28939


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def initdb(bindir, d, extra=()):
    r = run([f"{bindir}/initdb", "-D", str(d), "-A", "trust", "-U", "postgres", *extra])
    if r.returncode:
        raise SystemExit(r.stderr)
    with open(d / "postgresql.conf", "a") as f:
        f.write("listen_addresses = 'localhost'\nunix_socket_directories = ''\nshared_buffers = 512MB\n")


def ctl(bindir, d, port, action):
    cmd = [f"{bindir}/pg_ctl", "-D", str(d), "-w", "-l", str(d) + ".log", "-o", f"-p {port}", action]
    if action == "stop":
        cmd[1:1] = ["-m", "fast"]
    r = run(cmd)
    if r.returncode:
        raise SystemExit(r.stdout + r.stderr)


def dsn(port):
    return f"host=localhost port={port} user=postgres dbname=postgres"


def snapshot(c, queries, anchored):
    q = lambda s, a=None: c.execute(s, a).fetchone()[0]  # noqa: E731
    out = {
        "extension": q("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'"),
        "chunks": q(f"SELECT md5(string_agg(recording_id||':'||start_sample||':'||md5(encoded),',' "
                    f"ORDER BY recording_id,start_sample)) FROM {C}_chunks"),
        "envelopes": q(f"SELECT md5(string_agg(recording_id||':'||start_sample||':'||md5(envelope),',' "
                       f"ORDER BY recording_id,start_sample)) FROM {C}_envelopes"),
        "anchors": q(f"SELECT md5(string_agg(t::text,',' ORDER BY anchor_id)) FROM {C}_time_anchors t"),
        "row_security": q("SELECT morlet_row_security_enabled(%s::regclass)", (C,)),
        "notifications": q("SELECT count(*) FROM morlet_notification_channel(%s::regclass)", (C,)),
        "recordings": q(f"SELECT count(*) FROM {C}"),
    }
    hits = [c.execute("SELECT recording_id, start_sample, distance FROM morlet_search_similar(%s::regclass,%s,%s,%s)",
                      (C, rid, s, s + 4096)).fetchall() for rid, s in queries]
    out["search"] = hashlib.md5(json.dumps([[(r[0], r[1], float(r[2]).hex()) for r in h] for h in hits]).encode()
                                ).hexdigest()
    times = [c.execute("SELECT utc_ns::text, uncertainty_ns::text FROM morlet_sample_utc(%s::regclass,%s,%s)",
                       (C, rid, n)).fetchone() for rid in anchored for n in (0, 1000, 100000, 250000)]
    out["anchor_times"] = hashlib.md5(json.dumps(times).encode()).hexdigest()
    page, problems, after = None, 0, None
    while page is None or len(page) == 100:
        page = c.execute("SELECT recording_id, cardinality(problems) FROM morlet_verify(%s::regclass,%s,100)",
                         (C, after)).fetchall()
        problems += sum(1 for p in page if p[1])
        after = page[-1][0] if page else None
    out["verify_problems"] = problems
    out["chunk_stats_rows"] = q(f"SELECT count(*) FROM pg_stats WHERE tablename='{C}_chunks'")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin17", required=True)
    ap.add_argument("--bin19", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--socketdir", default=None, help="pg_upgrade's socket directory (default: the work directory; "
                    "macOS limits socket paths to 103 bytes)")
    a = ap.parse_args()
    work = Path(a.work)
    sock = ["-s", a.socketdir or str(work)]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    manifest = json.loads((Path(a.data) / "manifest.json").read_text())
    recs = manifest["recordings"]
    res = {"host": platform.platform(), "data": {k: manifest[k] for k in ("every", "samples", "samples_sha256")}}

    old = work / "old"
    initdb(a.bin17, old)
    ctl(a.bin17, old, PORT_OLD, "start")
    with psycopg.connect(dsn(PORT_OLD), autocommit=True) as c:
        c.execute("CREATE EXTENSION pg_morlet")
        c.execute("SELECT morlet_create_stream_collection('public', %s)", (C,))
        c.execute("SELECT morlet_enable_envelopes(%s::regclass)", (C,))
        load(c, Path(a.data), recs, coll=C)
        c.execute("SELECT morlet_enable_time_anchors(%s::regclass)", (C,))
        anchored = [r["recording_id"] for r in recs[:4]]
        for i, rid in enumerate(anchored):
            t0 = 1_700_000_000_000_000_000 + i * 10**12
            rows = [{"recording_id": rid, "sample_offset": n, "utc_ns": str(t0 + n * 15_625 + (n // 50_000) * 7_000),
                     "uncertainty_ns": "1000", "source": "test:upgrade"} for n in (0, 100_000, 200_000)]
            c.execute("SELECT morlet_add_time_anchors(%s::regclass,%s,%s::jsonb)", (C, f"b{i}", json.dumps(rows)))
        c.execute(f"ALTER TABLE {C} ENABLE ROW LEVEL SECURITY")
        c.execute(f"CREATE POLICY own ON {C} FOR SELECT USING (true)")
        c.execute("SELECT morlet_enable_row_security(%s::regclass)", (C,))
        c.execute("SELECT morlet_enable_notifications(%s::regclass, NULL, false)", (C,))
        c.execute(f"VACUUM ANALYZE {C}, {C}_chunks, {C}_envelopes")
        windows = c.execute(f"SELECT recording_id, start_sample FROM {C}_envelopes ORDER BY 1, 2").fetchall()
        queries = [windows[i] for i in sorted(np.random.RandomState(1919).choice(len(windows), 20, replace=False))]
        res["before"] = snapshot(c, queries, anchored)
        res["old_version"] = c.execute("SHOW server_version").fetchone()[0]
    ctl(a.bin17, old, PORT_OLD, "stop")
    res["old_cluster_bytes"] = sum(f.stat().st_size for f in old.rglob("*") if f.is_file())

    # a 19 cluster from a default initdb: data checksums are on by default since 18, the 17 cluster has none
    probe = work / "probe"
    initdb(a.bin19, probe)
    r = run([f"{a.bin19}/pg_upgrade", "--check", "-b", a.bin17, "-B", a.bin19, "-d", str(old), "-D", str(probe),
             "-p", str(PORT_OLD), "-P", str(PORT_NEW), "-U", "postgres", *sock], cwd=work)
    res["default_initdb_check"] = {"returncode": r.returncode,
                                   "message": [l for l in (r.stdout + r.stderr).splitlines() if "checksum" in l.lower()]}
    shutil.rmtree(probe)

    res["modes"] = {}
    for mode in ("copy", "clone", "link"):
        src = work / f"old-{mode}"
        if run(["cp", "-cR", str(old), str(src)]).returncode:     # APFS clone of the old cluster
            shutil.copytree(old, src)
        new = work / f"new-{mode}"
        initdb(a.bin19, new, ("--no-data-checksums",))
        args = [f"{a.bin19}/pg_upgrade", "-b", a.bin17, "-B", a.bin19, "-d", str(src), "-D", str(new),
                "-p", str(PORT_OLD), "-P", str(PORT_NEW), "-U", "postgres", *sock, f"--{mode}"]
        chk = run(args[:1] + ["--check"] + args[1:], cwd=work)
        t = time.perf_counter()
        r = run(args, cwd=work)
        elapsed = time.perf_counter() - t
        m = {"check_returncode": chk.returncode, "returncode": r.returncode, "upgrade_s": elapsed}
        if r.returncode:
            m["output"] = (r.stdout + r.stderr)[-3000:]
            res["modes"][mode] = m
            continue
        ctl(a.bin19, new, PORT_NEW, "start")
        with psycopg.connect(dsn(PORT_NEW), autocommit=True) as c:
            m["new_version"] = c.execute("SHOW server_version").fetchone()[0]
            m["after"] = snapshot(c, queries, anchored)
            c.execute("ALTER EXTENSION pg_morlet UPDATE")
            m["after_update_extension"] = c.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'"
                                                    ).fetchone()[0]
            same = {k: m["after"][k] == res["before"][k] for k in res["before"] if k != "chunk_stats_rows"}
            m["identical"] = same
            m["all_identical"] = all(same.values())
            t = time.perf_counter()
            c.execute("ANALYZE")
            m["analyze_s"] = time.perf_counter() - t
        ctl(a.bin19, new, PORT_NEW, "stop")
        res["modes"][mode] = m
        print(mode, json.dumps({k: m[k] for k in m if k not in ("after",)}), flush=True)
        shutil.rmtree(new)
        shutil.rmtree(src)
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    ok = all(m.get("all_identical") for m in res["modes"].values())
    print("PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
