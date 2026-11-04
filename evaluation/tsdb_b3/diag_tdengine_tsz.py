import sys, json, numpy as np
sys.path.insert(0, '.')
import common, workload as wl, tdengine_adapter as c
m = common.manifest(); acq = {a["acquisition"]: a for a in m["acquisitions"]}
conn = c.connect()
out = {}
for name in ["N09_M07_F10_KA05_1", "N09_M07_F10_KA06_1"]:
    a = acq[name]; src = wl.load(wl.ROOT / a["path"])["vibration_1"].astype(np.float64)
    n = len(src); _, v = c.read(conn, a, ["vibration_1"], 0, n); got = v["vibration_1"]
    bad = np.flatnonzero(src.view(np.int64) != got.view(np.int64))
    d = np.abs(got[bad] - src[bad]); rel = d / np.maximum(np.abs(src[bad]), 1e-300)
    ulp = np.abs(got[bad].view(np.int64) - src[bad].view(np.int64))
    out[name] = {"samples": int(n), "differing": int(bad.size), "first_index": int(bad[0]) if bad.size else None,
                 "max_abs_diff": float(d.max()) if bad.size else 0, "max_rel_diff": float(rel.max()) if bad.size else 0,
                 "max_ulp": int(ulp.max()) if bad.size else 0, "examples": [(int(i), repr(float(src[i])), repr(float(got[i]))) for i in bad[:5]],
                 "nan_or_inf_in_source": bool(~np.isfinite(src).all()), "source_range": [float(src.min()), float(src.max())]}
conn.close()
print(json.dumps(out, indent=1))
