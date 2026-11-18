#!/usr/bin/env python3
"""B3.19 round 3, part 7: which range bounds make the read lose row 1023? (server-side count, same copy volume)"""
import json
import common, cedar_adapter as c
BAD = "N15_M07_F04_KA06_1"; T = c.TABLES["high"]; s0, P, n = 1767228120000000000, 15625, 256001
MISS, LAST = s0 + 1023 * P, s0 + (n - 1) * P
conn = c.restart("b3-cedardb-r3-copy", "D2")
def probe(where, params):
    r = conn.execute(f"SELECT count(*) FILTER (WHERE t=%s), count(*) FROM {T} WHERE acquisition=%s AND {where}",
                     (MISS, BAD, *params)).fetchone()
    return {"row_1023_present": r[0] == 1, "rows": r[1]}
cases = {
 "t >= first AND t < last+1 period (the benchmark's read)": ("t >= %s AND t < %s", (s0, s0 + n * P)),
 "t >= first AND t <= last": ("t >= %s AND t <= %s", (s0, LAST)),
 "t >= first AND t < last (excludes last row)": ("t >= %s AND t < %s", (s0, LAST)),
 "t >= first only": ("t >= %s", (s0,)),
 "t <= last only": ("t <= %s", (LAST,)),
 "t < last only": ("t < %s", (LAST,)),
 "t > first (excludes first row) AND t <= last": ("t > %s AND t <= %s", (s0, LAST)),
 "t >= first-1s AND t <= last+1s (wider than the acquisition)": ("t >= %s AND t <= %s", (s0 - 10**9, LAST + 10**9)),
 "t >= row 1000 AND t <= last": ("t >= %s AND t <= %s", (s0 + 1000 * P, LAST)),
 "t >= row 1024 AND t <= last (excludes row 1023)": ("t >= %s AND t <= %s", (s0 + 1024 * P, LAST)),
 "t BETWEEN first AND last": ("t BETWEEN %s AND %s", (s0, LAST)),
 "no t predicate": ("TRUE", ()),
}
out = {k: probe(w, p) for k, (w, p) in cases.items()}
conn.close(); common.stop(c.NAME)
print(json.dumps(out, indent=1))
