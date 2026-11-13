#!/usr/bin/env python3
"""B3.19 round 3, part 4: the full-range read in text format (not psycopg's binary decoding), and the server's own
count of the two times within the range."""
import json
import common, cedar_adapter as c, workload as wl
BAD = "N15_M07_F04_KA06_1"; T = c.TABLES["high"]
a = next(x for x in common.manifest()["acquisitions"] if x["acquisition"] == BAD)
s0, P, n = a["start_ns"], 15625, 256001; MISS, EXTRA = s0 + 1023 * P, 1767228120197362652
conn = c.restart("b3-cedardb-r3-copy", "D2")
with conn.cursor(binary=False) as cur:
    cur.execute(f"SELECT t::text FROM {T} WHERE acquisition=%s AND t>=%s AND t<%s ORDER BY t", (BAD, s0, s0 + n * P))
    ts = [int(r[0]) for r in cur.fetchall()]
srv = conn.execute(f"SELECT count(*) FILTER (WHERE t=%s), count(*) FILTER (WHERE t=%s), count(*) FROM {T} "
                   f"WHERE acquisition=%s AND t>=%s AND t<%s", (MISS, EXTRA, BAD, s0, s0 + n * P)).fetchone()
print(json.dumps({"text_rows": len(ts), "text_has_missing": MISS in ts, "text_has_extra": EXTRA in ts,
                  "server_side_counts_in_range": {"missing_t": srv[0], "extra_t": srv[1], "all": srv[2]}}, indent=1))
conn.close(); common.stop(c.NAME)
