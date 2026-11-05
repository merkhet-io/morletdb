#!/usr/bin/env python3
"""I1 step 1 (issue #233): the importer's pacing changes, development set, exploratory.

Arms (B3's `morletdb-033` path otherwise; fresh server per load; full sample count and the chunk digest are
checked after every load):

  old       16 chunks per transaction, one connection per acquisition (B3's frozen arm)
  commit256 256 chunks per transaction (the new importer default), one connection per acquisition
  new       256 chunks per transaction and one connection for the whole load

Per load: wall time, server CPU, and `commit_wait_s` from the importer (time the client waited at commits for
the server to finish the queued statements); wall minus that wait is the client's own busy time, an upper
bound on what the client costs. D2 and D1 for `old` and `new`; one searchable (features) load each.

  B3_SET=dev B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1 \
    .venv-b3/bin/python evaluation/ingest_i1/pacing.py
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

import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

ARMS = {"old": {"commit_chunks": 16, "reuse_connection": False},
        "commit256": {"commit_chunks": 256, "reuse_connection": False},
        "new": {"commit_chunks": 256, "reuse_connection": True}}
PLAN = ([(a, "D2", False, r) for r in (1, 2) for a in ("old", "commit256", "new")]
        + [(a, "D1", False, r) for r in (1, 2) for a in ("old", "new")]
        + [(a, "D2", True, 1) for a in ("old", "new")])


def load(m, data, arm, durability, search):
    mo.VARIANT.update(ARMS[arm])
    conn = mo.start("i1-pacing", durability)
    mo.create(conn)
    wait, t0 = 0.0, time.perf_counter()
    with common.Stats(mo.NAME) as st:
        preflight = 0.0
        for a in m["acquisitions"]:
            _, _, rep = mo.load_one(a, data[a["k"]], search=search)
            preflight += rep["preflight_s"]
            wait += rep["commit_wait_s"]
        wall = time.perf_counter() - t0 - preflight
    n, s, digest = conn.execute(f"SELECT count(*), sum(sample_count), md5(string_agg(md5(encoded), '' ORDER BY "
                                f"recording_id, start_sample)) FROM {mo.COLLECTION}_chunks").fetchone()
    if mo._WRITER:
        mo._WRITER[0].close()
    common.remove(mo.NAME)
    return {"wall_s": round(wall, 3), "commit_wait_s": round(wait, 3), "client_busy_s": round(wall - wait, 3),
            "server": st.summary(), "chunks": n, "samples_stored": int(s), "chunk_digest": digest}


def main():
    assert os.environ["B3_SET"] == "dev"
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    samples = run.total_samples(m)
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True})
    out = {"set": "dev", "image": mo.IMAGE, "samples": samples, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "arms": ARMS, "loads": []}
    for arm, d, search, rep in PLAN:
        r = load(m, data, arm, d, search)
        if r["samples_stored"] != samples:
            raise SystemExit(f"{arm} {d}: stored {r['samples_stored']} of {samples} samples")
        r.update({"arm": arm, "durability": d, "search": search, "rep": rep,
                  "samples_per_s": round(samples / r["wall_s"])})
        out["loads"].append(r)
        print({k: r[k] for k in ("arm", "durability", "search", "rep", "wall_s", "samples_per_s", "commit_wait_s",
                                 "client_busy_s")}, r["server"]["cpu_percent_mean"], flush=True)
    digests = {(x["search"],): set() for x in out["loads"]}
    for x in out["loads"]:
        digests[(x["search"],)].add(x["chunk_digest"])
    out["chunks_identical_across_arms"] = all(len(v) == 1 for v in digests.values())
    out["median"] = {}
    for arm, d, search, _ in PLAN:
        key = f"{arm} {d}" + (" search" if search else "")
        xs = [x for x in out["loads"] if (x["arm"], x["durability"], x["search"]) == (arm, d, search)]
        out["median"][key] = {k: round(statistics.median(x[k] for x in xs), 3)
                              for k in ("wall_s", "samples_per_s", "commit_wait_s", "client_busy_s")}
    p = HERE / "results" / f"pacing_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps({"chunks_identical_across_arms": out["chunks_identical_across_arms"], "median": out["median"]},
                     indent=1))
    print(p)


if __name__ == "__main__":
    main()
