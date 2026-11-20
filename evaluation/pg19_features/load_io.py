#!/usr/bin/env python3
"""How much a load writes on PostgreSQL 17 and 19: WAL bytes, checkpoints and time per load (development
measurement). Separates "the server writes more" from "the disk was slower this time" when load times vary.

  python evaluation/pg19_features/load_io.py --data DIR --servers servers.json --rounds 5 --out results.json

Each round, for each server in alternating order: start it, CHECKPOINT, load the data (same_session.load, without
envelopes) into a fresh collection, CHECKPOINT again, and record the wall time of the load and of the final
checkpoint, the WAL bytes written (pg_current_wal_lsn), checkpoints requested and timed during the load, and the
collection's size.
"""
import argparse
import json
import os
import platform
import time
from pathlib import Path

import numpy as np
import psycopg

from same_session import SCHEMA, drop_schema, fresh_schema, sh, wait_ready

NAME = "io19"
C = f"{SCHEMA}.{NAME}"


def ckpt_counts(c):
    for sql in ("SELECT num_timed, num_requested FROM pg_stat_checkpointer",
                "SELECT checkpoints_timed, checkpoints_req FROM pg_stat_bgwriter"):
        try:
            with c.transaction():
                return list(c.execute(sql).fetchone())
        except psycopg.Error:
            continue
    return [None, None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--servers", required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = Path(a.data)
    recs = json.loads((data / "manifest.json").read_text())["recordings"]
    servers = json.loads(Path(a.servers).read_text())
    out = {"host": platform.platform(), "runs": []}
    for rnd in range(a.rounds):
        for srv in (servers if rnd % 2 == 0 else servers[::-1]):
            sh(srv["start"])
            try:
                wait_ready(srv["dsn"])
                with psycopg.connect(srv["dsn"], autocommit=True) as c:
                    c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
                    fresh_schema(c)
                    c.execute("SELECT morlet_create_stream_collection(%s, %s)", (SCHEMA, NAME))
                    c.execute("CHECKPOINT")
                    lsn0 = c.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
                    k0 = ckpt_counts(c)
                    t_db = 0.0
                    t0 = time.perf_counter()
                    for r in recs:
                        x = np.load(data / r["file"])
                        t = time.perf_counter()
                        c.execute("SELECT morlet_start_recording(%s::regclass,%s,%s,%s,%s,'{}'::jsonb,512,256,"
                                  "'zscore','none',%s)", (C, r["recording_id"], r["rate"], r["channel"], r["unit"],
                                                         r["quantum"]))
                        with c.transaction():
                            for s in range(0, len(x), 32768):
                                c.execute("SELECT morlet_append_chunk_raw(%s::regclass,%s,%s,%s)",
                                          (C, r["recording_id"], s, x[s:s + 32768].tobytes()))
                        t_db += time.perf_counter() - t
                    t_wall = time.perf_counter() - t0
                    k1 = ckpt_counts(c)
                    wal = c.execute("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), %s)", (lsn0,)).fetchone()[0]
                    t = time.perf_counter()
                    c.execute("CHECKPOINT")
                    t_ckpt = time.perf_counter() - t
                    run = {"round": rnd + 1, "server": srv["name"],
                           "version": c.execute("SHOW server_version").fetchone()[0],
                           "load_db_s": t_db, "load_wall_s": t_wall, "final_checkpoint_s": t_ckpt,
                           "wal_bytes": int(wal),
                           "checkpoints_timed": None if k0[0] is None else k1[0] - k0[0],
                           "checkpoints_requested": None if k0[1] is None else k1[1] - k0[1],
                           "collection_bytes": c.execute(f"SELECT pg_total_relation_size('{C}_chunks')").fetchone()[0],
                           "load_avg": os.getloadavg()}
                    drop_schema(c)
            finally:
                sh(srv["stop"])
            out["runs"].append(run)
            print(json.dumps({k: run[k] for k in ("round", "server", "load_db_s", "final_checkpoint_s", "wal_bytes",
                                                   "checkpoints_requested")}), flush=True)
            Path(a.out).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
