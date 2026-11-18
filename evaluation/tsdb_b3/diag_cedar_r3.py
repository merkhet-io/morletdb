#!/usr/bin/env python3
"""Diagnosis of B3.19 round 3 (RESULTS.md): CedarDB's preserved volume b3-cedardb-d2-r1 (queried on a byte-identical copy, b3-cedardb-r3-copy), read-only queries on the
acquisition that failed the read-back check. Not a benchmark; nothing timed."""
import json, sys
import numpy as np
import common, cedar_adapter as c, workload as wl

BAD = "N15_M07_F04_KA06_1"
m = common.manifest()
a = next(x for x in m["acquisitions"] if x["acquisition"] == BAD)
data = wl.load(wl.ROOT / a["path"])
conn = c.restart("b3-cedardb-r3-copy", "D2")
out = {"server": c.server_settings(conn), "counts": c.count(conn)}
for g, spec in wl.GROUPS.items():
    n = a["channels"][spec["channels"][0]]["samples"]
    want_t = a["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]
    r = {"n": n}
    for rep in range(3):   # does the same query return the same thing each time?
        t, v = c.read(conn, a, spec["channels"], 0, n, group=g)
        key = f"read{rep}"
        r[key] = {"rows": int(t.size), "times_ok": bool(t.size == n and np.array_equal(t, want_t))}
        if t.size == n and not r[key]["times_ok"]:
            d = np.nonzero(t != want_t)[0]
            r[key].update(first_bad=int(d[0]), last_bad=int(d[-1]), n_bad=int(d.size),
                          sorted=bool(np.all(np.diff(t) > 0)), unique=int(np.unique(t).size),
                          sample=[[int(i), int(t[i]), int(want_t[i])] for i in d[:5]])
        for ch in spec["channels"]:
            x = np.asarray(data[ch], dtype=np.float64)
            same = v[ch].size == n and np.array_equal(v[ch].view(np.int64), x.view(np.int64))
            if not same and v[ch].size == n:
                d = np.nonzero(v[ch].view(np.int64) != x.view(np.int64))[0]
                # are the wrong values a permutation of the right ones (rows out of order)?
                r[key][ch] = {"n_bad": int(d.size), "first_bad": int(d[0]), "last_bad": int(d[-1]),
                              "same_multiset": bool(np.array_equal(np.sort(v[ch]), np.sort(x)))}
            elif not same:
                r[key][ch] = {"rows": int(v[ch].size)}
    # independent of ORDER BY: aggregate checks
    q = conn.execute(f"SELECT count(*), count(DISTINCT t), min(t), max(t) FROM {c.TABLES[g]} WHERE acquisition = %s",
                     (BAD,)).fetchone()
    r["sql"] = {"count": q[0], "distinct_t": q[1], "min_ok": q[2] == int(want_t[0]), "max_ok": q[3] == int(want_t[-1])}
    out[g] = r
conn.close()
common.stop(c.NAME)
print(json.dumps(out, indent=1, default=str))
