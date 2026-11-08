#!/usr/bin/env python3
"""Fill a real (small, loop-mounted) disk on purpose with the steward running (#287 done-when):

  python fill.py setup  --dsn ...                              # collection for the test
  python fill.py fill   --dsn ... --mount DIR --ballast FILE --out result.json

`fill` appends incompressible samples (float64 noise, about 8 bytes per sample) in batches of 262,144, one transaction
each, until an append is refused. It then records how the refusal came: SQLSTATE 53100 from the ingest hold, the
intended way, or any other error, which would mean the disk filled before the steward acted. It deletes the ballast
file to free space and appends again until the steward has released the hold. Finally, every acknowledged batch is
read back and compared bitwise (SHA-256), so no stored sample was lost. Free space is sampled with every batch.
"""
import argparse
import hashlib
import json
import os
import time

import numpy as np
import psycopg

C, BATCH, CHUNK = "fill", 262_144, 32_768


def free(mount):
    st = os.statvfs(mount)
    return st.f_bavail * st.f_frsize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["setup", "fill"])
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--mount")
    ap.add_argument("--ballast")
    ap.add_argument("--out")
    ap.add_argument("--resume-timeout", type=float, default=300)
    a = ap.parse_args()
    conn = psycopg.connect(a.dsn, autocommit=True)
    if a.command == "setup":
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        conn.execute("SELECT morlet_create_stream_collection('public',%s)", (C,))
        conn.execute("SELECT morlet_start_recording(%s::regclass,'noise',1000,'x','g')", (C,))
        return
    rng = np.random.default_rng(7)
    acked, timeline, refusal, t0 = [], [], None, time.time()
    end = 0
    while refusal is None:
        x = rng.standard_normal(BATCH).astype("<f8").tobytes()
        try:
            conn.execute("SELECT morlet_append_chunks_raw(%s::regclass,'noise',%s,%b,%s)", (C, end, x, CHUNK))
        except psycopg.Error as e:
            refusal = {"sqlstate": e.sqlstate, "message": str(e).splitlines()[0], "free_bytes": free(a.mount),
                       "t": time.time() - t0, "stored_samples": end}
            break
        acked.append((end, hashlib.sha256(x).hexdigest()))
        end += BATCH
        timeline.append((round(time.time() - t0, 1), free(a.mount), end))
    print("refused:", refusal, flush=True)
    os.remove(a.ballast)                       # free space: the steward should release its hold
    freed_at, resumed = time.time(), None
    while resumed is None and time.time() - freed_at < a.resume_timeout:
        time.sleep(2)
        x = rng.standard_normal(BATCH).astype("<f8").tobytes()
        try:
            conn.execute("SELECT morlet_append_chunks_raw(%s::regclass,'noise',%s,%b,%s)", (C, end, x, CHUNK))
        except psycopg.Error as e:
            if e.sqlstate != "53100":
                resumed = {"error": str(e).splitlines()[0]}
            continue
        acked.append((end, hashlib.sha256(x).hexdigest()))
        end += BATCH
        resumed = {"after_s": round(time.time() - freed_at, 1), "free_bytes": free(a.mount)}
    print("resumed:", resumed, flush=True)
    bad = 0
    for s, h in acked:
        back = conn.execute("SELECT morlet_read_interval_raw(%s::regclass,'noise',%s,%s)", (C, s, s + BATCH)).fetchone()[0]
        bad += hashlib.sha256(back).hexdigest() != h
    res = {"batches_acknowledged": len(acked), "samples": end, "read_back_mismatches": bad, "refusal": refusal,
           "resumed": resumed, "timeline": timeline[::max(1, len(timeline) // 60)],
           "actions": [list(r) for r in conn.execute(
               "SELECT job,outcome,args::text,extract(epoch FROM started_at)::bigint FROM morlet_steward.actions "
               "WHERE job='ingest_hold' ORDER BY action_id").fetchall()]}
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ("timeline",)}, indent=1))


if __name__ == "__main__":
    main()
