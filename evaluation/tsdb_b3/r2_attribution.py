#!/usr/bin/env python3
"""Where a long exact read (R2: 128,000 samples of one channel) spends its time. Exploratory, development set.

One D2 load (I1 path, given image); then, for B3's R2 intervals, 50 passes each, timed from the client:
  ping        SELECT 1 (round trip only)
  server      SELECT octet_length(morlet_read_interval_raw(...))  (server work, no sample transfer)
  full        B3's read: morlet_read_interval_raw(...) as binary bytea, then numpy.frombuffer (what R2 measures)
  bytes_only  a stored 1,024,000-byte bytea of the same size, selected as binary (transfer without decoding)
  inside      the same reads inside the server in one statement (no client per read), per read
Medians in ms.

  B3_SET=dev B3_MORLET_IMAGE=morletdb:i1-verify .venv-b3/bin/python evaluation/tsdb_b3/r2_attribution.py
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("B3_SET", "dev")

import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

REPS = 50


def timed(f):
    t = time.perf_counter()
    f()
    return time.perf_counter() - t


def main():
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True, "commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0})
    h = mo.start("r2-attr", "D2")
    mo.create(h)
    run.ingest("morletdb-i1", h, m, data)
    if mo._WRITER:
        mo._WRITER[0].close()
    mo.settle(h)
    plan = common.read_plan(m)["R2"]
    acq = {a["k"]: a for a in m["acquisitions"]}
    n = plan["length"]
    conn = mo.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS r2_blob AS SELECT decode(repeat('ab', %s), 'hex') AS b", (8 * n,))
    conn.execute("ALTER TABLE r2_blob ALTER COLUMN b SET STORAGE EXTERNAL")
    conn.execute("TRUNCATE r2_blob")
    conn.execute("INSERT INTO r2_blob SELECT decode(repeat('ab', %s), 'hex')", (8 * n,))
    raw = "SELECT morlet_read_interval_raw(%s::regclass, %s, %s, %s)"
    lens = "SELECT octet_length(morlet_read_interval_raw(%s::regclass, %s, %s, %s))"
    t = {k: [] for k in ("ping", "server", "full", "bytes_only")}
    for k, off in plan["pairs"]:
        args = (mo.COLLECTION, mo.rid(acq[k], "vibration_1"), off, off + n)
        conn.execute(raw, args, binary=True, prepare=True).fetchone()   # warm-up
        for _ in range(REPS):
            t["ping"].append(timed(lambda: conn.execute("SELECT 1", prepare=True).fetchone()))
            t["server"].append(timed(lambda: conn.execute(lens, args, prepare=True).fetchone()))
            t["full"].append(timed(lambda: np.frombuffer(conn.execute(raw, args, binary=True, prepare=True)
                                                         .fetchone()[0], dtype="<f8")))
            t["bytes_only"].append(timed(lambda: conn.execute("SELECT b FROM r2_blob", binary=True, prepare=True)
                                         .fetchone()))
        got = np.frombuffer(conn.execute(raw, args, binary=True).fetchone()[0], dtype="<f8")
        assert np.array_equal(got.view(np.int64), data[k]["vibration_1"][off:off + n].view(np.int64))
    ids = [mo.rid(acq[k], "vibration_1") for k, _ in plan["pairs"]]
    starts = [off for _, off in plan["pairs"]]
    ends = [off + n for _, off in plan["pairs"]]
    inside = []
    for _ in range(5):
        inside.append(timed(lambda: conn.execute(
            "SELECT sum(octet_length(morlet_read_interval_raw(%s::regclass, u.id, u.s, u.e))) FROM "
            "unnest(%s::text[], %s::bigint[], %s::bigint[]) u(id, s, e), generate_series(1, %s)",
            (mo.COLLECTION, ids, starts, ends, REPS)).fetchone()) / (len(ids) * REPS))
    res = {k: round(statistics.median(v) * 1e3, 3) for k, v in t.items()}
    res["inside"] = round(statistics.median(inside) * 1e3, 3)
    res["p95"] = {k: round(sorted(v)[int(0.95 * len(v))] * 1e3, 3) for k, v in t.items()}
    res["image"] = mo.IMAGE
    res["load_average"] = common.sh(["uptime"]).strip()
    common.remove(mo.NAME)
    out = HERE / "results" / "dev" / f"r2_attribution_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
