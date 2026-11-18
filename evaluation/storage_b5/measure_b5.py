#!/usr/bin/env python3
"""B5 storage and codec-speed measurements (PROTOCOL.md sections 3 and 4A).

  MORLET_DATA=/path/to/data .venv-b4/bin/python -I evaluation/storage_b5/measure_b5.py storage OUT.json [SET]
  MORLET_DATA=/path/to/data .venv-b4/bin/python -I evaluation/storage_b5/measure_b5.py speed OUT.json [SET]
  .venv-b4/bin/python -I evaluation/storage_b5/measure_b5.py build OUT.json

  SET: benchmark (default: B4's six waveforms, the 18 Elf benchmark series, and the four development series,
       reported apart) or dev (the four development Elf series only: the harness test before any benchmark input
       is read). `storage` and `speed` on the benchmark set are started by run_b5_storage.sh, which enforces the
       start condition of section 4A.

Inputs are parsed by B4's loaders (evaluation/storage_b4/measure_b4.py `load_wave`, `load_elf`), unchanged; only
their data directory is pointed at MORLET_DATA (a checkout's data/). Chunking is B4's M-auto: the waveforms' stored
chunk lengths, 4,096-sample chunks for the Elf series, quantum 1 for every chunk.

Per input, two programs built --release from the build commit's codec:
- the codec bench example (`rawcodec_bench [--grizzbit2] --per-chunk`, B5_BENCH), G1 and G2: per-chunk version,
  kind and bytes; it decodes every chunk and compares it by `to_bits`;
- `b5codec storage` (evaluation/storage_b5/b5codec, the grizzbit crate of this branch, B5_CODEC): both arms per
  chunk, both decoded and compared by `to_bits`, and whether G2's bytes equal G1's (S4 needs byte identity, which
  the bench example does not report). Its per-chunk version, kind and bytes must equal the bench example's.
A round-trip failure in either program stops the measurement (S1).

Reported per input: bits per sample (8 x encoded chunk bytes / samples, frame and checksum included), G2 / ClickHouse
best, G2 / Pcodec (references from B4's b4_final.json, not re-run), chunk counts per kind for G1 and G2, chunks that
differ between G1 and G2 (with G1's kind), S4 violations, the chunk lengths stored, and G1 against B4's M-auto
(bytes, and the input's samples and sha256 against B4's).

`speed`: `b5codec speed 3` per input, single thread: encode and decode of G1 and G2, total samples / total time over
all chunks, best of 3 passes. The 1-minute load average is recorded before and after each input.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "evaluation/storage_b4"))
import measure_b4 as b4  # noqa: E402  (B4's loaders, unchanged)

b4.DATA = Path(os.environ.get("MORLET_DATA", ROOT / "data"))
BENCH = Path(os.environ.get("B5_BENCH", ROOT / "target/release/examples/rawcodec_bench"))
CODEC = Path(os.environ.get("B5_CODEC", HERE / "b5codec/target/release/b5codec"))
B4_FINAL = ROOT / "evaluation/storage_b4/results/b4_final.json"
ELF_DEV = ["Blockchain-tr", "POI-lat", "Stocks-USA", "electric_vehicle_charging"]
ELF_BENCH = ["Air-pressure", "Air-sensor", "Basel-temp", "Basel-wind", "Bird-migration", "Bitcoin-price",
             "City-lat", "City-lon", "City-temp", "Dew-point-temp", "Food-price", "IR-bio-temp",
             "PM10-dust", "POI-lon", "SSD-bench", "Stocks-DE", "Stocks-UK", "Wind-Speed"]
LARGE = ["SSD-bench", "Bird-migration", "Basel-temp", "Basel-wind", "Food-price"]   # S2
CH_ARMS = ["CH-ALP", "CH-ALP-zstd9", "CH-PCO", "CH-zstd9"]


def inputs(which):
    """(name, set, loader) in measurement order; names are fixed here (B4's dev_names() is not called)."""
    out = []
    if which == "benchmark":
        out += [(w, "waveform", lambda w=w: b4.load_wave(w)) for w in b4.WAVE]
        out += [(e, "elf", lambda e=e: b4.load_elf(e)) for e in ELF_BENCH]
    out += [(e, "elf-dev", lambda e=e: b4.load_elf(e)) for e in ELF_DEV]
    return out


def chunk_file(x, lengths, path):
    """The bench example's input: B4's chunk lengths, quantum 1 (as B4's M-auto)."""
    with open(path, "wb") as f:
        for ch in np.split(x, np.cumsum(lengths)[:-1]):
            f.write(np.uint32(len(ch)).tobytes() + np.float64(1.0).tobytes() + ch.tobytes())


def run_json(args):
    return json.loads(subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True).stdout)


