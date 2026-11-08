#!/usr/bin/env python3
"""Restore drill R1 (#192): base backup plus WAL archive, a lost database, and two restores (latest, and a point in
time), each checked bit for bit. Runs with Docker on any machine (and in CI):

  python evaluation/restore_r1/drill.py --image ghcr.io/merkhet-io/morletdb:<tag> --out result.json

1. A database (the image, data checksums, `archive_mode=on`, WAL archived to a volume) gets phase A: batches of
   deterministic samples (`power_cut_p1.writer.values`) with one context row each, then envelopes built.
2. `pg_basebackup` (plain, WAL streamed) into a backup volume.
3. Phase B batches; the time T is taken from the database after B's last commit. Phase C batches. The WAL is switched
   and the drill waits until every segment is archived.
4. The disaster: the database container and its data volume are removed.
5. Restore *latest*: a new data volume from the base backup, `restore_command` from the archive, recovery to the end.
   It must hold A+B+C exactly.
6. Restore *point in time*: the same with `recovery_target_time = T`. It must hold A+B exactly, and nothing of C.
Each restore is checked: recording end, every sample against the generator, interval statistics, exactly one context
row per batch, envelopes, `morlet_verify` without problems, extension version. Exit status 1 on any problem.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "power_cut_p1"))
from writer import SEED, values, write_batch  # noqa: E402

P = "r1drill"
C = "pc_study"
BATCH = 4096


def sh(*args, check=True, capture=False):
    r = subprocess.run(args, check=check, text=True, capture_output=capture)
    return r.stdout.strip() if capture else r


def docker(*args, **kw):
    return sh("docker", *args, **kw)


def start(name: str, image: str, data: str, port: int, extra: list[str]) -> None:
    docker("run", "-d", "--name", name, "-e", "POSTGRES_USER=morlet", "-e", "POSTGRES_PASSWORD=drill",
           "-e", "POSTGRES_DB=morlet", "-e", "POSTGRES_INITDB_ARGS=--data-checksums",
           "-v", f"{data}:/var/lib/postgresql/data", "-v", f"{P}-archive:/archive", "-v", f"{P}-backup:/backup",
           "-p", f"127.0.0.1:{port}:5432", image, "postgres", "-c", "max_wal_size=256MB", "-c", "wal_compression=on",
           *extra, capture=True)


def connect(port: int, timeout: float = 180):
    import psycopg
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            conn = psycopg.connect(f"postgresql://morlet:drill@127.0.0.1:{port}/morlet", autocommit=True,
                                   connect_timeout=5)
            conn.execute("SELECT 1")
            return conn
        except psycopg.Error:
            time.sleep(2)
    raise RuntimeError(f"database on port {port} did not come up")


def write(conn, first: int, n: int) -> int:
    for b in range(first, first + n):
        with conn.transaction():
            write_batch(conn, C, "pc", b * BATCH, BATCH, BATCH)
    return first + n


def check(conn, batches: int, version: str) -> list[str]:
    import numpy as np
    bad: list[str] = []
    end = int(conn.execute(f"SELECT (checkpoint->>'end')::bigint FROM {C} WHERE recording_id='pc'").fetchone()[0])
    if end != batches * BATCH:
        bad.append(f"recording ends at {end}, expected {batches * BATCH}")
    n = min(end, batches * BATCH)
    for s0 in range(0, n, 32 * BATCH):
        s1 = min(n, s0 + 32 * BATCH)
        raw = conn.execute("SELECT morlet_read_interval_raw(%s::regclass,'pc',%s,%s)", (C, s0, s1)).fetchone()[0]
        if not np.array_equal(np.frombuffer(raw, dtype="<f8"), values(SEED["pc"], s0, s1 - s0)):
            bad.append(f"samples {s0}..{s1} differ")
    want = values(SEED["pc"], 0, n)
    st = conn.execute("SELECT sample_count, min, max FROM morlet_interval_stats(%s::regclass,'pc',0,%s)",
                      (C, n)).fetchone()
    if n and (int(st[0]), st[1], st[2]) != (n, float(want.min()), float(want.max())):
        bad.append(f"interval statistics differ: {st}")
    ctx = conn.execute(f"SELECT count(*), count(DISTINCT start_sample), coalesce(max(end_sample),0) FROM {C}_ctx "
                       "WHERE recording_id='pc' AND key='batch'").fetchone()
    if tuple(int(x) for x in ctx) != (batches, batches, batches * BATCH):
        bad.append(f"context rows {tuple(ctx)} for {batches} batches")
    env = conn.execute(f"SELECT count(*), count(*) FILTER (WHERE start_sample + window_size > %s) FROM {C}_envelopes "
                       "WHERE recording_id='pc'", (end,)).fetchone()
    if int(env[1]):
        bad.append(f"{env[1]} envelopes beyond the end")
    for rid, chunks, problems in conn.execute(f"SELECT recording_id, chunks, problems FROM morlet_verify('{C}')"):
        if problems:
            bad.append(f"morlet_verify {rid}: {problems[:3]}")
    v = conn.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]
    if v != version:
        bad.append(f"extension {v}, expected {version}")
    return bad


def restore(image: str, name: str, port: int, target: str | None) -> None:
    vol = f"{P}-{name}"
    docker("volume", "create", vol, capture=True)
    docker("run", "--rm", "-v", f"{P}-backup:/backup", "-v", f"{vol}:/d", "--entrypoint", "sh", image, "-c",
           "cp -a /backup/base/. /d/ && touch /d/recovery.signal && chown -R postgres:postgres /d && chmod 700 /d")
    extra = ["-c", "restore_command=cp /archive/%f %p", "-c", "recovery_target_action=promote"]
    if target:
        extra += ["-c", f"recovery_target_time={target}"]
    start(f"{P}-{name}", image, vol, port, extra)


def cleanup() -> None:
    for c in ("db", "latest", "pitr"):
        docker("rm", "-fv", f"{P}-{c}", check=False, capture=True)
    for v in ("data", "archive", "backup", "latest", "pitr"):
        docker("volume", "rm", "-f", f"{P}-{v}", check=False, capture=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--batches", type=int, nargs=3, default=[120, 40, 40], metavar=("A", "B", "C"))
    ap.add_argument("--out")
    ap.add_argument("--keep", action="store_true", help="leave the containers and volumes for inspection")
    a = ap.parse_args(argv)
    from writer import setup
    cleanup()
    res: dict = {"image": a.image, "batches": a.batches}
    t0 = time.time()
    try:
        start(f"{P}-db", a.image, f"{P}-data", 55432,
              ["-c", "archive_mode=on", "-c", "archive_command=test ! -f /archive/%f && cp %p /archive/%f",
               "-c", "archive_timeout=60"])
        conn = connect(55432)
        docker("exec", f"{P}-db", "sh", "-c", "chown postgres /archive /backup")
        setup(conn, C)
        version = conn.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]
        na, nb, nc = a.batches
        b = write(conn, 0, na)
        while conn.execute(f"SELECT morlet_build_envelopes('{C}','pc',2048,512,1000)").fetchone()[0]:
            pass
        docker("exec", f"{P}-db", "pg_basebackup", "-U", "morlet", "-D", "/backup/base", "-Fp", "-X", "stream",
               "-c", "fast")
        b = write(conn, b, nb)
        target = conn.execute("SELECT now()::text").fetchone()[0]
        time.sleep(1.5)
        b = write(conn, b, nc)
        conn.execute("CHECKPOINT")
        last = conn.execute("SELECT pg_walfile_name(pg_switch_wal())").fetchone()[0]
        for _ in range(120):
            if conn.execute("SELECT last_archived_wal >= %s FROM pg_stat_archiver", (last,)).fetchone()[0]:
                break
            time.sleep(1)
        else:
            raise RuntimeError("WAL not archived")
        before = check(conn, na + nb + nc, version)
        res["before_disaster"] = before
        conn.close()
        docker("rm", "-fv", f"{P}-db", capture=True)                     # the disaster
        docker("volume", "rm", f"{P}-data", capture=True)
        res["disaster"] = "container and data volume removed"
        for name, port, tgt, n in (("latest", 55433, None, na + nb + nc), ("pitr", 55434, target, na + nb)):
            t = time.time()
            restore(a.image, name, port, tgt)
            rc = connect(port, 600)
            for _ in range(300):    # wait for recovery to finish and promote
                if not rc.execute("SELECT pg_is_in_recovery()").fetchone()[0]:
                    break
                time.sleep(1)
            res[name] = {"target": tgt, "expected_batches": n, "seconds": round(time.time() - t, 1),
                         "problems": check(rc, n, version)}
            rc.close()
    except Exception as exc:  # noqa: BLE001 - reported
        res["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        res["total_s"] = round(time.time() - t0, 1)
        if not a.keep:
            cleanup()
    ok = "error" not in res and not res.get("before_disaster") and all(
        not res.get(k, {}).get("problems", ["missing"]) for k in ("latest", "pitr"))
    res["pass"] = ok
    print(json.dumps(res, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
