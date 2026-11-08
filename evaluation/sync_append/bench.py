#!/usr/bin/env python3
"""#292: appending 8 channels at 50 kHz as one synchronous group against 8 separate appends.

  python -I evaluation/sync_append/bench.py --dsn "..." --out results/<machine>.json

Two arms in one session, alternating per round so host load hits both alike:
  group     morlet_append_acquisition_raw: one call per batch for all 8 members (one transaction)
  separate  morlet_append_chunks_raw once per member and batch, all 8 in one transaction
Each batch is 1 s of every channel (50,000 samples, chunks of 32,768), on a value grid like ADC counts. Each round
appends 20 batches per arm into fresh recordings. Sample bytes are sent as binary parameters (psycopg's %b): with %s a
list of bytes goes as a text array, which took 105 ms per batch against 24 ms. Reports samples per second (all channels) per round and the load
average before and after. After the run, every recording is read back and compared bitwise with what was sent.
"""
import argparse
import json
import os
import platform
import time
from pathlib import Path

import numpy as np
import psycopg

RATE, CHANNELS, BATCH, BATCHES, ROUNDS, CHUNK, C = 50000, 8, 50000, 20, 3, 32768, "sb"


def signals(seed):
    rng = np.random.default_rng(seed)
    t = np.arange(BATCH * BATCHES) / RATE
    out = []
    for ch in range(CHANNELS):
        x = 0.8 * np.sin(2 * np.pi * 50 * t + ch * np.pi / 4) + 0.05 * rng.standard_normal(t.size)
        out.append(np.round(x * 2 ** 15) / 2 ** 15)   # a 16-bit grid
    return out


def drop(c):
    names = [r[0] for r in c.execute("SELECT quote_ident(tablename) FROM pg_tables WHERE schemaname='public' "
                                     "AND (tablename=%s OR tablename LIKE %s)", (C, C + "\\_%"))]
    if names:
        c.execute(f"DROP TABLE {', '.join(names)} CASCADE")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = signals(1)
    roles = [f"ch{k}" for k in range(CHANNELS)]
    res = {"machine": platform.machine(), "rounds": [], "load_before": list(os.getloadavg())}
    with psycopg.connect(a.dsn, autocommit=True) as c:
        res["server"] = c.execute("SELECT version()").fetchone()[0]
        drop(c)
        c.execute("SELECT morlet_create_stream_collection('public', %s)", (C,))
        c.execute("SELECT morlet_enable_acquisitions(%s::regclass)", (C,))
        c.execute("SELECT morlet_enable_sync(%s::regclass)", (C,))
        sent = {}
        for rnd in range(ROUNDS):
            row = {}
            for arm in ("group", "separate") if rnd % 2 == 0 else ("separate", "group"):
                acq = f"{arm}{rnd}"
                ids = [f"{acq}/{r}" for r in roles]
                with c.transaction():
                    for rid in ids:
                        c.execute("SELECT morlet_start_recording(%s::regclass,%s,%s,'v','V','{}',512,256,'zscore','none',"
                                  "%s)", (C, rid, RATE, 2 ** -15))
                    if arm == "group":
                        c.execute("SELECT morlet_create_acquisition(%s::regclass,%s,'bench')", (C, acq))
                        for rid, r in zip(ids, roles):
                            c.execute("SELECT morlet_add_acquisition_channel(%s::regclass,%s,%s,%s)", (C, acq, rid, r))
                        c.execute("SELECT morlet_declare_sync(%s::regclass,%s,%s)", (C, acq, RATE))
                t0 = time.perf_counter()
                for b in range(BATCHES):
                    s = b * BATCH
                    parts = [d[s:s + BATCH].astype("<f8").tobytes() for d in data]
                    with c.transaction():
                        if arm == "group":
                            c.execute("SELECT morlet_append_acquisition_raw(%s::regclass,%s,%s,%s,%b,%s)",
                                      (C, acq, s, roles, parts, CHUNK))
                        else:
                            for rid, p in zip(ids, parts):
                                c.execute("SELECT morlet_append_chunks_raw(%s::regclass,%s,%s,%b,%s)",
                                          (C, rid, s, p, CHUNK))
                dt = time.perf_counter() - t0
                row[arm] = {"seconds": dt, "samples_per_s": CHANNELS * BATCH * BATCHES / dt}
                sent[acq] = ids
            res["rounds"].append(row)
            print(rnd, {k: round(v["samples_per_s"]) for k, v in row.items()}, flush=True)
        # bitwise read-back of every recording
        n = BATCH * BATCHES
        for acq, ids in sent.items():
            for rid, d in zip(ids, data):
                back = b"".join(c.execute("SELECT morlet_read_interval_raw(%s::regclass,%s,%s,%s)",
                                          (C, rid, s, min(s + 1_000_000, n))).fetchone()[0]
                                for s in range(0, n, 1_000_000))
                assert back == d.astype("<f8").tobytes(), rid
        res["bitwise_read_back"] = True
        res["bytes_per_sample"] = c.execute(f"SELECT sum(octet_length(encoded))::float8/sum(sample_count) "
                                            f"FROM {C}_chunks").fetchone()[0]
        drop(c)
    res["load_after"] = list(os.getloadavg())
    for arm in ("group", "separate"):
        res[f"median_{arm}_samples_per_s"] = float(np.median([r[arm]["samples_per_s"] for r in res["rounds"]]))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "rounds"}, indent=1))


if __name__ == "__main__":
    main()
