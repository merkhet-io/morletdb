# Post-run diagnostic (not a measurement): which mdb-l force series is not bit-exact, and how.
import sys, numpy as np
sys.path.insert(0, '.')
import b7, workload as wl
man = b7.manifest()
data = {"force": b7.group_units("force", man)}
neg0 = sum(int(np.count_nonzero((x[ch] == 0) & np.signbit(x[ch]))) for _, x, _ in data["force"].values() for ch in x)
print("negative zeros in force input:", neg0, flush=True)
s = b7.Modelar("mdb-l"); s.start(); s.client = s._wait()
s.load_group("force", data["force"]); s.settle()
spec = wl.GROUPS["force"]
for uid, x, start_ns in data["force"].values():
    n = len(x[spec["channels"][0]])
    for ch in spec["channels"]:
        ref = x[ch]; s_us = start_ns // 1000
        ts, got = s.read("force", uid, ch, s_us, s_us + 10**12)
        exp_ts = (start_ns + np.arange(n, dtype=np.int64) * spec["period_ns"] + 500) // 1000
        if got.size != n or not np.array_equal(ts, exp_ts):
            print(uid, ch, "size/timestamps", got.size, n); continue
        gb = got.astype(np.float32).view(np.uint32); rb = ref.view(np.uint32)
        bad = np.nonzero(gb != rb)[0]
        if bad.size:
            print(uid, ch, "differs at", bad.size, "positions; first", bad[:5].tolist(),
                  [(hex(rb[i]), hex(gb[i]), float(ref[i]), float(got[i])) for i in bad[:5]],
                  "value-equal:", bool(np.all((ref[bad] == got[bad].astype(np.float32)) | (np.isnan(ref[bad]) & np.isnan(got[bad])))))
s.remove()
