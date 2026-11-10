#!/usr/bin/env python3
"""Markdown tables for README.md from results/dev.json and results/dev_srla.json.

  python3 -I evaluation/codec_t261/summarize.py
"""
import json
from pathlib import Path

R = Path(__file__).resolve().parent / "results"
d = json.loads((R / "dev.json").read_text())
# dev.json was written before prepare.py treated negative zeros as off-grid; FEMTO's Grizzbit run on k*q (no
# negative zeros, the information T.261 and SRLA receive) is in dev_femto_grid.json.
_fg = json.loads((R / "dev_femto_grid.json").read_text())
for _r in d["inputs"]:
    if _r["name"] == _fg["name"] and "grizzbit_grid" not in _r:
        _r["grizzbit_grid"] = _fg
        _r["class"] = "near (-0.0)"
srla = {r["name"]: r for r in json.loads((R / "dev_srla.json").read_text())["inputs"]}


def short(name):
    return name.replace("N09_M07_F10_", "").replace("N15_M01_F10_", "").replace("N15_M07_F04_", "") \
        .replace("N15_M07_F10_", "").replace("phase_current_", "current_").replace("vibration_1", "vibration")


def f(v):
    return f"{v:.3f}"


print("### Bits per sample, per channel\n")
print("| input | class | Grizzbit 1 as stored, 4,096 / 32,768 | Grizzbit 1 on k*q, 4,096 / 32,768 | SRLA default / max / max+ "
      "| T.261 EEG / EMG / ECG / ACoM | T.261 best, intra 4,096 |")
print("|---|---|---|---|---|---|---|")
tot = {}
for r in d["inputs"]:
    go, gg = r["grizzbit_orig"], r.get("grizzbit_grid", r["grizzbit_orig"])
    s = srla[r["name"]]
    t = r["t261"]
    print(f"| {short(r['name'])} | {r['class']} | {f(go['4096']['bits_per_sample'])} / {f(go['32768']['bits_per_sample'])} "
          f"| {f(gg['4096']['bits_per_sample'])} / {f(gg['32768']['bits_per_sample'])} "
          f"| {f(s['default']['bits_per_sample'])} / {f(s['max']['bits_per_sample'])} / {f(s['max+']['bits_per_sample'])} "
          f"| {' / '.join(f(t[p]['bits_per_sample']) for p in ('EEG', 'EMG', 'ECG', 'ACoM'))} "
          f"| {f(r['t261_intra4096']['bits_per_sample'])} ({r['t261_best']}) |")

print("\n### Group means (bits/sample, sample-weighted) and estimates\n")
groups = {}
for r in d["inputs"]:
    key = r["name"].split(".")[-1] if r["set"] == "b3-dev" else r["name"]
    key = {"phase_current_1": "phase currents", "phase_current_2": "phase currents", "vibration_1": "vibration"}.get(key, key)
    groups.setdefault(key, []).append(r)
cols = ["grizzbit_grid_4096", "fixed_rice", "fixed_ac", "lpc_rice", "lpc_ac", "nlms_ac", "blk_rice", "srla_default",
        "srla_max", "srla_maxp", "t261_best", "t261_ip4096"]
print("| group | samples | " + " | ".join(cols) + " |")
print("|---|---" + "|---" * len(cols) + "|")
for g, rows in groups.items():
    n = sum(r["samples"] for r in rows)

    def w(fn):
        return sum(fn(r) * r["samples"] for r in rows) / n
    vals = [
        w(lambda r: r.get("grizzbit_grid", r["grizzbit_orig"])["4096"]["bits_per_sample"]),
        *[w(lambda r, k=k: r["ideas"]["4096"][k]) for k in ("fixed_rice", "fixed_ac", "lpc_rice", "lpc_ac", "nlms_ac", "blk_rice")],
        w(lambda r: srla[r["name"]]["default"]["bits_per_sample"]),
        w(lambda r: srla[r["name"]]["max"]["bits_per_sample"]),
        w(lambda r: srla[r["name"]]["max+"]["bits_per_sample"]),
        w(lambda r: r["t261"][r["t261_best"]]["bits_per_sample"]),
        w(lambda r: r["t261_intra4096"]["bits_per_sample"]),
    ]
    print(f"| {g} | {n:,} | " + " | ".join(f(v) for v in vals) + " |")

