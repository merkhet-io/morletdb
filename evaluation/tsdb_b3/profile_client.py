#!/usr/bin/env python3
"""B3.1 development profiling (B3_SET=dev only): cProfile of MorletDB's client path for ingestion and reads.

  B3_SET=dev ../../.venv-b3/bin/python profile_client.py ingest|read [n_acquisitions]
"""
import cProfile
import os
import pstats
import sys
import time

import common
import morletdb_adapter as mo
import run

assert os.environ.get("B3_SET") == "dev", "profiling runs on the development set only"


def main(what, n=10):
    n = int(n)
    m = common.manifest()
    ks = list(range(n))
    data, _ = run.preload(m, ks)
    conn = mo.start("b3-dev-profile", "D2")
    mo.create(conn)
    prof = cProfile.Profile()
    t = time.perf_counter()
    if what == "ingest":
        prof.enable()
        for k in ks:
            mo.load_one(m["acquisitions"][k], data[k])
        prof.disable()
    else:
        for k in ks:
            mo.load_one(m["acquisitions"][k], data[k])
        t = time.perf_counter()
        prof.enable()
        for k in ks:
            a = m["acquisitions"][k]
            for off in range(0, 128000 * 2, 128000):
                mo.read(conn, a, ["vibration_1"], off, 128000)
        prof.disable()
    wall = time.perf_counter() - t
    samples = sum(c["samples"] for k in ks for c in m["acquisitions"][k]["channels"].values())
    print(f"{what}: {wall:.2f} s wall" + (f", {samples / wall / 1e6:.2f} M samples/s" if what == "ingest" else ""))
    pstats.Stats(prof).sort_stats("tottime").print_stats(18)
    conn.close()
    common.remove(mo.NAME, "b3-dev-profile")


if __name__ == "__main__":
    main(*sys.argv[1:])
