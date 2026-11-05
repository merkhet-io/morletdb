#!/usr/bin/env python3
"""I1 read investigation, part 3 (exploratory, not a stage): read cost inside the server, without the client.

For each arm (`morletdb-033`, `morletdb-i1`, both on `morletdb:b310`), alternating, two fresh D2 loads; after B3's
correctness check and settle, B3's planned read intervals are read inside the server, one statement per query
type covering all its intervals 50 times (`morlet_read_interval_raw` per channel, as B3's raw reads), so there is
one round trip per statement and no per-read client or network cost. Each statement runs five times; reported
is microseconds per read (median of five).

  B3_MORLET_IMAGE=morletdb:b310 .venv-b3/bin/python evaluation/tsdb_b3/i1_reads_server.py
"""
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

ARMS = {"morletdb-033": {}, "morletdb-i1": {"commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0}}
READ_VARIANT = {"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True, "one_statement": True}
REPS, ROUNDS = 50, 5


def server_reads(conn, m):
    plan = common.read_plan(m)
    acq = {a["k"]: a for a in m["acquisitions"]}
    out = {}
    for q in ("R1", "R2", "R3", "R4_recent", "R4_older", "R5"):
        spec = plan[q]
        rows = [(mo.rid(acq[k], c), off, off + spec["length"]) for k, off in spec["pairs"] for c in spec["channels"]]
        ids, starts, ends = map(list, zip(*rows))
        sql = ("SELECT sum(octet_length(morlet_read_interval_raw(%s::regclass, u.id, u.s, u.e))) "
               "FROM unnest(%s::text[], %s::bigint[], %s::bigint[]) u(id, s, e), generate_series(1, %s)")
        conn.execute(sql, (mo.COLLECTION, ids, starts, ends, 1))       # warm-up
        ts = []
        for _ in range(ROUNDS):
            t = time.perf_counter()
            got = conn.execute(sql, (mo.COLLECTION, ids, starts, ends, REPS)).fetchone()[0]
            ts.append(time.perf_counter() - t)
        assert got == 8 * spec["length"] * len(spec["channels"]) * len(spec["pairs"]) * REPS
        reads = len(spec["pairs"]) * REPS                                # B3 counts a multi-channel read as one
        out[q] = {"us_per_read": round(statistics.median(ts) / reads * 1e6, 1),
                  "rounds_us": [round(t / reads * 1e6, 1) for t in ts]}
    return out


def main():
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    out = {"image": mo.IMAGE, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "load_average": common.sh(["uptime"]).strip(),
           "loads": []}
    for rep in (1, 2):
        for arm, extra in ARMS.items():
            mo.VARIANT.update({**READ_VARIANT, "commit_chunks": 16, "reuse_connection": False, "rpc_chunks": 1, **extra})
            handle = mo.start(f"i1-srv-{arm}", "D2")
            mo.create(handle)
            r, _ = run.ingest(arm, handle, m, data)
            if mo._WRITER:
                mo._WRITER[0].close()
            correct = run.full_counts_and_reads("morletdb", handle, m)
            mo.settle(handle)
            rec = {"arm": arm, "rep": rep, "samples_per_s": round(run.total_samples(m) / r["wall_s"]),
                   "correct": bool(correct["counts"]["ok"] and not correct["reads"]["mismatches"]),
                   "server": server_reads(handle, m)}
            out["loads"].append(rec)
            print(rec["arm"], rec["rep"], rec["correct"], {q: v["us_per_read"] for q, v in rec["server"].items()},
                  flush=True)
            common.remove(mo.NAME)
    out["load_average_after"] = common.sh(["uptime"]).strip()
    p = HERE / "results" / f"i1r-server_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(p)


if __name__ == "__main__":
    main()
