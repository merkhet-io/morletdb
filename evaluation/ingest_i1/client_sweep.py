#!/usr/bin/env python3
"""I1 step 0 (issue #233), client side: how much of a single-connection load is the importer's own pacing.

Development set only, exploratory. Fresh D2 load per variant and repetition through the `morletdb-033` path,
varying only `commit_chunks` (chunks per transaction; the importer drains its pipeline at each commit, so the
server waits while the client prepares the next batch). Also times opening a connection, which the importer
does once per acquisition.

  B3_SET=dev B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1 \
    .venv-b3/bin/python evaluation/ingest_i1/client_sweep.py
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tsdb_b3"))
os.environ.setdefault("B3_SET", "dev")

import psycopg  # noqa: E402

import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

COMMITS = (16, 64, 256)
REPS = 2


def main():
    assert os.environ["B3_SET"] == "dev"
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    samples = run.total_samples(m)
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True})
    out = {"set": "dev", "image": mo.IMAGE, "samples": samples, "acquisitions": len(m["acquisitions"]),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "loads": []}
    for rep in range(REPS):
        for cc in COMMITS:
            mo.VARIANT["commit_chunks"] = cc
            conn = mo.start("i1-sweep", "D2")
            mo.create(conn)
            with common.Stats(mo.NAME) as st:
                r, _ = run.ingest("morletdb-033", conn, m, data)
            n = conn.execute(f"SELECT count(*), sum(sample_count) FROM {mo.COLLECTION}_chunks").fetchone()
            assert int(n[1]) == samples, "sample count mismatch"
            if rep == 0 and cc == COMMITS[0]:
                ts = []
                for _ in range(50):
                    t = time.perf_counter()
                    c = psycopg.connect(mo.DSN)
                    c.execute("SELECT 1")
                    c.close()
                    ts.append(time.perf_counter() - t)
                out["connect_ms"] = {"p50": round(statistics.median(ts) * 1e3, 2), "max": round(max(ts) * 1e3, 2)}
            out["loads"].append({"commit_chunks": cc, "rep": rep + 1, "wall_s": round(r["wall_s"], 3),
                                 "samples_per_s": round(samples / r["wall_s"]), "server": st.summary()})
            print(out["loads"][-1], flush=True)
            common.remove(mo.NAME)
    out["median_wall_s"] = {cc: statistics.median(x["wall_s"] for x in out["loads"] if x["commit_chunks"] == cc)
                            for cc in COMMITS}
    p = HERE / "results" / f"client_sweep_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("connect_ms", "median_wall_s")}, indent=1))
    print(p)


if __name__ == "__main__":
    main()
