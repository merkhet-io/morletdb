#!/usr/bin/env python3
"""Study B8 harness (PROTOCOL.md sections 3 and 4). Local and unpublished (IU4 data).

  .venv-b4/bin/python -I evaluation/storage_b8/b8.py prepare SET WORK     SET: dev | bench
  .venv-b4/bin/python -I evaluation/storage_b8/b8.py storage WORK OUT     4.A (and S5 inputs under WORK/b6 if present)
  .venv-b4/bin/python -I evaluation/storage_b8/b8.py speed WORK OUT       4.C, start condition before every pass
  .venv-b4/bin/python -I evaluation/storage_b8/b8.py pcodec WORK OUT      4.E reference, no criterion

prepare writes WORK/inputs/<group>.bin (the codec bench example's chunk file: per stored chunk u32 LE sample count,
f64 LE quantum 1.0, the samples as f64 LE; every run's channel starts a new chunk, 4,096-sample chunks) per group
(force, ctrl) and WORK/manifest.json: the run list, every source file's SHA-256, per channel and run the sample count
and the SHA-256 of the float32 values, and per input file its SHA-256. Every channel must be float32-exact.

Sets (sorted by machine, tool, run over the 6,418 IU4 runs): dev = runs[5::40] (the development set, read before the
freeze); bench = runs[15::20] (the benchmark set, read only after the build record). B7 read runs[::20] and
runs[10::20].
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = Path(os.environ.get("MORLET_DATA", "/Users/zin/sophia/morletdb/data"))
IU4 = DATA / "denkena2023/npz"
CODEC = HERE / "b8codec/target/release/b8codec"
CHUNK = 4096
GROUPS = {
    "force": ["force_sensor_x", "force_sensor_y", "force_sensor_z"],
    "ctrl": ["torque_spindle", "torque_axis_x", "torque_axis_y", "torque_axis_z",
             "position_control_deviation_axis_x", "position_control_deviation_axis_y"],
}
GATE_WAIT_S = int(os.environ.get("B8_GATE_WAIT_S", "1800"))
DRYRUN = os.environ.get("B8_DRYRUN") == "1"


def runs(which):
    found = []
    for p in IU4.glob("M*T*R*.npz"):
        m = re.match(r"M(\d+)T(\d+)R(\d+)\.npz$", p.name)
        found.append((int(m[1]), int(m[2]), int(m[3]), p))
    found.sort()
    if len(found) != 6418:
        raise SystemExit(f"expected 6,418 IU4 runs, found {len(found)}")
    sel = {"dev": found[5::40], "bench": found[15::20]}[which]
    return [r[3] for r in sel]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def prepare(which, work):
    work = Path(work)
    (work / "inputs").mkdir(parents=True, exist_ok=True)
    rs = runs(which)
    man = {"study": "B8", "set": which, "selection": {"dev": "runs[5::40]", "bench": "runs[15::20]"}[which],
           "runs": [p.name for p in rs], "sources": {}, "channels": {}, "inputs": {}}
    files = {g: open(work / "inputs" / f"{g}.bin", "wb") for g in GROUPS}
    quantum = np.float64(1.0).tobytes()
    for p in rs:
        man["sources"][p.name] = sha_file(p)
        z = np.load(p)
        for g, chs in GROUPS.items():
            for ch in chs:
                a = z[ch]
                f = a.astype(np.float32)
                if not np.array_equal(f.astype(np.float64), a, equal_nan=True) or not np.isfinite(f).all():
                    raise SystemExit(f"{p.name} {ch}: not finite float32-exact")
                man["channels"][f"{p.stem}/{ch}"] = {"samples": int(f.size), "sha256_float32": sha(f.astype("<f4").tobytes())}
                x = f.astype("<f8")
                for s in range(0, x.size, CHUNK):
                    c = x[s:s + CHUNK]
                    files[g].write(np.uint32(c.size).astype("<u4").tobytes() + quantum + c.tobytes())
    for g, f in files.items():
        f.close()
        path = work / "inputs" / f"{g}.bin"
        man["inputs"][g] = {"chunk_file": f"inputs/{g}.bin", "sha256": sha_file(path),
                            "samples": sum(v["samples"] for k, v in man["channels"].items() if k.split("/")[1] in GROUPS[g])}
    (work / "manifest.json").write_text(json.dumps(man, indent=1) + "\n")
    print(json.dumps({g: v["samples"] for g, v in man["inputs"].items()} | {"runs": len(rs)}))


def run_json(args):
    out = subprocess.run([str(a) for a in args], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def inputs(work):
    """(name, set, chunk file): the IU4 groups, then B6's inputs if WORK/b6/inputs.json exists (S5)."""
    work = Path(work)
    man = json.loads((work / "manifest.json").read_text())
    for g, v in man["inputs"].items():
        p = work / v["chunk_file"]
        if sha_file(p) != v["sha256"]:
            raise SystemExit(f"{g}: chunk file differs from the manifest")
        yield g, "iu4-" + man["set"], p
    b6 = work / "b6" / "inputs.json"
    if b6.exists():
        for row in json.loads(b6.read_text())["inputs"]:
            yield row["input"], "b6-" + row["set"], work / "b6" / row["chunk_file"]


