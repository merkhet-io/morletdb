#!/usr/bin/env python3
"""B6 storage, codec speed and platform comparison (PROTOCOL.md sections 3, 4A and 4B).

  python3 -I evaluation/storage_b6/measure_b6.py storage WORK OUT.json
  python3 -I evaluation/storage_b6/measure_b6.py speed WORK OUT.json
  python3 -I evaluation/storage_b6/measure_b6.py platforms WORK PLATFORM_DIR OUT.json
  python3 -I evaluation/storage_b6/measure_b6.py record-images PLATFORM_DIR BUILD.json

WORK is prepare_b6.py's directory (inputs.json and the chunk files). Started by run_b6.sh, which sets the budget
(B6_STOP_AT) and, for the harness test only, B6_DRYRUN=1 (the start condition is then recorded but not waited for).

storage (section 3), per input, two programs built --release from the build commit's codec (bin/b6):
- `b6codec storage`: G1 and G21 per chunk, both decoded and compared with the input by `to_bits`, whether G21's bytes
  equal G1's, and each version-5 chunk's path (correction mode, LPC);
- the codec bench example (`rawcodec_bench [--grizzbit21] --per-chunk`): its per-chunk version, kind and bytes must
  equal b6codec's (two programs, one codec).
A round-trip failure stops the measurement (S1, exit 3). G1 must match B4's M-auto (b4_final.json: samples, sha256,
bytes) and B5's G1 (reference/b5_storage.json) on every input those measured; a difference stops B6 (exit 3,
section 3: it is explained from the codec's history before anything else is read). Reference columns, from published
results, not re-run: ClickHouse's best exact arm and Pcodec (B4), Grizzbit 2 (B5's G2).

speed (section 4A): the six waveforms, the six B3 machine channels and the 18 Elf series (no criterion); per input
three passes of `b6codec speed 1` (single thread; encode and decode of G1 and G21, total samples / total time over all
chunks), the start condition (B3.16, as amendment B5.S6r learned) checked before every pass, best of the 3 passes.

platforms (section 4B): compares the per-input sha256 of the concatenated G21 chunk bytes across the platforms
measured by platforms_b6.sh (PLATFORM_DIR/<platform>.jsonl), the Raspberry Pi's included when present.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BIN = Path(os.environ.get("B6_BIN", HERE / "bin/b6"))
BENCH = BIN / "rawcodec_bench"
CODEC = BIN / "b6codec"
B4_FINAL = ROOT / "evaluation/storage_b4/results/b4_final.json"
B5_STORAGE = HERE / "reference/b5_storage.json"
B5_STORAGE_SHA = "6592c0c167fb1d129da9f7ed44f0c98358dc729cb3b84ba826dbe9d487d74a10"
CH_ARMS = ["CH-ALP", "CH-ALP-zstd9", "CH-PCO", "CH-zstd9"]
MODES = {0: "none", 1: "per sample", 2: "table", 3: "exceptions only"}
DRYRUN = os.environ.get("B6_DRYRUN") == "1"
GATE_WAIT_S = int(os.environ.get("B6_GATE_WAIT_S", "1200"))   # up to 20 minutes per pass (amendment B5.S6r)


def run_json(args):
    return json.loads(subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True).stdout)


def sha_file(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def write(out, obj):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n")
    tmp.replace(out)


def manifest(work):
    return json.loads((Path(work) / "inputs.json").read_text())


def over_budget(out, res, name):
    """Section 7: storage and codec speed take at most 2 hours (B6_STOP_AT, epoch seconds, set by the runner)."""
    stop_at = float(os.environ.get("B6_STOP_AT", "inf"))
    if time.time() >= stop_at:
        res["stopped"] = (f"budget: the storage and codec-speed budget ended at "
                          f"{time.strftime('%H:%M:%S', time.localtime(stop_at))}, before {name}")
        write(out, res)
        print(res["stopped"], flush=True)
        raise SystemExit(4)


# ---------------------------------------------------------------- start condition (B3.16, B5.S6r)
def loadavg():
    return float(subprocess.run(["sysctl", "-n", "vm.loadavg"], capture_output=True, text=True).stdout.split()[1])


def busy_processes():
    """Processes other than Docker's VM using more than half a core."""
    out = subprocess.run(["ps", "-Ao", "pcpu,comm", "-r"], capture_output=True, text=True).stdout.splitlines()[1:]
    busy = []
    for line in out:
        cpu, _, comm = line.strip().partition(" ")
        try:
            if float(cpu) > 50 and "Virtualization" not in comm and "com.docker" not in comm:
                busy.append(f"{cpu} {comm.strip()}")
        except ValueError:
            pass
    return busy


