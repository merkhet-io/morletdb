#!/usr/bin/env python3
"""B3.19 round 3, part 6: query plans of the correct and the wrong variants (same copy volume)."""
import json
import common, cedar_adapter as c
BAD = "N15_M07_F04_KA06_1"; T = c.TABLES["high"]; s0, P, n = 1767228120000000000, 15625, 256001
MISS = s0 + 1023 * P
conn = c.restart("b3-cedardb-r3-copy", "D2")
V = {"point (correct)": (f"SELECT * FROM {T} WHERE acquisition=%s AND t=%s", (BAD, MISS)),
     "acquisition only (correct)": (f"SELECT t FROM {T} WHERE acquisition=%s", (BAD,)),
     "range rows 0-2047 (correct)": (f"SELECT t FROM {T} WHERE acquisition=%s AND t>=%s AND t<%s", (BAD, s0, s0 + 2048 * P)),
     "full range (wrong)": (f"SELECT t FROM {T} WHERE acquisition=%s AND t>=%s AND t<%s", (BAD, s0, s0 + n * P)),
     "full range, t only (wrong)": (f"SELECT t FROM {T} WHERE t>=%s AND t<%s", (s0, s0 + n * P))}
out = {k: "\n".join(r[0] for r in conn.execute("EXPLAIN " + q, p).fetchall()) for k, (q, p) in V.items()}
# where does the range read go wrong as the range widens? smallest upper bound (in rows) that loses row 1023
lo, hi = 2048, n
def bad(k):
    return conn.execute(f"SELECT count(*) FILTER (WHERE t=%s) FROM {T} WHERE acquisition=%s AND t>=%s AND t<%s",
                        (MISS, BAD, s0, s0 + k * P)).fetchone()[0] == 0
assert bad(hi) and not bad(lo)
while hi - lo > 1:
    mid = (lo + hi) // 2
    lo, hi = (lo, mid) if bad(mid) else (mid, hi)
out["smallest_range_end_row_that_fails"] = hi
out["plan_at_last_correct"] = "\n".join(r[0] for r in conn.execute(f"EXPLAIN SELECT t FROM {T} WHERE acquisition=%s AND t>=%s AND t<%s", (BAD, s0, s0 + lo * P)).fetchall())
out["plan_at_first_wrong"] = "\n".join(r[0] for r in conn.execute(f"EXPLAIN SELECT t FROM {T} WHERE acquisition=%s AND t>=%s AND t<%s", (BAD, s0, s0 + hi * P)).fetchall())
conn.close(); common.stop(c.NAME)
print(json.dumps(out, indent=1, ensure_ascii=False))
