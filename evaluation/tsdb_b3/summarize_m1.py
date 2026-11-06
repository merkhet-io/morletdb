#!/usr/bin/env python3
"""Study M1 summary: the smallest memory cap (256 MB to 4 GB, swap disabled) at which each system completed B3's D2
load with every count and read correct, and, at that cap and at the 16 GB reference in the same session, ingestion
speed, read and statistics latency (p50) and the 50 ms working-set peak. One load per cell: descriptive, no B3
statements.

  python3 evaluation/tsdb_b3/summarize_m1.py
"""
import json
from pathlib import Path

R = Path(__file__).resolve().parent / "results" / "m1"
SYSTEMS = [("morletdb", "morletdb-i2", "MorletDB 0.3.7"), ("questdb", "questdb", "QuestDB 10.1"),
           ("timescale", "timescale", "TimescaleDB 2.31"), ("tdengine", "tdengine-default", "TDengine 3.4"),
           ("iotdb", "iotdb-table", "IoTDB 2.0"), ("crate", "crate-array", "CrateDB 6.4")]
CAPS = ["256m", "512m", "1g", "2g", "4g"]
MIB = {"256m": 256, "512m": 512, "1g": 1024, "2g": 2048, "4g": 4096}


def result(cap, pref):
    f = R / cap / f"load_{pref}_D2_r1.json"
    return json.loads(f.read_text()) if f.exists() else None


def reads(d):
    # each system's faster layout per B3 (TimescaleDB: row or columnstore reads; the lower p50 of the two)
    keys = [k for k in d if k.startswith("reads")]
    best = {}
    for k in keys:
        for q in ("R1", "R2"):
            if q in d[k]:
                best[q] = min(best.get(q, 1e9), d[k][q]["p50_ms"])
    stats = [d[k]["p50_ms"] for k in d if k.startswith("analytics") and isinstance(d[k], dict) and "p50_ms" in d[k]]
    return best.get("R1"), best.get("R2"), min(stats) if stats else None


def row(d):
    if not d:
        return "–", "–", "–", "–", "–"
    s = d["ingest"]["server"]
    ws = (s.get("kernel_memory") or {}).get("working_set_peak_mib_50ms")
    r1, r2, st = reads(d)
    f = lambda v: "–" if v is None else f"{v:g}"
    return f"{d['ingest']['samples_per_s'] / 1e6:.2f} M/s", f(r1), f(r2), f(st), f(ws)


outcomes = [l.split("\t") for l in (R / "outcomes.tsv").read_text().splitlines()] if (R / "outcomes.tsv").exists() else []
print("| System | smallest passing cap | tried (cap: exit, s) | at 16 GB: ingest / R1 / R2 / stats ms / working set MiB "
      "| at smallest cap: the same |")
print("|---|---|---|---|---|")
mins = {}
for key, pref, name in SYSTEMS:
    tried = [f"{o[1]}: {o[2]}, {o[3]} s" for o in outcomes if o[0] == key]
    m = next((c for c in CAPS if (R / f"min_{key}").exists() and (R / f"min_{key}").read_text().strip() == c), None)
    mins[key] = m
    ref, at = row(result("16g", pref)), row(result(m, pref) if m else None)
    print(f"| {name} | {m or '> 4 GB'} | {'; '.join(tried)} | {' / '.join(ref)} | {' / '.join(at)} |")
base = mins.get("morletdb")
if base:
    print("\nSmallest passing cap as a multiple of MorletDB's:")
    for key, _, name in SYSTEMS[1:]:
        m = mins.get(key)
        print(f"- {name}: " + (f"{MIB[m] / MIB[base]:g}×" if m else f"> {MIB['4g'] / MIB[base]:g}×"))
