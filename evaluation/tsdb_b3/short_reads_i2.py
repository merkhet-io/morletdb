#!/usr/bin/env python3
"""Why were short reads slower in B3.15's compressed-transfer arm? Exploratory, development set; not a frozen
measurement and not a comparison with another database.

In B3.15, `morletdb-i2` read R1 and R4 (4,096 samples, below the SDK's encoded threshold, so raw in both arms)
1.1-1.4x slower than `morletdb-i1` on the same release. Candidate causes, each isolated by one client variant:
  i1        raw transfer (B3.15's morletdb-i1)
  i2        compressed transfer as in B3.15 (decoder looked up on every read)
  i2-once   i2 with the transfer decided once per length, as the SDK does       -> (a) per-read harness cost
  i1-lib    i1 with the SDK imported and the native library loaded, no encoded reads -> (b) process side effects
  i2-noR23  i2 without R2 and R3, so no encoded read precedes R4                -> (d) state left by encoded reads
Every variant runs in a fresh process against one loaded server, in an order rotated every round              -> (c)
Reads as B3's harness does them (run.reads, 50 repetitions x 20 intervals, every read checked bit for bit).

  B3_SET=dev B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.7-alpha.1 MORLETDB_NATIVE_LIB=.../libmorlet_ffi.dylib \\
    .venv-b3/bin/python evaluation/tsdb_b3/short_reads_i2.py [rounds]
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "sdk" / "python" / "src"))
os.environ.setdefault("B3_SET", "dev")
import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

VARIANTS = ["i1", "i2", "i2-once", "i1-lib", "i2-noR23"]
ALL = ["R1", "R2", "R3", "R4_recent", "R4_older"]


def child(variant):
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True, "encoded_read": variant.startswith("i2")})
    keep = ["R1", "R4_recent", "R4_older"] if variant == "i2-noR23" else ALL
    if variant == "i1-lib":
        from morletdb import _native
        _native.require()
    if variant == "i2-once":
        decided, original = {}, mo._encoded_reader
        mo._encoded_reader = lambda n: decided[n] if n in decided else decided.setdefault(n, original(n))
    plan = common.read_plan
    common.read_plan = lambda m: {k: v for k, v in plan(m).items() if k in keep}
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    r = run.reads("morletdb", None, m, data)
    print("RESULT " + json.dumps({k: {"p50": v["p50_ms"], "p95": v["p95_ms"], "mismatches": v["mismatches"]}
                                  for k, v in r.items()}), flush=True)


def main(rounds=5):
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True, "commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0})
    h = mo.start("short-reads-i2", "D2")
    mo.create(h)
    run.ingest("morletdb-i1", h, m, data)
    if mo._WRITER:
        mo._WRITER[0].close()
    mo.settle(h)
    del data
    out = {"image": mo.IMAGE, "native_lib": os.environ.get("MORLETDB_NATIVE_LIB"), "rounds": []}
    for r in range(rounds):
        order = VARIANTS[r % len(VARIANTS):] + VARIANTS[:r % len(VARIANTS)]
        row = {}
        for v in order:
            p = subprocess.run([sys.executable, __file__, "child", v], capture_output=True, text=True, env=os.environ)
            line = [x for x in p.stdout.splitlines() if x.startswith("RESULT ")]
            if p.returncode or not line:
                sys.exit(f"{v} failed:\n{p.stderr[-2000:]}")
            row[v] = json.loads(line[-1][7:])
            print(r + 1, v, {k: (x["p50"], x["p95"]) for k, x in row[v].items()}, flush=True)
        out["rounds"].append({"order": order, "results": row})
    dest = common.SET_DIR / f"short_reads_i2_{time.strftime('%Y%m%dT%H%M%S')}.json"
    dest.write_text(json.dumps(out, indent=1))
    print(dest)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        child(sys.argv[2])
    else:
        main(*(int(a) for a in sys.argv[1:]))
