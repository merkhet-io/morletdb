#!/usr/bin/env python3
"""B3 attribution (exploratory, not a frozen measure): where does MorletDB's R2 read time go?
Loads only the acquisitions the R2 plan uses, then times each R2 interval three ways (20 x 20 repetitions):
end to end as in the frozen measure; server only (the same reconstruction, returning only a count); and the
client-side decode alone (binary float8[] -> Python floats -> numpy), on arrays already fetched."""
import json
import time

import numpy as np

import common
import morletdb_adapter as mo
import run


def main():
    m = common.manifest()
    plan = common.read_plan(m)["R2"]
    ks = sorted({k for k, _ in plan["pairs"]})
    data, _ = run.preload(m, ks)
    mo.VARIANT["rpc_chunks"] = 16
    conn = mo.start("b3-diag", "D2")
    mo.create(conn)
    for k in ks:
        mo.load_one(m["acquisitions"][k], data[k])
    acq = {a["k"]: a for a in m["acquisitions"]}
    n = plan["length"]
    server_q = ("SELECT sum(cardinality(samples)) FROM generate_series(%s::bigint, %s::bigint - 1, %s) a, "
                "LATERAL morlet_read_interval(%s::regclass, %s, a, least(a + %s, %s::bigint))")
    fetch_q = ("SELECT samples FROM generate_series(%s::bigint, %s::bigint - 1, %s) a, "
               "LATERAL morlet_read_interval(%s::regclass, %s, a, least(a + %s, %s::bigint)) ORDER BY a")
    e2e, server, fetch_only, decode = [], [], [], []
    for k, off in plan["pairs"]:
        args = (off, off + n, mo.MAX_READ, mo.COLLECTION, mo.rid(acq[k], "vibration_1"), mo.MAX_READ, off + n)
        mo.read(conn, acq[k], ["vibration_1"], off, n)
        conn.execute(server_q, args).fetchone()
        for _ in range(20):
            t = time.perf_counter()
            mo.read(conn, acq[k], ["vibration_1"], off, n)
            e2e.append(time.perf_counter() - t)
            t = time.perf_counter()
            assert conn.execute(server_q, args).fetchone()[0] == n
            server.append(time.perf_counter() - t)
            t = time.perf_counter()
            cur = conn.cursor(binary=True)
            cur.execute(fetch_q, args)
            rows = cur.fetchall()
            fetch_only.append(time.perf_counter() - t)
            t = time.perf_counter()
            np.concatenate([np.asarray(r[0], dtype=np.float64) for r in rows])
            decode.append(time.perf_counter() - t)
    out = {"note": "exploratory attribution; not a frozen B3 measure", "acquisitions_loaded": len(ks),
           "end_to_end": common.pcts(e2e), "server_reconstruct_count_only": common.pcts(server),
           "fetch_including_psycopg_array_decode": common.pcts(fetch_only), "numpy_from_lists": common.pcts(decode)}
    conn.close()
    common.remove(mo.NAME, "b3-diag")
    (common.RESULTS / "attribution_reads_morletdb.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
