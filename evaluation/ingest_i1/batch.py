#!/usr/bin/env python3
"""I1 step 2 (issue #233): batched raw appends (`morlet_append_chunks_raw`, SQL 0.3.4), development set,
exploratory.

Both arms use step 1's pacing (256 chunks per transaction, one connection) on the same image; only the chunks
per call differ:

  step1   one `morlet_append_chunk_raw` call per chunk (rpc_chunks=1)
  batch   the importer's automatic batching (rpc_chunks=0: up to 64 chunks per `morlet_append_chunks_raw`)

Each load gets B3's full correctness check (every channel read back against the manifest's SHA-256, counts),
and the chunk digest is compared across arms and with step 1's loads on the released image.

  B3_SET=dev B3_MORLET_IMAGE=morletdb:i1-dev .venv-b3/bin/python evaluation/ingest_i1/batch.py
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

ARMS = {"step1": {"commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 1},
        "batch": {"commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0}}
PLAN = ([(a, "D2", False, r) for r in (1, 2) for a in ARMS] + [(a, "D1", False, r) for r in (1, 2) for a in ARMS]
        + [(a, "D2", True, 1) for a in ARMS])
STEP1 = HERE / "results/pacing_20261105T074353.json"


def load(m, data, arm, durability, search):
    mo.VARIANT.update(ARMS[arm])
    conn = mo.start("i1-batch", durability)
    mo.create(conn)
    t0, preflight, calls = time.perf_counter(), 0.0, 0
    with common.Stats(mo.NAME) as st:
        for a in m["acquisitions"]:
            _, _, rep = mo.load_one(a, data[a["k"]], search=search)
            preflight += rep["preflight_s"]
            calls += rep["rpc_calls"]
        wall = time.perf_counter() - t0 - preflight
    n, s, digest = conn.execute(f"SELECT count(*), sum(sample_count), md5(string_agg(md5(encoded), '' ORDER BY "
                                f"recording_id, start_sample)) FROM {mo.COLLECTION}_chunks").fetchone()
    seg = conn.execute(f"SELECT count(*), md5(string_agg(recording_id || ':' || start_sample || ':' || features::text,"
                       f" ';' ORDER BY recording_id, start_sample)) FROM {mo.COLLECTION}_segments").fetchone()
    cp = conn.execute(f"SELECT md5(string_agg(recording_id || checkpoint::text, ';' ORDER BY recording_id)) "
                      f"FROM {mo.COLLECTION}").fetchone()[0]
    correctness = run.full_counts_and_reads("morletdb", conn, m)
    if mo._WRITER:
        mo._WRITER[0].close()
    common.remove(mo.NAME)
    return {"wall_s": round(wall, 3), "rpc_calls": calls, "server": st.summary(), "chunks": n,
            "samples_stored": int(s), "chunk_digest": digest, "segments": list(seg), "checkpoint_digest": cp,
            "correct": bool(correctness["counts"]["ok"] and not correctness["reads"]["mismatches"])}


def main():
    assert os.environ["B3_SET"] == "dev"
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    samples = run.total_samples(m)
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True})
    out = {"set": "dev", "image": mo.IMAGE, "image_id": common.sh(["docker", "image", "inspect", mo.IMAGE, "--format",
                                                                  "{{.Id}}"]).strip(),
           "samples": samples, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "arms": ARMS, "loads": []}
    for arm, d, search, rep in PLAN:
        r = load(m, data, arm, d, search)
        r.update({"arm": arm, "durability": d, "search": search, "rep": rep,
                  "samples_per_s": round(samples / r["wall_s"])})
        out["loads"].append(r)
        print({k: r[k] for k in ("arm", "durability", "search", "rep", "wall_s", "samples_per_s", "rpc_calls",
                                 "correct")}, r["server"]["cpu_percent_mean"], flush=True)
        if not r["correct"] or r["samples_stored"] != samples:
            raise SystemExit("correctness failed")
    step1 = {x["chunk_digest"] for x in json.loads(STEP1.read_text())["loads"] if not x["search"]}
    for search in (False, True):
        xs = [x for x in out["loads"] if x["search"] == search]
        out[f"identical_{'search' if search else 'base'}"] = {
            "chunks": len({x["chunk_digest"] for x in xs}) == 1, "segments": len({tuple(x["segments"]) for x in xs}) == 1,
            "checkpoints": len({x["checkpoint_digest"] for x in xs}) == 1}
    out["chunks_equal_step1_release_image"] = {x["chunk_digest"] for x in out["loads"] if not x["search"]} == step1
    out["median"] = {}
    for arm, d, search, _ in PLAN:
        key = f"{arm} {d}" + (" search" if search else "")
        xs = [x for x in out["loads"] if (x["arm"], x["durability"], x["search"]) == (arm, d, search)]
        out["median"][key] = {k: round(statistics.median(x[k] for x in xs), 3) for k in ("wall_s", "samples_per_s")}
    p = HERE / "results" / f"batch_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("identical_base", "identical_search", "chunks_equal_step1_release_image",
                                          "median")}, indent=1))
    print(p)


if __name__ == "__main__":
    main()
