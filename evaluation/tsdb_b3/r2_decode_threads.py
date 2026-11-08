#!/usr/bin/env python3
"""Exact reads (R1, R2) with server-side decoding on 1 or 4 threads (morlet.decode_threads), exploratory,
development set. One D2 load (I1 path); B3's intervals, 50 passes each, alternating the setting per pass, timed
from the client as B3 does (binary bytea + numpy.frombuffer) and inside the server; read-back checked.

  B3_SET=dev B3_MORLET_IMAGE=morletdb:pdec .venv-b3/bin/python evaluation/tsdb_b3/r2_decode_threads.py
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

REPS, SETTINGS = 50, (1, 4)


def main():
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True, "commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0})
    h = mo.start("r2-dec", "D2")
    mo.create(h)
    run.ingest("morletdb-i1", h, m, data)
    if mo._WRITER:
        mo._WRITER[0].close()
    mo.settle(h)
    plan = common.read_plan(m)
    acq = {a["k"]: a for a in m["acquisitions"]}
    conns = {t: mo.connect() for t in SETTINGS}
    for t, c in conns.items():
        c.execute(f"SET morlet.decode_threads = {t}")
    raw = "SELECT morlet_read_interval_raw(%s::regclass, %s, %s, %s)"
    res = {}
    for q in ("R1", "R2"):
        n = plan[q]["length"]
        lat = {t: [] for t in SETTINGS}
        inside = {t: [] for t in SETTINGS}
        for k, off in plan[q]["pairs"]:
            args = (mo.COLLECTION, mo.rid(acq[k], "vibration_1"), off, off + n)
            for t, c in conns.items():
                c.execute(raw, args, binary=True, prepare=True).fetchone()
            for _ in range(REPS):
                for t, c in conns.items():
                    s = time.perf_counter()
                    np.frombuffer(c.execute(raw, args, binary=True, prepare=True).fetchone()[0], dtype="<f8")
                    lat[t].append(time.perf_counter() - s)
            for t, c in conns.items():
                v = np.frombuffer(c.execute(raw, args, binary=True).fetchone()[0], dtype="<f8")
                assert np.array_equal(v.view(np.int64), data[k]["vibration_1"][off:off + n].view(np.int64))
        ids = [mo.rid(acq[k], "vibration_1") for k, _ in plan[q]["pairs"]]
        st = [o for _, o in plan[q]["pairs"]]
        en = [o + n for _, o in plan[q]["pairs"]]
        for _ in range(5):
            for t, c in conns.items():
                s = time.perf_counter()
                c.execute("SELECT sum(octet_length(morlet_read_interval_raw(%s::regclass, u.id, u.s, u.e))) FROM "
                          "unnest(%s::text[], %s::bigint[], %s::bigint[]) u(id, s, e), generate_series(1, %s)",
                          (mo.COLLECTION, ids, st, en, REPS)).fetchone()
                inside[t].append((time.perf_counter() - s) / (len(ids) * REPS))
        res[q] = {f"threads_{t}": {"client_p50_ms": round(statistics.median(lat[t]) * 1e3, 3),
                                   "client_p95_ms": round(sorted(lat[t])[int(.95 * len(lat[t]))] * 1e3, 3),
                                   "server_ms": round(statistics.median(inside[t]) * 1e3, 3)} for t in SETTINGS}
    res["image"] = mo.IMAGE
    res["load_average"] = common.sh(["uptime"]).strip()
    common.remove(mo.NAME)
    out = HERE / "results" / "dev" / f"r2_decode_threads_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
