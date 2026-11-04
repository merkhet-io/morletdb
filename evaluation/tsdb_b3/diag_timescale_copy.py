import sys, time, struct, numpy as np
sys.path.insert(0, '.')
import common, workload as wl, timescale_adapter as c
m = common.manifest(); ks = list(range(12))
data = {k: wl.load(wl.ROOT / m["acquisitions"][k]["path"]) for k in ks}
conn = c.start("b3-ts-copyprobe", "D2"); c.create(conn)
def binary_copy(acq, d, g):
    spec = wl.GROUPS[g]; chans = spec["channels"]; n = len(d[chans[0]])
    name = acq["acquisition"].encode()
    nf = 2 + len(chans)
    dt = np.dtype([("nf", ">i2"), ("la", ">i4"), ("a", f"S{len(name)}"), ("lt", ">i4"), ("t", ">i8")] +
                  sum([[(f"l{i}", ">i4"), (f"v{i}", ">f8")] for i in range(len(chans))], []))
    rec = np.empty(n, dtype=dt); rec["nf"] = nf; rec["la"] = len(name); rec["a"] = name; rec["lt"] = 8
    rec["t"] = acq["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]
    for i, ch in enumerate(chans): rec[f"l{i}"] = 8; rec[f"v{i}"] = d[ch]
    return b"PGCOPY\n\xff\r\n\x00" + struct.pack(">ii", 0, 0) + rec.tobytes() + struct.pack(">h", -1)
for label, ks_ in [("write_row", ks[:6]), ("numpy_binary", ks[6:])]:
    t0 = time.perf_counter(); n = 0
    for k in ks_:
        a = m["acquisitions"][k]
        if label == "write_row":
            c.load_one(conn, a, data[k])
        else:
            for g, spec in wl.GROUPS.items():
                buf = binary_copy(a, data[k], g)
                with conn.cursor() as cur:
                    with cur.copy(f"COPY {c.TABLES[g]} (acquisition, t, {', '.join(spec['channels'])}) FROM STDIN (FORMAT BINARY)") as cp:
                        cp.write(buf)
        n += sum(len(v) for v in data[k].values())
    print(label, round(n / (time.perf_counter() - t0)), "samples/s", flush=True)
# exactness of the numpy path
a = m["acquisitions"][6]; tms, v = c.read(conn, a, ["vibration_1"], 0, len(data[6]["vibration_1"]))
print("numpy path exact:", np.array_equal(v["vibration_1"].view(np.int64), data[6]["vibration_1"].view(np.int64)))
conn.close(); common.remove(c.NAME, "b3-ts-copyprobe")
