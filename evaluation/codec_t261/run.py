#!/usr/bin/env python3
"""Codec comparison T1 (development only): Grizzbit 1 vs the T.261 reference software in lossless mode.

  .venv-b3/bin/python -I evaluation/codec_t261/prepare.py BUILD/work
  (cd evaluation/codec_t261/grizzbit_bench && cargo build --release)
  .venv-b3/bin/python -I evaluation/codec_t261/run.py BUILD evaluation/codec_t261/results/dev.json [--quick]

BUILD holds bin/EncoderApp, bin/DecoderApp (built from the reference software, see README.md), cfg/ (its preset
files) and work/ (prepare.py's output). Bitstreams and decoded files go to BUILD/out/; nothing from BUILD is
committed. Every T.261 bitstream is decoded with DecoderApp and compared byte for byte with the input integers.
Speed: single process, best of 3 runs; T.261 times are the encoder's and decoder's own reported elapsed time
(file I/O included, process start excluded); Grizzbit times are in-process over all chunks.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE / "grizzbit_bench/target/release/grizzbit_bench"
PRESETS = ["EEG", "EMG", "ECG", "ACoM"]
REPS = 3
NICE = ["nice", "-n", "10"]
# Tool ablations (config keys of the reference software), run once each on the best preset.
ABLATIONS = {
    "no_lms": {"cgps_allow_lms_flag": 0},
    "no_cc_lms": {"cgps_allow_cc_lms_flag": 0},
    "no_linear_model": {"UseLinearModel": 0},
    "no_block_matching": {"UseBlockMatching": 0},
    "no_lpc_dct": {"UsePreLPC": 0},
    "no_zero_lsb": {"cgps_allow_zero_lsb_flag": 0},
    "lms_order_8": {"LMS_ORDER": 8},
    "lms_order_32": {"LMS_ORDER": 32},
}


def tool_bits(bits):
    """InputBitDepth for T.261: 16, 24 or 32. The reference encoder aborts on 13, 14 and 15 (seen on 12-bit
    and 14-bit inputs), and 16 codes no larger than the minimal 12 on the one input where both run."""
    return 16 if bits <= 16 else 24 if bits <= 24 else 32


def elapsed(log):
    m = re.findall(r"Total Time:\s+([\d.]+) sec\. \[user\]\s+([\d.]+) sec\. \[elapsed\]", log)
    return float(m[-1][1]) if m else None


def t261(build, kfile, channels, bits, cfg, tag, extra=None, reps=REPS):
    out = build / "out"
    out.mkdir(exist_ok=True)
    bs = out / f"{kfile.stem}.{tag}.bin"
    dec = out / f"{kfile.stem}.{tag}.dec"
    args = [str(build / "bin/EncoderApp"), "-c", str(build / "cfg" / cfg), f"--InputFile={kfile}", "--FileFormat=Raw",
            f"--InputNumChannels={channels}", f"--InputBitDepth={tool_bits(bits)}", f"--BitstreamFile={bs}"]
    args += [f"--{k}={v}" for k, v in (extra or {}).items()]
    te, td = [], []
    for _ in range(reps):
        r = subprocess.run(NICE + args, capture_output=True, text=True, cwd=out)
        if r.returncode != 0:
            return {"error": (r.stdout + r.stderr)[-600:]}
        te.append(elapsed(r.stdout + r.stderr))
        d = subprocess.run(NICE + [str(build / "bin/DecoderApp"), f"--BitstreamFile={bs}", f"--OutputFile={dec}",
                                   "--FileFormat=Raw"], capture_output=True, text=True, cwd=out)
        if d.returncode != 0:
            return {"error": (d.stdout + d.stderr)[-600:]}
        td.append(elapsed(d.stdout + d.stderr))
    orig = kfile.read_bytes()
    width = 2 if bits <= 16 else 4
    a, b = orig, dec.read_bytes()
    mism = sum(1 for i in range(0, min(len(a), len(b)), width) if a[i:i + width] != b[i:i + width]) if a != b else 0
    rec = Path(str(bs) + ".rec")
    res = {"bytes": bs.stat().st_size, "decoded_equal": a == b, "mismatched_samples": mism + abs(len(a) - len(b)) // width,
           "rec_equal": rec.exists() and rec.read_bytes() == a, "enc_s": min(te), "dec_s": min(td)}
    for p in (bs, dec, rec):
        p.unlink(missing_ok=True)
    return res


def grizzbit(name, path, quantum):
    r = subprocess.run(NICE + [str(BENCH), "grizzbit", name, str(path), repr(quantum)], capture_output=True, text=True,
                       check=True)
    return json.loads(r.stdout)


def ideas(name, path, bits):
    r = subprocess.run(NICE + [str(BENCH), "ideas", name, str(path), str(bits)], capture_output=True, text=True,
                       check=True)
    return json.loads(r.stdout)


def bps(nbytes, n):
    return 8.0 * nbytes / n


def main():
    build, outp = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    quick = "--quick" in sys.argv
    work = build / "work"
    spec = json.loads((work / "inputs.json").read_text())
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    inputs = spec["inputs"][:2] if quick else spec["inputs"]
    for inp in inputs:
        name, n, bits = inp["name"], inp["samples"], inp["bits"]
        kfile = work / f"{name}.k"
        row = {k: inp[k] for k in ("name", "set", "group", "class", "samples", "bits", "quantum", "max_dev_steps")}
        row["grizzbit_orig"] = grizzbit(name, work / f"{name}.f64", inp["quantum"])
        if inp["class"] == "near":
            row["grizzbit_grid"] = grizzbit(name, work / f"{name}.grid.f64", inp["quantum"])
        row["ideas"] = ideas(name, kfile, bits)
        t = {}
        for p in PRESETS:
            r = t261(build, kfile, 1, bits, f"combinedPreset{p}_IndepChannel_lossless.cfg", p)
            if "bytes" in r:
                r["bits_per_sample"] = bps(r["bytes"], n)
                r["enc_msps"] = n / r["enc_s"] / 1e6 if r["enc_s"] else None
                r["dec_msps"] = n / r["dec_s"] / 1e6 if r["dec_s"] else None
            t[p] = r
        row["t261"] = t
        ok = {p: r for p, r in t.items() if "bytes" in r}
        if not ok:
            raise SystemExit(f"{name}: every T.261 preset failed: {t}")
        best = min(ok, key=lambda p: ok[p]["bytes"])
        row["t261_best"] = best
        # Chunk-comparable view: the best preset with an intra period of 4,096 samples (state reset like a chunk).
        r = t261(build, kfile, 1, bits, f"combinedPreset{best}_IndepChannel_lossless.cfg", best + "_ip4096",
                 {"IntraPeriod": 4096}, reps=1)
        if "bytes" in r:
            r["bits_per_sample"] = bps(r["bytes"], n)
        row["t261_intra4096"] = r
        print(json.dumps({"name": name, "grizzbit4096": row["grizzbit_orig"]["4096"]["bits_per_sample"],
                          "grizzbit_grid4096": row.get("grizzbit_grid", row["grizzbit_orig"])["4096"]["bits_per_sample"],
                          "t261": {p: round(r.get("bits_per_sample", -1), 4) for p, r in t.items()},
                          "ip4096": round(r.get("bits_per_sample", -1), 4),
                          "ideas4096": row["ideas"]["4096"]}), flush=True)
        rows.append(row)

    # Tool ablations on one input of each kind (best preset, one run each).
    abl = []
    picks = {}
    for row in rows:
        key = row["group"] if row["set"] == "b3-dev" else row["name"]
        if key not in picks:
            picks[key] = row
    if "phase_current_1" in "".join(r["name"] for r in rows):
        pc = [r for r in rows if r["name"].endswith("phase_current_1")]
        if pc:
            picks["phase_current"] = pc[0]
    for key, row in ([] if quick else picks.items()):
        kfile = work / f"{row['name']}.k"
        cfg = f"combinedPreset{row['t261_best']}_IndepChannel_lossless.cfg"
        res = {"name": row["name"], "preset": row["t261_best"],
               "base": row["t261"][row["t261_best"]]["bits_per_sample"]}
        for a, extra in ABLATIONS.items():
            r = t261(build, kfile, 1, row["bits"], cfg, "abl_" + a, extra, reps=1)
            res[a] = bps(r["bytes"], row["samples"]) if "bytes" in r else r.get("error")
            if "bytes" in r and not r["decoded_equal"]:
                res[a + "_lossless"] = False
        print(json.dumps(res), flush=True)
        abl.append(res)

    # Joint view: the three 64 kHz channels of an acquisition in one channel group (inter-channel tools).
    joint = []
    for j in ([] if quick else spec["joint"]):
        kfile = work / f"{j['name']}.k"
        n3 = 3 * j["samples"]
        t = {}
        for p in PRESETS:
            r = t261(build, kfile, 3, j["bits"], f"combinedPreset{p}_lossless.cfg", "joint" + p)
            if "bytes" in r:
                r["bits_per_sample"] = bps(r["bytes"], n3)
                r["enc_msps"] = n3 / r["enc_s"] / 1e6 if r["enc_s"] else None
                r["dec_msps"] = n3 / r["dec_s"] / 1e6 if r["dec_s"] else None
            t[p] = r
        per = {r["name"]: r for r in rows}
        g_orig = {s: sum(per[c]["grizzbit_orig"][s]["bytes"] for c in j["channels"]) for s in ("4096", "32768")}
        g_grid = {s: sum(per[c].get("grizzbit_grid", per[c]["grizzbit_orig"])[s]["bytes"] for c in j["channels"])
                  for s in ("4096", "32768")}
        indep = sum(min(v["bytes"] for v in per[c]["t261"].values() if "bytes" in v) for c in j["channels"])
        res = {"name": j["name"], "samples": n3, "bits": j["bits"], "t261_joint": t,
               "t261_indep_best_sum_bps": bps(indep, n3),
               "grizzbit_orig_sum_bps": {s: bps(v, n3) for s, v in g_orig.items()},
               "grizzbit_grid_sum_bps": {s: bps(v, n3) for s, v in g_grid.items()}}
        print(json.dumps({"name": j["name"], "joint": {p: round(r.get("bits_per_sample", -1), 4) for p, r in t.items()},
                          "indep": res["t261_indep_best_sum_bps"], "grizzbit_grid": res["grizzbit_grid_sum_bps"]}),
              flush=True)
        joint.append(res)

    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps({"study": "codec comparison T1 (development only)", "started": started,
                                "finished": time.strftime("%Y-%m-%dT%H:%M:%S"), "reference": "T.261 reference software 7.0",
                                "excluded": spec["excluded"], "inputs": rows, "ablations": abl, "joint": joint},
                               indent=1) + "\n")


if __name__ == "__main__":
    main()