def storage(work, out):
    res = {"study": "B8 storage (PROTOCOL.md 4.A)", "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "codec_sha256": sha_file(CODEC), "inputs": []}
    for name, s, path in inputs(work):
        r = run_json([CODEC, "storage", path])
        cs = r.pop("chunks")
        n = r["samples"]
        rec = {"input": name, "set": s, "samples": n, "exact": r["exact"], "keep_smaller": r["keep_smaller"],
               "chunks": len(cs)}
        for arm in ("g1", "g16", "g21", "g216"):
            rec[f"{arm}_bytes"] = r[f"{arm}_bytes"]
            rec[f"{arm}_bytes_per_sample"] = round(r[f"{arm}_bytes"] / n, 4)
        rec["v6_grid_chunks"] = sum(1 for c in cs if c[4] == 1)
        rec["v6_signmag_chunks"] = sum(1 for c in cs if c[4] == 3)
        rec["g216_v6_chunks"] = sum(1 for c in cs if c[7] == 6)
        rec["g16_larger_chunks"] = sum(1 for c in cs if c[3] > c[1])
        rec["g216_larger_chunks"] = sum(1 for c in cs if c[8] > c[6])
        res["inputs"].append(rec)
        Path(out).write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps({k: rec[k] for k in ("input", "g1_bytes_per_sample", "g16_bytes_per_sample", "keep_smaller")}),
              flush=True)
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    Path(out).write_text(json.dumps(res, indent=1) + "\n")


def loadavg():
    return float(subprocess.run(["sysctl", "-n", "vm.loadavg"], capture_output=True, text=True).stdout.split()[1])


def busy():
    out = subprocess.run(["ps", "-Ao", "pcpu,comm", "-r"], capture_output=True, text=True).stdout.splitlines()[1:]
    b = []
    for line in out:
        cpu, _, comm = line.strip().partition(" ")
        try:
            if float(cpu) > 50 and "Virtualization" not in comm and "com.docker" not in comm:
                b.append(f"{cpu} {comm.strip()}")
        except ValueError:
            pass
    return b


def gate():
    t0 = time.time()
    while True:
        c = {"load1": loadavg(), "busy": busy(), "at": time.strftime("%H:%M:%S")}
        met = c["load1"] < 3 and not c["busy"]
        if met or DRYRUN or time.time() - t0 >= GATE_WAIT_S:
            c["met"], c["waited_s"] = met, round(time.time() - t0)
            return met or DRYRUN, c
        time.sleep(5)


def speed(work, out):
    res = {"study": "B8 speed (PROTOCOL.md 4.C)", "dryrun": DRYRUN, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "codec_sha256": sha_file(CODEC), "passes": 3, "threads": 1, "inputs": [], "not_run": []}
    for name, s, path in inputs(work):
        if s.startswith("b6-") and s not in ("b6-waveform", "b6-b3"):
            continue   # 4.C: force, ctrl, B6's six waveforms and six B3 machine channels
        passes, conds = [], []
        for p in range(3):
            ok, c = gate()
            if not ok:
                res["not_run"].append({"input": name, "pass": p + 1, "condition": c})
                break
            passes.append(run_json([CODEC, "speed", 1, path]))
            c["load1_after"] = loadavg()
            conds.append(c)
        if len(passes) < 3:
            continue
        rec = {"input": name, "set": s, "samples": passes[0]["samples"], "conditions": conds}
        for arm in ("G1", "G16"):
            rec[arm] = {k: max(x[arm][k] for x in passes) for k in ("encode_msamples_s", "decode_msamples_s")}
            rec[arm]["encode_passes"] = [x[arm]["encode_msamples_s"] for x in passes]
            rec[arm]["decode_passes"] = [x[arm]["decode_msamples_s"] for x in passes]
        res["inputs"].append(rec)
        Path(out).write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps({"input": name, "G16": {k: rec["G16"][k] for k in ("encode_msamples_s", "decode_msamples_s")}}),
              flush=True)
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    Path(out).write_text(json.dumps(res, indent=1) + "\n")


def pcodec_ref(work, out):
    import pcodec
    import pcodec.standalone as pcs
    res = {"study": "B8 pcodec reference (PROTOCOL.md 4.E, no criterion)", "pcodec": getattr(pcodec, "__version__", "1.0.4"),
           "inputs": []}
    for name, s, path in inputs(work):
        if not s.startswith("iu4-"):
            continue
        b = Path(path).read_bytes()
        pos, n, total = 0, 0, 0
        while pos < len(b):
            k = int(np.frombuffer(b, "<u4", 1, pos)[0])
            x = np.frombuffer(b, "<f8", k, pos + 12).astype(np.float32)
            pos += 12 + 8 * k
            total += len(pcs.simple_compress(np.ascontiguousarray(x), pcodec.ChunkConfig()))
            n += k
        res["inputs"].append({"input": name, "samples": n, "bytes": total, "bytes_per_sample": round(total / n, 4)})
    Path(out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res["inputs"]))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "prepare":
        prepare(sys.argv[2], sys.argv[3])
    elif cmd == "storage":
        storage(sys.argv[2], sys.argv[3])
    elif cmd == "speed":
        speed(sys.argv[2], sys.argv[3])
    elif cmd == "pcodec":
        pcodec_ref(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__)
