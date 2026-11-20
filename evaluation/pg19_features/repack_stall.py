#!/usr/bin/env python3
"""Giving disk space back while appends go on: REPACK (CONCURRENTLY) on 19 against VACUUM FULL on 17
(development measurement).

  python evaluation/pg19_features/repack_stall.py --server pg19 --servers servers.json --data DIR \
      --mode repack|vacuum_full --out results.json

Loads the data (prepare_data.py) into a fresh collection, removes every other recording and runs VACUUM (the
files keep their size), then rewrites the chunk table while one writer keeps appending 4,096-sample chunks to the
kept recordings, one transaction each, and logs every append's start and latency. Before the rewrite, a
REPEATABLE READ transaction takes its snapshot (reading only pg_class); after the rewrite it counts the collection's
chunks, to see whether an older snapshot still sees them. Checks afterwards: the kept chunks are byte for byte
unchanged, every concurrent append is stored, morlet_verify reports no problems.
"""
import argparse
import json
import os
import platform
import threading
import time
from pathlib import Path

import numpy as np
import psycopg

from same_session import SCHEMA, drop_schema, fresh_schema, sh, wait_ready

NAME = "rp19"
C = f"{SCHEMA}.{NAME}"


def q1(c, sql, args=None):
    return c.execute(sql, args).fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--servers", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--mode", choices=["repack", "vacuum_full"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    srv = next(s for s in json.loads(Path(a.servers).read_text()) if s["name"] == a.server)
    manifest = json.loads((Path(a.data) / "manifest.json").read_text())
    recs = manifest["recordings"]
    res = {"host": platform.platform(), "cpus": os.cpu_count(), "server": srv["name"], "mode": a.mode,
           "data": {k: manifest[k] for k in ("every", "samples", "samples_sha256")}}
    sh(srv["start"])
    try:
        wait_ready(srv["dsn"])
        with psycopg.connect(srv["dsn"], autocommit=True) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
            res["version"] = q1(c, "SHOW server_version")
            for s in ("wal_level", "max_wal_size", "shared_buffers"):
                res[s] = q1(c, f"SHOW {s}")
            fresh_schema(c)
            c.execute("SELECT morlet_create_stream_collection(%s, %s)", (SCHEMA, NAME))
            # chunks only: envelopes are not part of this check
            for r in recs:
                x = np.load(Path(a.data) / r["file"])
                c.execute("SELECT morlet_start_recording(%s::regclass,%s,%s,%s,%s,'{}'::jsonb,512,256,'zscore','none',%s)",
                          (C, r["recording_id"], r["rate"], r["channel"], r["unit"], r["quantum"]))
                with c.transaction():
                    for s in range(0, len(x), 32768):
                        c.execute("SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%s)",
                                  (C, r["recording_id"], s, x[s:s + 32768].tobytes()))
            res["size_full"] = q1(c, f"SELECT pg_total_relation_size('{C}_chunks')")
            ids = [r["recording_id"] for r in recs]
            removed = ids[0::2]
            kept = ids[1::2]
            c.execute(f"DELETE FROM {C} WHERE recording_id = ANY(%s)", (removed,))
            c.execute(f"VACUUM {C}, {C}_chunks")
            res["size_after_delete_vacuum"] = q1(c, f"SELECT pg_total_relation_size('{C}_chunks')")
            ends = {r["recording_id"]: r["samples"] for r in recs}
            # the chunks that exist before the rewrite, by key; the concurrent appends add others
            c.execute(f"CREATE TABLE {C}_keys AS SELECT recording_id, start_sample FROM {C}_chunks")
            sig_sql = (f"SELECT md5(string_agg(x.recording_id||':'||x.start_sample||':'||md5(x.encoded),',' "
                       f"ORDER BY x.recording_id,x.start_sample)) FROM {C}_chunks x JOIN {C}_keys k "
                       f"USING (recording_id, start_sample)")
            sig0 = q1(c, sig_sql)
            chunks0 = q1(c, f"SELECT count(*) FROM {C}_chunks")
            res["chunks_kept"] = chunks0

            stop = threading.Event()
            log = []
            attempts = [0]
            failures = []

            def writer():
                x = (np.arange(4096) % 97).astype("<f8").tobytes()
                offs = {rid: ends[rid] for rid in kept}
                with psycopg.connect(srv["dsn"], autocommit=True) as w:
                    i = 0
                    while not stop.is_set():
                        rid = kept[i % len(kept)]
                        t = time.perf_counter()
                        attempts[0] += 1
                        try:
                            w.execute("SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%s)", (C, rid, offs[rid], x))
                        except psycopg.Error as e:
                            failures.append(str(e).strip())
                            return
                        log.append((t, time.perf_counter() - t))
                        offs[rid] += 4096
                        i += 1

            rr = psycopg.connect(srv["dsn"])
            rr.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            rr.execute("SELECT count(*) FROM pg_class")        # takes the snapshot, no lock on the collection
            th = threading.Thread(target=writer, daemon=True)
            th.start()
            time.sleep(3)
            t0 = time.perf_counter()
            sql = (f"REPACK (CONCURRENTLY) {C}_chunks" if a.mode == "repack" else f"VACUUM FULL {C}_chunks")
            err = None
            try:
                c.execute(sql)
            except psycopg.Error as e:
                err = str(e).strip()
            t1 = time.perf_counter()
            time.sleep(3)
            stop.set()
            th.join()
            try:
                res["old_snapshot_sees_chunks"] = q1(rr, f"SELECT count(*) FROM {C}_chunks")
                rr.execute("COMMIT")
            except psycopg.Error as e:
                res["old_snapshot_error"] = str(e).strip()
            rr.close()
            res["statement"] = sql
            res["error"] = err
            res["rewrite_s"] = t1 - t0
            res["size_after_rewrite"] = q1(c, f"SELECT pg_total_relation_size('{C}_chunks')")
            starts = np.array([s for s, _ in log])
            lat = np.array([l for _, l in log])
            during = (starts + lat >= t0) & (starts <= t1)
            before = starts + lat < t0
            res["appends"] = {"total": len(log), "during_rewrite": int(during.sum()),
                              "latency_ms_before": {"p50": float(np.percentile(lat[before], 50) * 1e3),
                                                    "max": float(lat[before].max() * 1e3)},
                              "latency_ms_during": {"p50": float(np.percentile(lat[during], 50) * 1e3) if during.any() else None,
                                                    "max": float(lat[during].max() * 1e3) if during.any() else None},
                              "longest_wait_ms": float(lat.max() * 1e3)}
            # the longest stretch inside the rewrite in which no append completed
            done = np.sort(starts + lat)
            inside = done[(done >= t0) & (done <= t1 + lat.max())]
            edges = np.concatenate([[t0], inside, [max(t1, inside[-1] if len(inside) else t1)]])
            res["appends"]["longest_gap_without_completed_append_ms"] = float(np.diff(edges).max() * 1e3)
            res["kept_chunks_unchanged"] = q1(c, sig_sql) == sig0
            res["chunks_after"] = q1(c, f"SELECT count(*) FROM {C}_chunks")
            res["append_attempts"] = attempts[0]
            res["append_failures"] = failures
            res["all_appends_stored"] = (not failures and attempts[0] == len(log)
                                         and res["chunks_after"] == chunks0 + len(log))
            problems, after = 0, None
            while True:
                page = c.execute("SELECT recording_id, cardinality(problems) FROM morlet_verify(%s::regclass,%s,100)",
                                 (C, after)).fetchall()
                problems += sum(1 for p in page if p[1])
                if len(page) < 100:
                    break
                after = page[-1][0]
            res["verify_problems"] = problems
            drop_schema(c)
    finally:
        sh(srv["stop"])
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
