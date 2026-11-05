#!/usr/bin/env python3
"""I1 (issue #233): cost of `morlet.verify_appends`, development set, exploratory.

Arms (step 2's client settings, automatic batching, default encoding threads; alternating; B3's full correctness
check after every load; chunk digests compared):
  check     morletdb:b310: the CHECK constraint decodes every chunk (B3.10's build)
  verify    this branch, morlet.verify_appends=on: round trip on the encoding threads, CHECK skips own chunks
  trusted   this branch, morlet.verify_appends=off: no round trip, CHECK skips own chunks

  B3_SET=dev .venv-b3/bin/python evaluation/ingest_i1/verify.py
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tsdb_b3"))
os.environ.setdefault("B3_SET", "dev")

import batch  # noqa: E402
import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

BASE = {"commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0}
IMAGES = {"check": "morletdb:b310", "verify": "morletdb:i1-verify", "trusted": "morletdb:i1-verify"}
batch.ARMS = {"check": BASE, "verify": {**BASE, "verify_appends": "on"}, "trusted": {**BASE, "verify_appends": "off"}}
PLAN = [(a, "D2", r) for r in (1, 2, 3) for a in IMAGES] + [(a, "D1", r) for r in (1, 2) for a in IMAGES]


def main():
    assert os.environ["B3_SET"] == "dev"
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    samples = run.total_samples(m)
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True})
    out = {"set": "dev", "images": {a: common.sh(["docker", "image", "inspect", i, "--format", "{{.Id}}"]).strip()
                                    for a, i in IMAGES.items()},
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "load_average_before": common.sh(["uptime"]).strip(),
           "arms": batch.ARMS, "loads": []}
    for arm, d, rep in PLAN:
        mo.IMAGE = IMAGES[arm]
        mo.VARIANT.pop("verify_appends", None)
        r = batch.load(m, data, arm, d, False)
        r.update({"arm": arm, "durability": d, "rep": rep, "samples_per_s": round(samples / r["wall_s"])})
        out["loads"].append(r)
        print({k: r[k] for k in ("arm", "durability", "rep", "wall_s", "samples_per_s", "correct")},
              r["server"]["cpu_percent_mean"], flush=True)
        if not r["correct"] or r["samples_stored"] != samples:
            raise SystemExit("correctness failed")
    out["chunks_identical"] = len({x["chunk_digest"] for x in out["loads"]}) == 1
    out["median"] = {f"{a} {d}": round(statistics.median(x["samples_per_s"] for x in out["loads"]
                                                         if (x["arm"], x["durability"]) == (a, d)))
                     for a in IMAGES for d in ("D2", "D1")}
    out["load_average_after"] = common.sh(["uptime"]).strip()
    p = HERE / "results" / f"verify_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("chunks_identical", "median", "load_average_before", "load_average_after")},
                     indent=1))
    print(p)


if __name__ == "__main__":
    main()
