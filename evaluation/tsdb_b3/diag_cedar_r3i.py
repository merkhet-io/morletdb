#!/usr/bin/env python3
"""B3.19 round 3, part 9: plan and result side by side for each query form, in one server session (copy volume).
A form is wrong when its result lacks row 1023 or contains the stray row.
Optional: argv[1] volume (default the copy), argv[2] image (default the benchmark's v2026-11-04)."""
import json, re, sys
import common, cedar_adapter as c
BAD = "N15_M07_F04_KA06_1"; T = c.TABLES["high"]; s0, P, n = 1767228120000000000, 15625, 256001
MISS, EXTRA = s0 + 1023 * P, 1767228120197362652
R = "acquisition=%s AND t>=%s AND t<%s"; RP = (BAD, s0, s0 + n * P)
rows = {
 "A t, vibration_1, range, ORDER BY t": (f"SELECT t, vibration_1 FROM {T} WHERE {R} ORDER BY t", RP),
 "B t, 3 channels, range, ORDER BY t (the benchmark)": (f"SELECT t, vibration_1, phase_current_1, phase_current_2 FROM {T} WHERE {R} ORDER BY t", RP),
 "C t, vibration_1, range": (f"SELECT t, vibration_1 FROM {T} WHERE {R}", RP),
 "D t, range": (f"SELECT t FROM {T} WHERE {R}", RP),
 "E t, range, ORDER BY t": (f"SELECT t FROM {T} WHERE {R} ORDER BY t", RP),
 "F t, vibration_1, no range": (f"SELECT t, vibration_1 FROM {T} WHERE acquisition=%s", (BAD,)),
 "G t, vibration_1, t range only": (f"SELECT t, vibration_1 FROM {T} WHERE t>=%s AND t<%s", RP[1:]),
}
aggs = {
 "H 2 filtered counts, range": (f"SELECT count(*) FILTER (WHERE t=%s), count(*) FILTER (WHERE t=%s) FROM {T} WHERE {R}", (MISS, EXTRA) + RP),
 "I 2 filtered counts + count(*), range": (f"SELECT count(*) FILTER (WHERE t=%s), count(*) FILTER (WHERE t=%s), count(*) FROM {T} WHERE {R}", (MISS, EXTRA) + RP),
}
if len(sys.argv) > 2:
    c.IMAGE = sys.argv[2]
conn = c.restart(sys.argv[1] if len(sys.argv) > 1 else "b3-cedardb-r3-copy", "D2")
plan = lambda q, p: " | ".join(re.sub(r"^\W+", "", r[0]).strip() for r in conn.execute("EXPLAIN " + q, p).fetchall()).replace("\n", " | ")
out = {}
for k, (q, p) in rows.items():
    ts = {r[0] for r in conn.execute(q, p).fetchall()}
    out[k] = {"wrong": MISS not in ts or EXTRA in ts, "has_1023": MISS in ts, "has_stray": EXTRA in ts, "plan": plan(q, p)}
for k, (q, p) in aggs.items():
    r = conn.execute(q, p).fetchone()
    out[k] = {"wrong": not (r[0] == 1 and r[1] == 0), "result": list(r), "plan": plan(q, p)}
out["server"] = c.server_settings(conn)
conn.close(); common.stop(c.NAME)
print(json.dumps(out, indent=1, ensure_ascii=False))
