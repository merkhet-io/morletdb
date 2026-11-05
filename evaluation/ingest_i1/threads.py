#!/usr/bin/env python3
"""I1 step 3 (issue #233): chunk encoding on worker threads (`morlet.encode_threads`), development set,
exploratory.

Every arm uses step 2's client settings (256 chunks per transaction, one connection, automatic batching) on
the same image and differs only in `morlet.encode_threads` (1 = the backend's own thread, as in step 2).
Arms alternate; every load gets B3's full correctness check, and chunk, segment and checkpoint digests are
compared across arms and with step 2's loads.

  B3_SET=dev B3_MORLET_IMAGE=morletdb:i1-dev3 .venv-b3/bin/python evaluation/ingest_i1/threads.py
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
batch.ARMS = {f"t{n}": {**BASE, "encode_threads": n} for n in (1, 2, 4, 8)}
PLAN = ([(f"t{n}", "D2", False, r) for r in (1, 2) for n in (1, 2, 4, 8)]
        + [(f"t{n}", "D1", False, r) for r in (1, 2) for n in (1, 4)]
        + [(f"t{n}", "D2", True, 1) for n in (1, 4)])
STEP2 = HERE / "results/batch_20261105T084529.json"


def main():
    assert os.environ["B3_SET"] == "dev"
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    samples = run.total_samples(m)
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True})
    out = {"set": "dev", "image": mo.IMAGE, "image_id": common.sh(["docker", "image", "inspect", mo.IMAGE, "--format",
                                                                  "{{.Id}}"]).strip(),
           "samples": samples, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "arms": batch.ARMS, "loads": [],
           "load_average_before": common.sh(["uptime"]).strip()}
    for arm, d, search, rep in PLAN:
        r = batch.load(m, data, arm, d, search)
        r.update({"arm": arm, "durability": d, "search": search, "rep": rep,
                  "samples_per_s": round(samples / r["wall_s"])})
        out["loads"].append(r)
        print({k: r[k] for k in ("arm", "durability", "search", "rep", "wall_s", "samples_per_s", "correct")},
              r["server"]["cpu_percent_mean"], flush=True)
        if not r["correct"] or r["samples_stored"] != samples:
            raise SystemExit("correctness failed")
    step2 = json.loads(STEP2.read_text())["loads"]
    for search in (False, True):
        xs = [x for x in out["loads"] + step2 if x["search"] == search]
        out[f"identical_with_step2_{'search' if search else 'base'}"] = {
            "chunks": len({x["chunk_digest"] for x in xs}) == 1,
            "segments": len({tuple(x["segments"]) for x in xs}) == 1,
            "checkpoints": len({x["checkpoint_digest"] for x in xs}) == 1}
    out["median"] = {}
    for arm, d, search, _ in PLAN:
        key = f"{arm} {d}" + (" search" if search else "")
        xs = [x for x in out["loads"] if (x["arm"], x["durability"], x["search"]) == (arm, d, search)]
        out["median"][key] = {k: round(statistics.median(x[k] for x in xs), 3) for k in ("wall_s", "samples_per_s")}
        out["median"][key]["server_cpu_percent"] = round(statistics.median(x["server"]["cpu_percent_mean"] for x in xs), 1)
    out["load_average_after"] = common.sh(["uptime"]).strip()
    p = HERE / "results" / f"threads_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k.startswith(("identical", "median", "load_average"))},
                     indent=1))
    print(p)


if __name__ == "__main__":
    main()
