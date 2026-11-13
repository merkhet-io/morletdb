#!/usr/bin/env python3
"""B3.19 round 3, part 8: is the wrong result stable? The same queries repeated within one server session, on the
volume named in argv[1]."""
import json, sys
import numpy as np
import common, cedar_adapter as c, workload as wl
BAD = "N15_M07_F04_KA06_1"; T = c.TABLES["high"]; s0, P, n = 1767228120000000000, 15625, 256001
MISS, EXTRA = s0 + 1023 * P, 1767228120197362652
a = next(x for x in common.manifest()["acquisitions"] if x["acquisition"] == BAD)
conn = c.restart(sys.argv[1], "D2")
out = {"volume": sys.argv[1], "server_count": [], "full_read_has_missing": [], "full_read_has_extra": []}
for i in range(10):
    r = conn.execute(f"SELECT count(*) FILTER (WHERE t=%s), count(*) FILTER (WHERE t=%s) FROM {T} "
                     f"WHERE acquisition=%s AND t>=%s AND t<%s", (MISS, EXTRA, BAD, s0, s0 + n * P)).fetchone()
    out["server_count"].append(list(r))
for i in range(5):
    t, _ = c.read(conn, a, ["vibration_1"], 0, n)
    out["full_read_has_missing"].append(bool(MISS in set(t.tolist())))
    out["full_read_has_extra"].append(bool(EXTRA in set(t.tolist())))
conn.close(); common.stop(c.NAME)
print(json.dumps(out))
