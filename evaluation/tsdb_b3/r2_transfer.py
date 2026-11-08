#!/usr/bin/env python3
"""Compressed transfer against raw reads (docs/compressed-transfer.md). Exploratory, development set; not a frozen
measurement, and not a comparison with another database.

One D2 load (I1 path, given image, which must have morlet_read_interval_encoded); then B3's read intervals,
REPS passes each, timed from the client, with the transfers alternating within every pass:
  raw          morlet_read_interval_raw as binary bytea, numpy.frombuffer (what B3 measures today)
  encoded_1    morlet_read_interval_encoded, decoded by the native library on one thread
  encoded_4    the same on up to four threads
R1 (4,096 samples), R2 (128,000) and R3 (3 channels x 64,000, one statement per read as B3's I1 arms do).
Every timed read is compared bit for bit with the source. Also reported: bytes transferred per read, and server
work without transfer (octet_length of each function's result). Medians and p95 in ms.

  B3_SET=dev B3_MORLET_IMAGE=morletdb:ct MORLETDB_NATIVE_LIB=target/release/libmorlet_ffi.dylib \\
    .venv-b3/bin/python evaluation/tsdb_b3/r2_transfer.py
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
sys.path.insert(0, str(HERE.parents[1] / "sdk" / "python" / "src"))
os.environ.setdefault("B3_SET", "dev")

import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402
from morletdb import _native  # noqa: E402

REPS = int(os.environ.get("CT_REPS", "50"))
RAW = "SELECT morlet_read_interval_raw(%s::regclass, %s, %s, %s)"
ENC = "SELECT morlet_read_interval_encoded(%s::regclass, %s, %s, %s, %s)"
RAW3 = ("SELECT morlet_read_interval_raw(%s::regclass, r.id, %s, %s) FROM unnest(%s::text[]) WITH ORDINALITY "
        "r(id, o) ORDER BY r.o")
ENC3 = ("SELECT morlet_read_interval_encoded(%s::regclass, r.id, %s, %s, %s) FROM unnest(%s::text[]) WITH "
        "ORDINALITY r(id, o) ORDER BY r.o")


def timed(f):
    t = time.perf_counter()
    v = f()
    return time.perf_counter() - t, v


def summary(v):
    v = sorted(v)
    return {"p50": round(statistics.median(v) * 1e3, 3), "p95": round(v[int(0.95 * len(v))] * 1e3, 3)}


def main():
    native = _native.native()
    if native is None:
        sys.exit("set MORLETDB_NATIVE_LIB to the built libmorlet_ffi")
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True, "commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0})
    h = mo.start("r2-transfer", "D2")
    mo.create(h)
    run.ingest("morletdb-i1", h, m, data)
    if mo._WRITER:
        mo._WRITER[0].close()
    mo.settle(h)
    plans = common.read_plan(m)
    acq = {a["k"]: a for a in m["acquisitions"]}
    conn = mo.connect()
    v = native.max_chunk_version
    res = {"image": mo.IMAGE, "reps": REPS, "max_chunk_version": v, "measures": {}}

    def enc(threads):
        def f(frame, a, b):
            native.threads = threads
            return native.decode(frame, a, b)
        return f

    for name in ("R1", "R2"):
        plan = plans[name]
        n = plan["length"]
        t = {"raw": [], "encoded_1": [], "encoded_4": []}
        size = {"raw": [], "encoded": []}
        server = {"raw": [], "encoded": []}
        for k, off in plan["pairs"]:
            want = data[k]["vibration_1"][off:off + n].view(np.int64)
            args = (mo.COLLECTION, mo.rid(acq[k], "vibration_1"), off, off + n)
            conn.execute(RAW, args, binary=True, prepare=True).fetchone()
            conn.execute(ENC, args + (v,), binary=True, prepare=True).fetchone()
            for _ in range(REPS):
                dt, got = timed(lambda: np.frombuffer(conn.execute(RAW, args, binary=True, prepare=True)
                                                      .fetchone()[0], dtype="<f8"))
                t["raw"].append(dt)
                assert np.array_equal(got.view(np.int64), want)
                for arm, dec in (("encoded_1", enc(1)), ("encoded_4", enc(4))):
                    dt, got = timed(lambda: dec(conn.execute(ENC, args + (v,), binary=True, prepare=True)
                                                .fetchone()[0], off, off + n))
                    t[arm].append(dt)
                    assert np.array_equal(got.view(np.int64), want), (name, arm, k, off)
            size["raw"].append(len(conn.execute(RAW, args, binary=True).fetchone()[0]))
            size["encoded"].append(len(conn.execute(ENC, args + (v,), binary=True).fetchone()[0]))
            for arm, lq, a in (("raw", "SELECT octet_length(morlet_read_interval_raw(%s::regclass, %s, %s, %s))", args),
                               ("encoded", "SELECT octet_length(morlet_read_interval_encoded(%s::regclass, %s, %s, %s, %s))",
                                args + (v,))):
                for _ in range(10):
                    server[arm].append(timed(lambda: conn.execute(lq, a, prepare=True).fetchone())[0])
        res["measures"][name] = {"samples": n, "reads": len(plan["pairs"]) * REPS,
                                 "client": {k2: summary(x) for k2, x in t.items()},
                                 "server_no_transfer": {k2: summary(x) for k2, x in server.items()},
                                 "bytes_per_read": {k2: int(statistics.median(x)) for k2, x in size.items()}}
        print(name, json.dumps(res["measures"][name]))

    plan = plans["R3"]
    n = plan["length"]
    t = {"raw": [], "encoded_1": [], "encoded_4": []}
    for k, off in plan["pairs"]:
        chans = plan.get("channels") or ["vibration_1", "current_1", "current_2"]
        ids = [mo.rid(acq[k], c) for c in chans]
        want = [data[k][c][off:off + n].view(np.int64) for c in chans]
        for _ in range(REPS):
            dt, rows = timed(lambda: [np.frombuffer(r[0], dtype="<f8") for r in conn.execute(
                RAW3, (mo.COLLECTION, off, off + n, ids), binary=True, prepare=True).fetchall()])
            t["raw"].append(dt)
            assert all(np.array_equal(g.view(np.int64), w) for g, w in zip(rows, want))
            for arm, dec in (("encoded_1", enc(1)), ("encoded_4", enc(4))):
                dt, rows = timed(lambda: [dec(r[0], off, off + n) for r in conn.execute(
                    ENC3, (mo.COLLECTION, off, off + n, v, ids), binary=True, prepare=True).fetchall()])
                t[arm].append(dt)
                assert all(np.array_equal(g.view(np.int64), w) for g, w in zip(rows, want)), (arm, k, off)
    res["measures"]["R3"] = {"samples": n, "channels": len(ids), "reads": len(plan["pairs"]) * REPS,
                             "client": {k2: summary(x) for k2, x in t.items()}}
    print("R3", json.dumps(res["measures"]["R3"]))
    res["load_average"] = common.sh(["uptime"]).strip()
    common.remove(mo.NAME)
    out = HERE / "results" / "dev" / f"r2_transfer_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=1))
    print(out)


if __name__ == "__main__":
    main()
