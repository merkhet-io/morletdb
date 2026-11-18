#!/usr/bin/env python3
"""B3.19 round 3, part 3: is the one wrong row a stored-data or a query-result fault? Variants of the read."""
import json
import numpy as np
import common, cedar_adapter as c, workload as wl
BAD = "N15_M07_F04_KA06_1"; T = c.TABLES["high"]
m = common.manifest(); a = next(x for x in m["acquisitions"] if x["acquisition"] == BAD)
data = wl.load(wl.ROOT / a["path"]); P = 15625; s0 = a["start_ns"]; n = 256001
MISS, EXTRA = s0 + 1023 * P, 1767228120197362652
sent = {ch: float(data[ch][1023]) for ch in ("vibration_1", "phase_current_1", "phase_current_2")}
conn = c.restart("b3-cedardb-r3-copy", "D2")
q = lambda sql, *p: conn.execute(sql, p).fetchall()
out = {"point_missing_row": q(f"SELECT t, vibration_1, phase_current_1, phase_current_2 FROM {T} WHERE acquisition=%s AND t=%s", BAD, MISS),
       "sent_missing_row": sent}
def has(rows): ts = [r[0] for r in rows]; return {"rows": len(rows), "has_missing": MISS in ts, "has_extra": EXTRA in ts}
base = f"SELECT t, vibration_1 FROM {T} WHERE acquisition=%s"
out["variants"] = {
 "full_range_order": has(q(base + " AND t>=%s AND t<%s ORDER BY t", BAD, s0, s0 + n * P)),
 "full_range_no_order": has(q(base + " AND t>=%s AND t<%s", BAD, s0, s0 + n * P)),
 "acq_only_no_range": has(q(base, BAD)),
 "small_range_0_2048": has(q(base + " AND t>=%s AND t<%s ORDER BY t", BAD, s0, s0 + 2048 * P)),
 "range_1000_1100": has(q(base + " AND t>=%s AND t<%s ORDER BY t", BAD, s0 + 1000 * P, s0 + 1100 * P)),
 "range_12600_12700": has(q(base + " AND t>=%s AND t<%s ORDER BY t", BAD, s0 + 12600 * P, s0 + 12700 * P)),
 "t_only_no_acq": has(q(f"SELECT t, vibration_1 FROM {T} WHERE t>=%s AND t<%s", s0, s0 + n * P)),
}
out["extra_point"] = q(f"SELECT acquisition, t FROM {T} WHERE t=%s", EXTRA)
out["extra_t_minus_missing_t"] = EXTRA - MISS
out["xor_bits"] = bin(EXTRA ^ MISS)
x = np.float64(out["point_missing_row"][0][1]) if out["point_missing_row"] else None
out["point_row_matches_sent"] = bool(out["point_missing_row"]) and all(
    np.float64(v).view(np.int64) == np.float64(sent[k]).view(np.int64)
    for v, k in zip(out["point_missing_row"][0][1:], sent))
out["explain"] = [r[0] for r in q("EXPLAIN " + base + " AND t>=%s AND t<%s ORDER BY t", BAD, s0, s0 + n * P)]
conn.close(); common.stop(c.NAME)
print(json.dumps(out, indent=1, default=str))