def powered():
    b = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    c = subprocess.run(["ioreg", "-r", "-k", "AppleClamshellState", "-d", "1"], capture_output=True, text=True).stdout
    return "AC Power" in b and ('"AppleClamshellState" = No' in c or '"AppleClamshellCausesSleep" = No' in c)


def condition():
    return {"load1": loadavg(), "busy": busy_processes(), "powered": powered(), "at": time.strftime("%H:%M:%S")}


def gate():
    """Waits up to GATE_WAIT_S for the start condition; returns (met, the condition when it was checked last, waited
    seconds). In the harness test (B6_DRYRUN) the condition is recorded but not waited for."""
    t0 = time.time()
    while True:
        c = condition()
        ok = c["load1"] < 3 and not c["busy"] and c["powered"]
        if ok or DRYRUN or time.time() - t0 >= GATE_WAIT_S:
            c["met"] = ok
            return ok or DRYRUN, c, round(time.time() - t0)
        time.sleep(5)


# ---------------------------------------------------------------- storage
def kinds(cs):
    d = {}
    for v, k, *_ in cs:
        d[f"v{v} {k}"] = d.get(f"v{v} {k}", 0) + 1
    return dict(sorted(d.items()))


def references():
    b4 = json.loads(B4_FINAL.read_text())["inputs"]
    if sha_file(B5_STORAGE) != B5_STORAGE_SHA:
        raise SystemExit("reference/b5_storage.json does not match its recorded sha256")
    b5 = {r["input"]: r for r in json.loads(B5_STORAGE.read_text())["inputs"]}
    return b4, b5


def storage_one(row, path, b4, b5):
    name = row["input"]
    c = run_json([CODEC, "storage", path])
    b1 = run_json([BENCH, "--per-chunk", path])
    b2 = run_json([BENCH, "--grizzbit21", "--per-chunk", path])
    g1 = [r[0:3] for r in c["chunks"]]
    g21 = [r[3:6] for r in c["chunks"]]
    agrees = [x[:3] for x in b1["chunks"]] == g1 and [x[:3] for x in b2["chunks"]] == g21 and \
        [x[3:5] for x in b2["chunks"]] == [r[7:9] for r in c["chunks"]] and \
        b1["bytes"] == c["g1_bytes"] and b2["bytes"] == c["g21_bytes"]
    if not agrees:
        raise SystemExit(f"{name}: the bench example and b6codec disagree")
    if not c["exact"] or c["samples"] != row["samples"]:
        raise SystemExit(f"{name}: not exact")
    n = row["samples"]
    changed = [(r[0:3], r[3:6]) for r in c["chunks"] if not r[6]]
    # S3: a changed chunk is a version-5 chunk with strictly fewer bytes; an unchanged one is byte-identical
    s3_bad = [i for i, r in enumerate(c["chunks"])
              if (not r[6] and not (r[3] == 5 and r[5] < r[2])) or (r[6] and r[0:3] != r[3:6])]
    by_kind, paths = {}, {"table": 0, "exceptions only": 0, "per sample": 0, "none": 0, "LPC": 0}
    for a, _ in changed:
        by_kind[f"v{a[0]} {a[1]}"] = by_kind.get(f"v{a[0]} {a[1]}", 0) + 1
    for r in c["chunks"]:
        if r[3] == 5:
            paths[MODES[r[7]]] += 1
            paths["LPC"] += r[8]
    out = {"input": name, "set": row["set"], "samples": n, "sha256": row["sha256"], "chunks": len(g1),
           "chunk_lengths": row["chunk_lengths"], "bytes": {"G1": c["g1_bytes"], "G21": c["g21_bytes"]},
           "bits_per_sample": {"G1": round(8 * c["g1_bytes"] / n, 4), "G21": round(8 * c["g21_bytes"] / n, 4)},
           "G21_over_G1": round(c["g21_bytes"] / c["g1_bytes"], 6),
           "chunk_kinds": {"G1": kinds(g1), "G21": kinds(g21)}, "version5_paths": paths,
           "chunks_changed": len(changed), "changed_by_G1_kind": dict(sorted(by_kind.items())),
           "s3_violations": s3_bad, "exact": True, "bench_example_agrees": agrees}
    ref = b4.get(name)
    if ref is not None:
        arms = ref["arms"]
        ch = {k: arms[k]["bits"] for k in CH_ARMS if arms[k].get("exact")}
        best = min(ch, key=ch.get) if ch else None
        out["b4"] = {"samples": ref["samples"], "sha256_matches": ref["sha256"] == row["sha256"],
                     "samples_match": ref["samples"] == n, "M_auto_bytes": arms["M-auto"]["bytes"],
                     "G1_matches_M_auto": arms["M-auto"]["bytes"] == c["g1_bytes"],
                     "CH_best_arm": best, "CH_best_bits": ch.get(best), "PCO_bits": arms["PCO"]["bits"],
                     "PCO_exact": arms["PCO"]["exact"]}
    r5 = b5.get(name)
    if r5 is not None:
        out["b5"] = {"G1_bytes": r5["bytes"]["G1"], "G1_matches": r5["bytes"]["G1"] == c["g1_bytes"]
                     and r5["sha256"] == row["sha256"], "G2_bytes": r5["bytes"]["G2"],
                     "G2_bits": r5["bits_per_sample"]["G2"]}
    return out


