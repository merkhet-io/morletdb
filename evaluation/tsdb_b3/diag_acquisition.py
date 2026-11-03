#!/usr/bin/env python3
"""B3.3 attribution (exploratory, development set only): where does morlet_read_acquisition's time go?
Loads the acquisitions the AQ plan uses, registers them, and times each AQ interval (20 x 20 repetitions):
end to end through morlet_read_acquisition and through the client-side raw read; the server alone (the same
calls returning only a sample count); and morlet_time_to_samples alone for the 7 channels."""
import json
import time

import common
import morletdb_adapter as mo
import acquisitions as aq
import run


def main():
    m = common.manifest()
    plan = aq.plan(m)
    ks = sorted({k for p in plan.values() for k, _ in p["pairs"]})
    data, _ = run.preload(m, ks)
    mo.VARIANT.update({"raw_append": True, "pipeline": True})
    conn = mo.start("b3-diag-acq", "D2")
    mo.create(conn)
    for k in ks:
        mo.load_one(m["acquisitions"][k], data[k])
    sub = {**m, "acquisitions": [m["acquisitions"][k] for k in ks]}
    aq.register(conn, sub, mo.COLLECTION)
    mo.settle(conn)
    acq = {a["k"]: a for a in m["acquisitions"]}
    read_acq, read_client = aq.reader_acq(conn, mo.COLLECTION), aq.reader_client(conn, mo.COLLECTION)
    server_acq = "SELECT sum(cardinality(samples)) FROM morlet_read_acquisition(%s::regclass, %s, %s, %s)"
    server_client = ("SELECT sum(length(morlet_read_interval_raw(%s::regclass, r.id, r.s, r.e))) / 8 FROM "
                     "unnest(%s::text[], %s::bigint[], %s::bigint[]) r(id, s, e)")
    mapping = ("SELECT count(*) FROM unnest(%s::float8[]) r(rate), "
               "LATERAL morlet_time_to_samples(r.rate, 0, %s, %s)")
    out = {}
    for name, p in plan.items():
        res = {k: [] for k in ("acq_end_to_end", "client_end_to_end", "acq_server", "client_server", "mapping_7")}
        for k, t0 in p["pairs"]:
            a, t1 = acq[k], t0 + p["window_ns"]
            ids, ss, es = [], [], []
            for c in aq.CHANNELS:
                s, e = aq.sample_range(c, a["channels"][c]["samples"], t0, t1)
                if e > s:
                    ids.append(f"{a['acquisition']}.{c}")
                    ss.append(s)
                    es.append(e)
            calls = {
                "acq_end_to_end": lambda: read_acq(a, t0, t1),
                "client_end_to_end": lambda: read_client(a, t0, t1),
                "acq_server": lambda: conn.execute(server_acq, (mo.COLLECTION, a["acquisition"], t0, t1),
                                                   prepare=True).fetchone(),
                "client_server": lambda: conn.execute(server_client, (mo.COLLECTION, ids, ss, es), prepare=True).fetchone(),
                "mapping_7": lambda: conn.execute(mapping, ([aq.RATE[c] for c in aq.CHANNELS], t0, t1),
                                                  prepare=True).fetchone(),
            }
            for f in calls.values():
                f()
            for _ in range(20):
                for key, f in calls.items():
                    t = time.perf_counter()
                    f()
                    res[key].append(time.perf_counter() - t)
        out[name] = {k: common.pcts(v) for k, v in res.items()}
    conn.close()
    common.remove(mo.NAME, "b3-diag-acq")
    out = {"note": "exploratory attribution on the development set; not a frozen B3 measure", "image": mo.IMAGE, **out}
    (common.RESULTS / "attribution_acquisition_reads.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({n: {k: v["p50_ms"] for k, v in r.items()} for n, r in out.items() if n in plan}, indent=1))


if __name__ == "__main__":
    main()
