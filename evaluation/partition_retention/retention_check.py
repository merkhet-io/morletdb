#!/usr/bin/env python3
"""Removing the oldest month of chunks while appends go on: DELETE + VACUUM, DELETE + VACUUM + REPACK (CONCURRENTLY),
and a time-partitioned table with DETACH PARTITION ... CONCURRENTLY + DROP (#211; development measurement).

  python evaluation/partition_retention/retention_check.py --data DIR --dsn DSN --fs-path / --rounds 2 --out r.json

PostgreSQL 19 (REPACK). Real chunk bytes: the data (evaluation/pg19_features/prepare_data.py) is loaded once into a
pg_morlet collection, and each arm copies its chunks into a fresh table of its own: part 1 (the oldest month) and
part 2 (the next one), each a full copy. While the removal of part 1 runs, one writer keeps inserting chunks (copies
of real encoded chunks) into part 3 at a fixed rate (--rate per second, one transaction each), as a collector would,
and logs every insert's start and latency.

Each arm runs twice per round: a space pass without other readers, and a snapshot pass in which a REPEATABLE READ
transaction takes its snapshot before the removal (reading only pg_class) and counts parts 1 and 2 ten seconds after
the removal started. They are separate because an open older snapshot changes the removal itself: VACUUM cannot
reclaim rows that snapshot may still see, and DETACH ... CONCURRENTLY waits for it.

Measured per arm: the removal's duration; the longest wait for one insert and the inserts completed during the
removal; WAL written; the filesystem's free space before, right after, and after a CHECKPOINT; the tables' total
size; whether part 2 is byte for byte unchanged and every insert is stored. The arms' tables have the chunk table's
columns, but are not pg_morlet collections: this compares storage operations, not MorletDB's functions.
Everything lives in the schema morlet_retention_check, which the script creates and drops. Python 3.9+, psycopg 3.
"""
import argparse
import json
import math
import os
import platform
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pg19_features"))
from same_session import EXTERNAL_DEPENDENTS  # noqa: E402  (objects outside a schema that depend on it)

SCHEMA = "morlet_retention_check"
MARK = "created by evaluation/partition_retention; dropped and recreated by its script"
SRC = f"{SCHEMA}.src"
ARMS = ("delete_vacuum", "delete_vacuum_repack", "detach_drop")


def q1(c, sql, args=None):
    return c.execute(sql, args).fetchone()[0]


def drop_schema(c):
    """Drop the script's schema: only if it carries the script's mark and nothing outside it depends on it."""
    row = c.execute("SELECT obj_description(oid,'pg_namespace') FROM pg_namespace WHERE nspname=%s", (SCHEMA,)).fetchone()
    if row is None:
        return
    if row[0] != MARK:
        raise SystemExit(f"schema {SCHEMA} exists and was not created by this script")
    n = q1(c, EXTERNAL_DEPENDENTS, {"s": SCHEMA})
    if n:
        raise SystemExit(f"{n} object(s) outside schema {SCHEMA} depend on it: remove them before running")
    c.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def fresh_schema(c):
    drop_schema(c)
    c.execute(f"CREATE SCHEMA {SCHEMA}")
    c.execute(f"COMMENT ON SCHEMA {SCHEMA} IS '{MARK}'")


def load_source(c, data):
    recs = json.loads((data / "manifest.json").read_text())["recordings"]
    c.execute("SELECT morlet_create_stream_collection(%s,'src')", (SCHEMA,))
    for r in recs:
        x = np.load(data / r["file"])
        c.execute("SELECT morlet_start_recording(%s::regclass,%s,%s,%s,%s,'{}'::jsonb,512,256,'zscore','none',%s)",
                  (SRC, r["recording_id"], r["rate"], r["channel"], r["unit"], r["quantum"]))
        with c.transaction():
            for s in range(0, len(x), 32768):
                c.execute("SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%s)",
                          (SRC, r["recording_id"], s, x[s:s + 32768].tobytes()))
    c.execute(f"VACUUM ANALYZE {SRC}_chunks")