print("\n### Lossless checks\n")
bad_t = [(r["name"], p) for r in d["inputs"] for p, v in r["t261"].items() if not v.get("decoded_equal")]
bad_j = [(j["name"], p) for j in d["joint"] for p, v in j["t261_joint"].items() if not v.get("decoded_equal")]
bad_g = [r["name"] for r in d["inputs"] for k in ("grizzbit_orig", "grizzbit_grid") if k in r
         for s in ("4096", "32768") if r[k][s]["fail"]]
bad_s = [(n, s) for n, r in srla.items() for s in ("default", "max", "max+") if not r[s]["decoded_equal"]]
bad_i = [r["name"] for r in d["inputs"] if not r["t261_intra4096"].get("decoded_equal")]
n_t = sum(len(r["t261"]) for r in d["inputs"]) + len(d["inputs"]) + sum(len(j["t261_joint"]) for j in d["joint"])
print(f"T.261 bitstreams decoded and compared: {n_t}; failures: {bad_t + bad_j + bad_i}")
print(f"SRLA: {3 * len(srla)} decoded; failures: {bad_s}")
print(f"Grizzbit chunks failing: {bad_g}")
mism = sum(v.get("mismatched_samples", 0) for r in d["inputs"] for v in r["t261"].values())
print(f"T.261 mismatched samples (per-channel arms): {mism}")

print("\n### Speed (M samples/s, best of 3, single thread)\n")
print("| group | Grizzbit enc / dec (4,096) | SRLA default enc / dec | SRLA max enc / dec | T.261 best enc / dec |")
print("|---|---|---|---|---|")
for g, rows in groups.items():
    n = sum(r["samples"] for r in rows)

    def rate(fn):
        return n / sum(fn(r) for r in rows) / 1e6
    gg = lambda r: r.get("grizzbit_grid", r["grizzbit_orig"])["4096"]  # noqa: E731
    print(f"| {g} | {rate(lambda r: gg(r)['enc_s']):.1f} / {rate(lambda r: gg(r)['dec_s']):.1f} "
          f"| {rate(lambda r: srla[r['name']]['default']['enc_s']):.2f} / {rate(lambda r: srla[r['name']]['default']['dec_s']):.2f} "
          f"| {rate(lambda r: srla[r['name']]['max']['enc_s']):.2f} / {rate(lambda r: srla[r['name']]['max']['dec_s']):.2f} "
          f"| {rate(lambda r: r['t261'][r['t261_best']]['enc_s']):.2f} / {rate(lambda r: r['t261'][r['t261_best']]['dec_s']):.2f} |")
go = lambda r: r["grizzbit_orig"]["4096"]  # noqa: E731
print("\nGrizzbit as stored (with last-place corrections), all inputs: "
      f"enc {sum(r['samples'] for r in d['inputs']) / sum(go(r)['enc_s'] for r in d['inputs']) / 1e6:.1f}, "
      f"dec {sum(r['samples'] for r in d['inputs']) / sum(go(r)['dec_s'] for r in d['inputs']) / 1e6:.1f} M samples/s")

print("\n### Tool ablations (T.261, best preset, bits/sample)\n")
keys = [k for k in d["ablations"][0] if k not in ("name", "preset", "base")] if d["ablations"] else []
print("| input | preset | base | " + " | ".join(keys) + " |")
print("|---|---|---" + "|---" * len(keys) + "|")
for a in d["ablations"]:
    print(f"| {short(a['name'])} | {a['preset']} | {f(a['base'])} | "
          + " | ".join(f(a[k]) if isinstance(a[k], float) else str(a[k])[:20] for k in keys) + " |")

print("\n### Joint view: the three 64 kHz channels together (bits/sample over all three)\n")
print("| acquisition | T.261 joint EEG / EMG / ECG / ACoM | T.261 per-channel best, summed | Grizzbit 1 on k*q, sum (4,096) | Grizzbit 1 as stored, sum (4,096) |")
print("|---|---|---|---|---|")
for j in d["joint"]:
    t = j["t261_joint"]
    print(f"| {short(j['name'])} | {' / '.join(f(t[p]['bits_per_sample']) if 'bits_per_sample' in t[p] else 'err' for p in ('EEG', 'EMG', 'ECG', 'ACoM'))} "
          f"| {f(j['t261_indep_best_sum_bps'])} | {f(j['grizzbit_grid_sum_bps']['4096'])} | {f(j['grizzbit_orig_sum_bps']['4096'])} |")