def main_storage(work, out):
    m = manifest(work)
    b4, b5 = references()
    res = {"study": "B6 storage (PROTOCOL.md section 3)", "set": m["set"], "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "chunk": m["chunk"], "quantum": m["quantum"], "inputs_manifest_sha256": sha_file(Path(work) / "inputs.json"),
           "bench": {"path": str(BENCH), "sha256": sha_file(BENCH)},
           "b6codec": {"path": str(CODEC), "sha256": sha_file(CODEC)}, "inputs": []}
    for row in m["inputs"]:
        over_budget(out, res, row["input"])
        t = time.perf_counter()
        path = Path(work) / row["chunk_file"]
        if sha_file(path) != row["chunk_file_sha256"]:
            raise SystemExit(f"{row['input']}: chunk file differs from the manifest")
        r = storage_one(row, path, b4, b5)
        r["seconds"] = round(time.perf_counter() - t, 1)
        res["inputs"].append(r)
        write(out, res)   # after every input, so a stop leaves what was measured
        print(json.dumps({k: r[k] for k in ("input", "set", "bits_per_sample", "chunks_changed")}
                         | {"G1_matches_B4": r.get("b4", {}).get("G1_matches_M_auto"),
                            "G1_matches_B5": r.get("b5", {}).get("G1_matches")}), flush=True)
        # Section 3: G1 must equal B4's and B5's Grizzbit 1 exactly; any difference stops B6 here.
        bad = [k for k, ok in (("B4 input", r.get("b4", {}).get("sha256_matches", True)
                                and r.get("b4", {}).get("samples_match", True)),
                               ("B4 M-auto bytes", r.get("b4", {}).get("G1_matches_M_auto", True)),
                               ("B5 G1 bytes", r.get("b5", {}).get("G1_matches", True))) if not ok]
        if bad or r["s3_violations"]:
            res["stopped"] = f"{r['input']}: " + (f"G1 differs from {', '.join(bad)}" if bad else
                                                  f"S3 violation in chunks {r['s3_violations'][:10]}")
            write(out, res)
            print("STOPPED: " + res["stopped"], flush=True)
            raise SystemExit(3)
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write(out, res)