def make_table(c, arm):
    t = f"{SCHEMA}.t"
    cols = ("part int NOT NULL, recording_id text NOT NULL, start_sample bigint NOT NULL, sample_count int NOT NULL, "
            "encoded bytea NOT NULL, PRIMARY KEY (part, recording_id, start_sample)")
    if arm == "detach_drop":
        c.execute(f"CREATE TABLE {t} ({cols}) PARTITION BY RANGE (part)")
        for p in (1, 2, 3):
            c.execute(f"CREATE TABLE {t}_{p} PARTITION OF {t} FOR VALUES FROM ({p}) TO ({p + 1})")
            c.execute(f"ALTER TABLE {t}_{p} ALTER COLUMN encoded SET COMPRESSION pglz")
    else:
        c.execute(f"CREATE TABLE {t} ({cols})")
        c.execute(f"ALTER TABLE {t} ALTER COLUMN encoded SET COMPRESSION pglz")
    for p in (1, 2):
        c.execute(f"INSERT INTO {t} SELECT {p}, recording_id, start_sample, sample_count, encoded FROM {SRC}_chunks")
    c.execute(f"VACUUM ANALYZE {t}")
    return t


def tables_size(c):
    return q1(c, "SELECT coalesce(sum(pg_total_relation_size(c.oid)),0)::bigint FROM pg_class c JOIN pg_namespace n "
                 "ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p') AND c.relname LIKE 't%%'",
              (SCHEMA,))


def fs_free(path):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize


def run_arm(dsn, arm, fs_path, rate, reader_on):
    res = {"arm": arm, "pass": "snapshot" if reader_on else "space", "rate_per_s": rate}
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(f"DROP TABLE IF EXISTS {SCHEMA}.t CASCADE")
        t = make_table(c, arm)
        res["rows_per_part"] = q1(c, f"SELECT count(*) FROM {t} WHERE part=1")
        part2_sig = (f"SELECT md5(string_agg(recording_id||':'||start_sample||':'||sample_count||':'||md5(encoded),',' "
                     f"ORDER BY recording_id,start_sample)) FROM {t} WHERE part=2")
        sig0 = q1(c, part2_sig)
        payloads = [r[0] for r in c.execute(f"SELECT encoded FROM {SRC}_chunks ORDER BY recording_id, start_sample "
                                            "LIMIT 64").fetchall()]
        c.execute("CHECKPOINT")
        time.sleep(2)
        res["size_before"] = tables_size(c)
        res["fs_free_before"] = fs_free(fs_path)
        lsn0 = q1(c, "SELECT pg_current_wal_lsn()")

        stop = threading.Event()
        log, failures = [], []

        def writer():
            with psycopg.connect(dsn, autocommit=True) as w:
                i = 0
                begin = time.perf_counter()
                while not stop.is_set():
                    pause = begin + i / rate - time.perf_counter()        # a fixed rate, as a collector appends
                    if pause > 0:
                        time.sleep(pause)
                    t0 = time.perf_counter()
                    try:
                        w.execute(f"INSERT INTO {t} VALUES (3, 'new', %s, 32768, %s)", (i * 32768, payloads[i % len(payloads)]))
                    except psycopg.Error as e:
                        failures.append(str(e).strip())
                        return
                    log.append((t0, time.perf_counter() - t0))
                    i += 1

        snapshot = {}
        rr = None
        if reader_on:
            rr = psycopg.connect(dsn)
            rr.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            rr.execute("SELECT count(*) FROM pg_class")

        def reader(start):
            time.sleep(max(0.0, start + 10 - time.perf_counter()))
            try:
                snapshot["part1"] = q1(rr, f"SELECT count(*) FROM {t} WHERE part=1")
                snapshot["part2"] = q1(rr, f"SELECT count(*) FROM {t} WHERE part=2")
            except psycopg.Error as e:
                snapshot["error"] = str(e).strip()
            rr.execute("COMMIT")

        th = threading.Thread(target=writer, daemon=True)
        th.start()
        time.sleep(5)
        t_start = time.perf_counter()
        rd = threading.Thread(target=reader, args=(t_start,), daemon=True)
        if reader_on:
            rd.start()
        steps = {}
        try:
            if arm == "detach_drop":
                s = time.perf_counter()
                c.execute(f"ALTER TABLE {t} DETACH PARTITION {t}_1 CONCURRENTLY")   # waits for older snapshots
                steps["detach_s"] = time.perf_counter() - s
                s = time.perf_counter()
                c.execute(f"DROP TABLE {t}_1")
                steps["drop_s"] = time.perf_counter() - s
            else:
                s = time.perf_counter()
                c.execute(f"DELETE FROM {t} WHERE part=1")
                steps["delete_s"] = time.perf_counter() - s
                s = time.perf_counter()
                c.execute(f"VACUUM {t}")
                steps["vacuum_s"] = time.perf_counter() - s
                if arm == "delete_vacuum_repack":
                    s = time.perf_counter()
                    c.execute(f"REPACK (CONCURRENTLY) {t}")
                    steps["repack_s"] = time.perf_counter() - s
        except psycopg.Error as e:
            res["error"] = str(e).strip()
        t_end = time.perf_counter()
        res["fs_free_after"] = fs_free(fs_path)
        if reader_on:
            rd.join(timeout=60)
        time.sleep(3)
        stop.set()
        th.join(timeout=60)
        res["steps"] = steps
        res["removal_s"] = t_end - t_start
        res["wal_bytes"] = int(q1(c, "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), %s)", (lsn0,)))
        c.execute("CHECKPOINT")
        time.sleep(2)
        res["fs_free_after_checkpoint"] = fs_free(fs_path)
        res["size_after"] = tables_size(c)
        res["old_snapshot_sees"] = snapshot if reader_on else None
        res["inserted_bytes"] = q1(c, f"SELECT coalesce(sum(octet_length(encoded)),0)::bigint FROM {t} WHERE part=3")
        starts = np.array([s for s, _ in log])
        lat = np.array([x for _, x in log])
        during = (starts + lat >= t_start) & (starts <= t_end)
        before = starts + lat < t_start
        done = np.sort(starts + lat)
        inside = done[(done >= t_start) & (done <= t_end + (lat.max() if len(lat) else 0))]
        edges = np.concatenate([[t_start], inside, [max(t_end, inside[-1] if len(inside) else t_end)]])
        res["inserts"] = {
            "total": len(log), "failures": failures, "during_removal": int(during.sum()),
            "p50_ms_before": float(np.percentile(lat[before], 50) * 1e3) if before.any() else None,
            "p50_ms_during": float(np.percentile(lat[during], 50) * 1e3) if during.any() else None,
            "max_ms_during": float(lat[during].max() * 1e3) if during.any() else None,
            "longest_gap_ms": float(np.diff(edges).max() * 1e3),
        }
        res["part2_unchanged"] = q1(c, part2_sig) == sig0
        res["all_inserts_stored"] = (not failures and len(log) > 0
                                     and q1(c, f"SELECT count(*) FROM {t} WHERE part=3") == len(log))
        res["part1_left"] = q1(c, f"SELECT count(*) FROM {t} WHERE part=1")
        res["ok"] = (res.get("error") is None and res["part1_left"] == 0 and res["part2_unchanged"]
                     and res["all_inserts_stored"])
        c.execute(f"DROP TABLE {t} CASCADE")
        c.execute(f"DROP TABLE IF EXISTS {t}_1")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--fs-path", default="/", help="a path on the filesystem that holds the database")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--rate", type=float, default=10.0, help="chunks inserted per second during the removal")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if not (math.isfinite(a.rate) and a.rate > 0):
        ap.error("--rate must be a positive number")
    out = {"host": platform.platform(), "runs": []}
    with psycopg.connect(a.dsn, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        out["version"] = q1(c, "SHOW server_version")
        out["settings"] = {s: q1(c, f"SHOW {s}") for s in ("shared_buffers", "max_wal_size", "wal_level",
                                                          "data_checksums")}
        fresh_schema(c)
        load_source(c, Path(a.data))
        out["source_chunks"] = q1(c, f"SELECT count(*) FROM {SRC}_chunks")
        out["source_bytes"] = q1(c, f"SELECT pg_total_relation_size('{SRC}_chunks')")
    for rnd in range(a.rounds):
        for arm in (ARMS if rnd % 2 == 0 else ARMS[::-1]):
            for reader_on in (False, True):
                r = {"round": rnd + 1, "load_avg": os.getloadavg(),
                     **run_arm(a.dsn, arm, a.fs_path, a.rate, reader_on)}
                out["runs"].append(r)
                print(json.dumps({k: r[k] for k in ("round", "arm", "pass", "removal_s", "wal_bytes")} |
                                 {"fs_gain": r["fs_free_after_checkpoint"] - r["fs_free_before"],
                                  "size_change": r["size_after"] - r["size_before"],
                                  "max_ms": r["inserts"]["max_ms_during"], "snap": r["old_snapshot_sees"],
                                  "ok": r["ok"]}), flush=True)
                Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    with psycopg.connect(a.dsn, autocommit=True) as c:
        drop_schema(c)


if __name__ == "__main__":
    main()