def kinds(cs):
    d = {}
    for v, k, _ in cs:
        d[f"v{v} {k}"] = d.get(f"v{v} {k}", 0) + 1
    return dict(sorted(d.items()))


def storage_one(name, which, x, lengths, path, ref):
    chunk_file(x, lengths, path)
    c = run_json([CODEC, "storage", path])
    b1 = run_json([BENCH, "--per-chunk", path])
    b2 = run_json([BENCH, "--grizzbit2", "--per-chunk", path])
    g1 = [r[0:3] for r in c["chunks"]]
    g2 = [r[3:6] for r in c["chunks"]]
    bench_agrees = b1["chunks"] == g1 and b2["chunks"] == g2 and b1["bytes"] == c["g1_bytes"] and \
        b2["bytes"] == c["g2_bytes"]
    if not bench_agrees:
        raise SystemExit(f"{name}: the bench example and b5codec disagree")
    n = int(x.size)
    changed = [(r[0:3], r[3:6]) for r in c["chunks"] if not r[6]]
    # S4: a changed chunk is a version-4 chunk with strictly fewer bytes; an unchanged one is byte-identical
    s4_bad = [i for i, r in enumerate(c["chunks"])
              if (not r[6] and not (r[3] == 4 and r[5] < r[2])) or (r[6] and r[0:3] != r[3:6])]
    by_kind = {}
    for a, _ in changed:
        by_kind[f"v{a[0]} {a[1]}"] = by_kind.get(f"v{a[0]} {a[1]}", 0) + 1
    sizes = {}
    for ln in lengths:
        sizes[int(ln)] = sizes.get(int(ln), 0) + 1
    row = {"input": name, "set": which, "samples": n, "sha256": hashlib.sha256(x.tobytes()).hexdigest(),
           "chunks": len(g1), "chunk_lengths": {str(k): v for k, v in sorted(sizes.items())},
           "bytes": {"G1": c["g1_bytes"], "G2": c["g2_bytes"]},
           "bits_per_sample": {"G1": round(8 * c["g1_bytes"] / n, 4), "G2": round(8 * c["g2_bytes"] / n, 4)},
           "chunk_kinds": {"G1": kinds(g1), "G2": kinds(g2)},
           "chunks_changed": len(changed), "changed_by_G1_kind": dict(sorted(by_kind.items())),
           "s4_violations": s4_bad, "exact": bool(c["exact"]), "bench_example_agrees": bench_agrees}
    if ref is not None:
        arms = ref["arms"]
        ch = {k: arms[k]["bits"] for k in CH_ARMS if arms[k].get("exact")}
        best = min(ch, key=ch.get)
        row["b4"] = {"samples": ref["samples"], "sha256_matches": ref["sha256"] == row["sha256"],
                     "samples_match": ref["samples"] == n, "M_auto_bytes": arms["M-auto"]["bytes"],
                     "M_auto_bits": arms["M-auto"]["bits"], "G1_matches_M_auto": arms["M-auto"]["bytes"] == c["g1_bytes"],
                     "CH_best_arm": best, "CH_best_bits": ch[best], "CH_arms_bits": ch,
                     "PCO_bits": arms["PCO"]["bits"], "PCO_exact": arms["PCO"]["exact"]}
        row["ratios"] = {"G2_over_CH_best": round(8 * c["g2_bytes"] / n / ch[best], 4),
                         "G1_over_CH_best": round(8 * c["g1_bytes"] / n / ch[best], 4),
                         "G2_over_PCO": round(8 * c["g2_bytes"] / n / arms["PCO"]["bits"], 4),
                         "G2_over_G1": round(c["g2_bytes"] / c["g1_bytes"], 4)}
    return row


def load(name, which):
    x, lengths, _ = b4.load_wave(name) if which == "waveform" else b4.load_elf(name)
    return np.ascontiguousarray(x, dtype="<f8"), lengths


def main_storage(out, which="benchmark"):
    ref = json.loads(B4_FINAL.read_text())["inputs"]
    res = {"study": "B5 storage (PROTOCOL.md section 3)", "set": which, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "chunk": b4.CHUNK, "quantum": 1.0, "bench": {"path": str(BENCH), "sha256": sha_file(BENCH)},
           "b5codec": {"path": str(CODEC), "sha256": sha_file(CODEC)}, "inputs": []}
    with tempfile.TemporaryDirectory() as tmp:
        for name, kind, _ in inputs(which):
            over_budget(out, res, name)
            t = time.perf_counter()
            x, lengths = load(name, kind)
            row = storage_one(name, kind, x, lengths, Path(tmp) / "chunks.bin", ref.get(name))
            row["seconds"] = round(time.perf_counter() - t, 1)
            print(json.dumps({k: row[k] for k in ("input", "samples", "bits_per_sample", "chunks_changed")}
                             | {"b4": {k: row.get("b4", {}).get(k) for k in ("G1_matches_M_auto", "sha256_matches")}}),
                  flush=True)
            res["inputs"].append(row)
            write(out, res)   # after every input, so a stop leaves what was measured
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write(out, res)


