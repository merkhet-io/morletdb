# DEVELOPMENT ONLY (#326 float32 spike): dump development chunks as raw little-endian float32.
# Development set: IU4 runs[5::40] sorted by (machine, tool, run). B7 used runs[::20] (benchmark) and runs[10::20]
# (development); runs[15::20] and the rest of runs[5::20] stay unread for a frozen benchmark set.
import re, sys
from pathlib import Path
import numpy as np
IU4 = Path("/Users/zin/sophia/morletdb/data/denkena2023/npz")
CH = {"force": ["force_sensor_x", "force_sensor_y", "force_sensor_z"],
      "ctrl": ["torque_spindle", "torque_axis_x", "torque_axis_y", "torque_axis_z",
               "position_control_deviation_axis_x", "position_control_deviation_axis_y"]}
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
runs = sorted((int(m[1]), int(m[2]), int(m[3]), p) for p in IU4.glob("M*T*R*.npz")
              if (m := re.match(r"M(\d+)T(\d+)R(\d+)\.npz$", p.name)))
assert len(runs) == 6418
dev = [r[3] for r in runs[5::40]]
for g, chs in CH.items():
    with open(out / f"{g}.f32", "wb") as f, open(out / f"{g}.len", "w") as L:
        for p in dev:
            z = np.load(p)
            for ch in chs:
                a = z[ch]; b = a.astype(np.float32)
                assert np.array_equal(b.astype(np.float64), a, equal_nan=True), (p.name, ch)
                assert np.isfinite(b).all()
                f.write(b.astype("<f4").tobytes()); L.write(f"{b.size}\n")
    print(g, len(dev), "runs")
