#!/usr/bin/env python3
"""Tables for README.md from results/dev.json (DEVELOPMENT ONLY).

  python3 -I evaluation/codec_g21/summarize.py [evaluation/codec_g21/results/dev.json]

Group means are sample-weighted bits/sample. The SRLA and T.261 columns are not rerun: they are copied from the
codec comparison T1 (evaluation/codec_t261/README.md on branch dev/t261-reference, commit 2a89c40), where they
were measured on the same development inputs' integers k (the "on k*q" view here).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ["g1", "corr", "lpc", "g21"]
LABEL = {"g1": "Grizzbit 1", "corr": "2.1 corrections only", "lpc": "2.1 LPC only", "g21": "Grizzbit 2.1"}
GROUPS = ["vibration", "phase currents", "force", "femto", "elf (B4 dev)"]
# T1 (2a89c40) group means on the integers, 4,096-sample resets where applicable:
# (SRLA default, T.261 best preset, T.261 best with intra period 4,096)
T1 = {
    "vibration": (8.091, 7.945, 8.026),
    "phase currents": (7.703, 7.592, 7.800),
    "force": (7.763, 7.391, 7.507),
    "femto": (10.040, 9.951, 10.003),
    "all 21": (8.973, 8.865, 8.971),
}


def mean(rows, arm, key="bits_per_sample"):
    n = sum(r["n"] for r in rows)
    return sum(r[arm][key] * r["n"] for r in rows) / n


STRATEGIES = ["around", "estimated", "est+max", "fixed-8-16-32", "fixed-12-32", "fixed-32", "skip-20", "skip-50"]


def strategies(d):
    """LPC order-selection strategies (results/dev_strategies.json): bits/sample, encode and decode speed."""
    rows = d["rows"]
    print(f"chunk {d['chunk']} samples, best of {d['reps']}, as stored\n")
    print("### Per group: bits/sample | encode | decode (M samples/s, harmonic mean)\n")
    print("| group | Grizzbit 1 | " + " | ".join(STRATEGIES) + " |")
    print("|---|" + "---|" * (len(STRATEGIES) + 1))
    sels = [("Paderborn 64 kHz", lambda r: r["group"] in ("vibration", "phase currents"))]
    sels += [(g, (lambda g: lambda r: r["group"] == g)(g)) for g in GROUPS]
    sels += [("all 21 (T1 inputs)", lambda r: r["group"] != "elf (B4 dev)")]
    for g, f in sels:
        sel = [r for r in rows if f(r)]
        if not sel:
            continue
        n = sum(r["n"] for r in sel)
        cell = lambda a: (f"{mean(sel, a):.3f} / {n / sum(r['n'] / r[a]['enc_msps'] for r in sel):.1f} / "
                          f"{n / sum(r['n'] / r[a]['dec_msps'] for r in sel):.0f}")
        print(f"| {g} | {cell('g1')} | " + " | ".join(cell(a) for a in STRATEGIES) + " |")
    print()
    print("### Per input: bits/sample | encode M samples/s\n")
    print("| input | Grizzbit 1 | " + " | ".join(STRATEGIES) + " |")
    print("|---|" + "---|" * (len(STRATEGIES) + 1))
    for r in rows:
        cell = lambda a: f"{r[a]['bits_per_sample']:.3f} / {r[a]['enc_msps']:.1f}"
        print(f"| {r['name']} | {cell('g1')} | " + " | ".join(cell(a) for a in STRATEGIES) + " |")
    print()
    print("### Slowest decode per strategy (M samples/s, input)\n")
    for a in STRATEGIES:
        w = [r for r in rows if r["group"] != "elf (B4 dev)"]
        slow = min(w, key=lambda r: r[a]["dec_msps"])
        print(f"- {a}: {slow[a]['dec_msps']:.0f} ({slow['name']})")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results/dev.json"
    d = json.loads(path.read_text())
    if d.get("strategies"):
        return strategies(d)
    rows = d["rows"]
    print(f"chunk {d['chunk']} samples, best of {d['reps']}\n")
    for view in ["as stored", "on k*q"]:
        print(f"### {view}\n")
        hdr = "| group | samples | " + " | ".join(LABEL[a] for a in ARMS) + " | 2.1 vs 1 |"
        if view == "on k*q":
            hdr += " SRLA default (T1) | T.261 best (T1) | T.261 best, intra 4,096 (T1) |"
        print(hdr)
        print("|" + "---|" * (hdr.count("|") - 1))
        groups = [g for g in GROUPS if any(r["view"] == view and r["group"] == g for r in rows)]
        names = ["Paderborn 64 kHz"] + groups
        names += ["all 21"] if view == "on k*q" else ["all 21 (T1 inputs)", "all 25"]
        for g in names:
            if g == "Paderborn 64 kHz":
                sel = [r for r in rows if r["view"] == view and r["group"] in ("vibration", "phase currents")]
            elif g in ("all 21", "all 21 (T1 inputs)"):
                sel = [r for r in rows if r["view"] == view and r["group"] != "elf (B4 dev)"]
            elif g == "all 25":
                sel = [r for r in rows if r["view"] == view]
            else:
                sel = [r for r in rows if r["view"] == view and r["group"] == g]
            if not sel:
                continue
            vals = [mean(sel, a) for a in ARMS]
            line = f"| {g} | {sum(r['n'] for r in sel):,} | " + " | ".join(f"{v:.3f}" for v in vals)
            line += f" | {vals[3] - vals[0]:+.3f} ({100 * (vals[3] / vals[0] - 1):+.1f} %) |"
            if view == "on k*q":
                t = T1.get(g)
                line += " " + " | ".join(f"{v:.3f}" for v in t) + " |" if t else " | | |"
            print(line)
        print()
    print("### Per input, as stored (bits/sample)\n")
    print("| input | group | " + " | ".join(LABEL[a] for a in ARMS) + " | chunks | 2.1 chunks: v5 / table / exceptions / per-sample / none / LPC |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r["view"] != "as stored":
            continue
        f = r["g21"]["fix_mode"]
        print(f"| {r['name']} | {r['group']} | " + " | ".join(f"{r[a]['bits_per_sample']:.3f}" for a in ARMS)
              + f" | {r['chunks']} | {r['g21']['v5_chunks']} / {f['table']} / {f['exceptions']} / {f['per_sample']} / "
              f"{f['none']} / {r['g21']['lpc_chunks']} |")
    print()
    print("### Share of chunks choosing each new path (Grizzbit 2.1, both ideas)\n")
    print("| group | view | chunks | version 5 | table | exceptions only | per-sample | no corrections | LPC |")
    print("|---|---|---|---|---|---|---|---|---|")
    for view in ["as stored", "on k*q"]:
        for g in GROUPS:
            sel = [r for r in rows if r["view"] == view and r["group"] == g]
            if not sel:
                continue
            c = sum(r["chunks"] for r in sel)
            pct = lambda k: f"{100 * k / c:.0f} %"
            fm = {m: sum(r["g21"]["fix_mode"][m] for r in sel) for m in ["table", "exceptions", "per_sample", "none"]}
            print(f"| {g} | {view} | {c} | {pct(sum(r['g21']['v5_chunks'] for r in sel))} | {pct(fm['table'])} | "
                  f"{pct(fm['exceptions'])} | {pct(fm['per_sample'])} | {pct(fm['none'])} | "
                  f"{pct(sum(r['g21']['lpc_chunks'] for r in sel))} |")
    print()
    print("### Speed (M samples/s, single thread, sample-weighted harmonic mean per group)\n")
    print("| group | view | encode G1 | encode 2.1 | decode G1 | decode 2.1 | decode 2.1 slowest input |")
    print("|---|---|---|---|---|---|---|")
    for view in ["as stored", "on k*q"]:
        for g in GROUPS:
            sel = [r for r in rows if r["view"] == view and r["group"] == g]
            if not sel:
                continue
            n = sum(r["n"] for r in sel)
            hm = lambda a, k: n / sum(r["n"] / r[a][k] for r in sel)
            slow = min(sel, key=lambda r: r["g21"]["dec_msps"])
            print(f"| {g} | {view} | {hm('g1', 'enc_msps'):.1f} | {hm('g21', 'enc_msps'):.1f} | "
                  f"{hm('g1', 'dec_msps'):.0f} | {hm('g21', 'dec_msps'):.0f} | {slow['g21']['dec_msps']:.0f} ({slow['name']}) |")


if __name__ == "__main__":
    main()