def over_budget(out, res, name):
    """Section 7: storage and codec speed take at most 2 hours (B5_STOP_AT, epoch seconds, set by the runner)."""
    stop_at = float(os.environ.get("B5_STOP_AT", "inf"))
    if time.time() >= stop_at:
        res["stopped"] = (f"budget: the 2-hour storage and codec-speed budget ended at "
                          f"{time.strftime('%H:%M:%S', time.localtime(stop_at))}, before {name}")
        write(out, res)
        print(res["stopped"], flush=True)
        raise SystemExit(4)


def loadavg():
    return float(subprocess.run(["sysctl", "-n", "vm.loadavg"], capture_output=True, text=True).stdout.split()[1])


def main_speed(out, which="benchmark"):
    res = {"study": "B5 codec speed (PROTOCOL.md section 4A)", "set": which,
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "passes": 3, "threads": 1,
           "b5codec": {"path": str(CODEC), "sha256": sha_file(CODEC)}, "inputs": []}
    with tempfile.TemporaryDirectory() as tmp:
        for name, kind, _ in inputs(which):
            if kind == "elf-dev" and which == "benchmark":
                continue   # section 4A: the six waveforms (criterion) and the 18 Elf benchmark series
            over_budget(out, res, name)
            x, lengths = load(name, kind)
            path = Path(tmp) / "chunks.bin"
            chunk_file(x, lengths, path)
            before = loadavg()
            r = run_json([CODEC, "speed", 3, path])
            row = {"input": name, "set": kind, "samples": r["samples"], "G1": r["G1"], "G2": r["G2"],
                   "G2_over_G1_encode": round(r["G2"]["encode_msamples_s"] / r["G1"]["encode_msamples_s"], 4),
                   "G2_over_G1_decode": round(r["G2"]["decode_msamples_s"] / r["G1"]["decode_msamples_s"], 4),
                   "load1_before": before, "load1_after": loadavg()}
            print(json.dumps(row), flush=True)
            res["inputs"].append(row)
            write(out, res)
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write(out, res)


def sha_file(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def image_id(ref):
    r = subprocess.run(["docker", "image", "inspect", ref, "--format", "{{.Id}} {{json .RepoDigests}}"],
                       capture_output=True, text=True)
    if r.returncode:
        return None
    i, _, d = r.stdout.strip().partition(" ")
    return {"id": i, "repo_digests": json.loads(d or "[]")}


def main_build(out):
    """results/build.json (section 7): build commit, image digests, decoder sha256, pco version."""
    lock = (ROOT / "Cargo.lock").read_text()
    at = lock.index('name = "pco"')
    entry = lock[at:lock.index("[[package]]", at) if "[[package]]" in lock[at:] else None]
    res = {"build_commit": "5ce0328", "pco": {"version": "1.0.4", "cargo_lock_entry": entry.strip(),
                                              "cargo_lock_sha256_at_build_commit": os.environ.get("B5_LOCK_SHA")},
           "images": {"morletdb:b5": image_id("morletdb:b5"),
                      "ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1": image_id("ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1"),
                      "questdb/questdb:10.1.0": image_id("questdb/questdb:10.1.0")},
           "decoders": {"b5": {"path": os.environ.get("B5_NATIVE_LIB"), "sha256": sha_file(os.environ.get("B5_NATIVE_LIB", "")),
                               "built_from": "5ce0328, cargo build --locked --release -p morlet-ffi"},
                        "release": {"path": os.environ.get("REL_NATIVE_LIB"), "sha256": sha_file(os.environ.get("REL_NATIVE_LIB", "")),
                                    "from": "morletdb-0.1.0a3-py3-none-macosx_11_0_arm64.whl of release v0.3.10-alpha.1",
                                    "wheel_sha256": os.environ.get("REL_WHEEL_SHA"),
                                    "wheel_sha256_in_SHA256SUMS": os.environ.get("REL_WHEEL_SHA_LISTED")}},
           "programs": {"rawcodec_bench": {"path": str(BENCH), "sha256": sha_file(BENCH),
                                           "built_from": "5ce0328, cargo build --locked --release -p morlet-core --example rawcodec_bench"},
                        "b5codec": {"path": str(CODEC), "sha256": sha_file(CODEC),
                                    "built_from": "evaluation/storage_b5/b5codec, grizzbit crate identical to 5ce0328"}},
           "written": time.strftime("%Y-%m-%dT%H:%M:%S")}
    write(out, res)


def write(out, obj):
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(obj, indent=1) + "\n")


if __name__ == "__main__":
    {"storage": main_storage, "speed": main_speed, "build": main_build}[sys.argv[1]](*sys.argv[2:])
