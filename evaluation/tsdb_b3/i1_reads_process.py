#!/usr/bin/env python3
"""I1 read investigation, part 2 (exploratory, not a stage): is the read penalty after an I1 load in the
database or in the benchmark client?

B3 times reads from the same Python process that just ingested. For each arm (`morletdb-033`, `morletdb-i1`,
both on `morletdb:b310`), alternating, two fresh D2 loads; after B3's correctness check and settle, on the same
load and server:
  a  reads in the ingesting process (as B3 does)
  b  reads in a fresh Python process
  c  reads in the ingesting process again, after gc.collect()
Python garbage-collector counts and the process's resident memory are recorded before (a).

  B3_MORLET_IMAGE=morletdb:b310 .venv-b3/bin/python evaluation/tsdb_b3/i1_reads_process.py
"""
import gc
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

READ_VARIANT = {"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True, "one_statement": True}
ARMS = {"morletdb-033": {}, "morletdb-i1": {"commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0}}
QUERIES = ["R1", "R2", "R3", "R4_recent", "R4_older", "R5"]


def p50s(reads, analytics):
    out = {q: reads[q]["p50_ms"] for q in QUERIES}
    out["analytics"] = analytics["p50_ms"]
    return out


def reads_only():
    """Child process: the read benchmark against the running server, printed as JSON."""
    mo.VARIANT.update(READ_VARIANT)
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    r = run.reads("morletdb", None, m, data)
    a = run.analytics("morletdb", None, m, data)
    print(json.dumps(p50s(r, a)))


def main():
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    out = {"image": mo.IMAGE, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "load_average": common.sh(["uptime"]).strip(),
           "loads": []}
    for rep in (1, 2):
        for arm, extra in ARMS.items():
            mo.VARIANT.update({**READ_VARIANT, "commit_chunks": 16, "reuse_connection": False, "rpc_chunks": 1, **extra})
            handle = mo.start(f"i1-proc-{arm}", "D2")
            mo.create(handle)
            r, _ = run.ingest(arm, handle, m, data)
            if mo._WRITER:
                mo._WRITER[0].close()
            correct = run.full_counts_and_reads("morletdb", handle, m)
            mo.settle(handle)
            rec = {"arm": arm, "rep": rep, "samples_per_s": round(run.total_samples(m) / r["wall_s"]),
                   "correct": bool(correct["counts"]["ok"] and not correct["reads"]["mismatches"]),
                   "gc_counts": gc.get_count(), "gc_objects": len(gc.get_objects()),
                   "maxrss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1)}
            rec["a_same_process"] = p50s(run.reads("morletdb", handle, m, data), run.analytics("morletdb", handle, m, data))
            child = subprocess.run([sys.executable, __file__, "reads-only"], capture_output=True, text=True, check=True,
                                   env=os.environ)
            rec["b_fresh_process"] = json.loads(child.stdout.strip().splitlines()[-1])
            gc.collect()
            rec["c_same_process_after_gc"] = p50s(run.reads("morletdb", handle, m, data),
                                                  run.analytics("morletdb", handle, m, data))
            out["loads"].append(rec)
            print(json.dumps(rec), flush=True)
            common.remove(mo.NAME)
    p = HERE / "results" / f"i1r-process_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(p)


if __name__ == "__main__":
    reads_only() if sys.argv[1:] == ["reads-only"] else main()
