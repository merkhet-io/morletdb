#!/usr/bin/env python3
"""B3.19 round 3, part 5: whose values does the stray row carry? Searches the dataset (no database) for rows with
all three of the stray row's values (from diagnosis/2_rows.json)."""
import json
import numpy as np
import common, workload as wl
stray = json.load(open(common.RESULTS / "b319-cedardb-s3/diagnosis/2_rows.json"))["extra_rows"][0]
out = {"stray_values": stray, "full_matches": []}
for b in common.manifest()["acquisitions"]:
    d = wl.load(wl.ROOT / b["path"])
    msk = np.ones(len(d["vibration_1"]), dtype=bool)
    for ch, v in stray.items():
        msk &= np.asarray(d[ch], dtype=np.float64) == v
    for i in np.nonzero(msk)[0]:
        out["full_matches"].append({"acquisition": b["acquisition"], "row": int(i), "t": int(b["start_ns"] + i * 15625)})
print(json.dumps(out, indent=1))
