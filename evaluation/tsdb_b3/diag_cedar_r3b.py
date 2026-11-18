#!/usr/bin/env python3
"""B3.19 round 3, part 2: which stored rows differ from what was sent (same copy volume)."""
import json
import numpy as np
import common, cedar_adapter as c, workload as wl
BAD = "N15_M07_F04_KA06_1"
m = common.manifest(); a = next(x for x in m["acquisitions"] if x["acquisition"] == BAD)
data = wl.load(wl.ROOT / a["path"]); spec = wl.GROUPS["high"]; chans = spec["channels"]
n = a["channels"][chans[0]]["samples"]; P = spec["period_ns"]
want_t = a["start_ns"] + np.arange(n, dtype=np.int64) * P
conn = c.restart("b3-cedardb-r3-copy", "D2")
t, v = c.read(conn, a, chans, 0, n, group="high")
missing = sorted(set(want_t.tolist()) - set(t.tolist())); extra = sorted(set(t.tolist()) - set(want_t.tolist()))
out = {"period_ns": P, "channels": chans, "missing": [[x, (x - a["start_ns"]) / P] for x in missing],
       "extra": [[x, (x - a["start_ns"]) / P] for x in extra]}
# for rows whose time is right, are the values right?
pos = {x: i for i, x in enumerate(want_t.tolist())}
idx = np.array([pos.get(x, -1) for x in t.tolist()]); ok = idx >= 0
wrong = {}
for ch in chans:
    x = np.asarray(data[ch], dtype=np.float64)
    good = v[ch][ok].view(np.int64) == x[idx[ok]].view(np.int64)
    wrong[ch] = int((~good).sum())
out["value_mismatches_on_matching_times"] = wrong
# the extra row's values: do they equal the missing row's sent values (only the time changed)?
for e in extra:
    i = t.tolist().index(e)
    out.setdefault("extra_rows", []).append({ch: float(v[ch][i]) for ch in chans})
for mm in missing:
    out.setdefault("missing_rows_sent", []).append({ch: float(data[ch][pos[mm]]) for ch in chans})
# other acquisitions' tables: does the extra time belong to another acquisition, or appear twice?
for e in extra + missing:
    out.setdefault("rows_with_time_any_acq", {})[str(e)] = conn.execute(
        f"SELECT acquisition, count(*) FROM {c.TABLES['high']} WHERE t = %s GROUP BY acquisition", (e,)).fetchall()
conn.close(); common.stop(c.NAME)
print(json.dumps(out, indent=1, default=str))