# ---------------------------------------------------------------- speed
def main_speed(work, out):
    m = manifest(work)
    res = {"study": "B6 codec speed (PROTOCOL.md section 4A)", "set": m["set"], "dryrun": DRYRUN,
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "passes": 3, "threads": 1,
           "b6codec": {"path": str(CODEC), "sha256": sha_file(CODEC)}, "inputs": [], "not_run": []}
    for row in m["inputs"]:
        if row["set"] not in ("waveform", "b3", "elf"):
            continue   # section 4A: the six waveforms, the six B3 channels and the 18 Elf series
        over_budget(out, res, row["input"])
        path = Path(work) / row["chunk_file"]
        passes, conds = [], []
        for p in range(3):
            ok, c, waited = gate()
            c["waited_s"] = waited
            if not ok:
                res["not_run"].append({"input": row["input"], "pass": p + 1, "condition": c})
                break
            r = run_json([CODEC, "speed", 1, path])
            c["load1_after"] = loadavg()
            passes.append(r)
            conds.append(c)
        if len(passes) < 3:
            print(json.dumps({"input": row["input"], "not_run": "start condition not met within "
                              f"{GATE_WAIT_S} s"}), flush=True)
            write(out, res)
            continue
        best = {}
        for f in ("G1", "G21"):
            best[f] = {k: max(x[f][k] for x in passes) for k in ("encode_msamples_s", "decode_msamples_s")}
            best[f]["encode_passes"] = [x[f]["encode_msamples_s"] for x in passes]
            best[f]["decode_passes"] = [x[f]["decode_msamples_s"] for x in passes]
        rec = {"input": row["input"], "set": row["set"], "samples": row["samples"], **best,
               "G21_over_G1_encode": round(best["G21"]["encode_msamples_s"] / best["G1"]["encode_msamples_s"], 4),
               "G21_over_G1_decode": round(best["G21"]["decode_msamples_s"] / best["G1"]["decode_msamples_s"], 4),
               "conditions": conds}
        res["inputs"].append(rec)
        write(out, res)
        print(json.dumps({k: rec[k] for k in ("input", "G21_over_G1_encode", "G21_over_G1_decode")}
                         | {"G21": {k: rec["G21"][k] for k in ("encode_msamples_s", "decode_msamples_s")}}),
              flush=True)
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write(out, res)


# ---------------------------------------------------------------- platforms
def main_platforms(work, pdir, out):
    m = manifest(work)
    names = [r["input"] for r in m["inputs"]]
    plats = {}
    for f in sorted(Path(pdir).glob("*.jsonl")):
        rows = {}
        for line in f.read_text().splitlines():
            if line.strip().startswith("{"):
                r = json.loads(line)
                rows[Path(r["file"]).stem] = r
        plats[f.stem] = rows
    notes = {}
    for f in sorted(Path(pdir).glob("*.status")):
        notes[f.stem] = f.read_text().strip()
    table, differ = [], []
    for n in names:
        hashes = {p: rows[n]["sha256"] for p, rows in plats.items() if n in rows}
        same = len(set(hashes.values())) <= 1
        if not same:
            differ.append(n)
        table.append({"input": n, "set": next(r["set"] for r in m["inputs"] if r["input"] == n),
                      "sha256": hashes, "same": same,
                      "bytes": {p: rows[n]["bytes"] for p, rows in plats.items() if n in rows}})
    res = {"study": "B6 platforms (PROTOCOL.md section 4B)", "set": m["set"],
           "written": time.strftime("%Y-%m-%dT%H:%M:%S"), "platforms": sorted(plats), "platform_status": notes,
           "complete": {p: sorted(set(names) - set(rows)) for p, rows in plats.items()},
           "inputs": table, "differing_inputs": differ,
           "edge_speeds": {p: {n: {k: rows[n][k] for k in ("encode_msamples_s", "decode_msamples_s")}
                               for n in rows} for p, rows in plats.items() if p.startswith("pi")}}
    write(out, res)
    print(json.dumps({"platforms": res["platforms"], "differing_inputs": differ}))


def main_record_images(pdir, build_json):
    """Records the platform images of section 4B (PDIR/<platform>.image) in the build record's `platform_images`."""
    p = Path(build_json)
    b = json.loads(p.read_text()) if p.is_file() else {}
    imgs = b.setdefault("platform_images", {})
    for f in sorted(Path(pdir).glob("*.image")):
        imgs[f.stem] = json.loads(f.read_text())
    imgs["recorded"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write(p, b)
    print(json.dumps({k: v.get("id") if isinstance(v, dict) else v for k, v in imgs.items()}))


if __name__ == "__main__":
    {"storage": main_storage, "speed": main_speed, "platforms": main_platforms,
     "record-images": main_record_images}[sys.argv[1]](*sys.argv[2:])
