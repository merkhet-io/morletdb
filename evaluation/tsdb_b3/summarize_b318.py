#!/usr/bin/env python3
"""Summaries of B3.18 (a pair: MorletDB beside CedarDB or InfluxDB 3 Core) and B3.19 (interval statistics against
CedarDB, three D2 r1 rounds), from the result files, by B3's statement rule (PROTOCOL.md).

    python summarize_b318.py pair results/b318-influxdb3 influxdb3
    python summarize_b318.py b319 results/b319-cedardb-s1 results/b319-cedardb-s2 results/b319-cedardb-s3
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics
import sys

M = "morletdb-i1-sum"
MIB = 1 << 20


def load(d, system, dur, rep):
    p = os.path.join(d, f"load_{system}_{dur}_r{rep}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def rate(r):
    return r["ingest"]["samples_per_s"] / 1e6


def reads(r):
    return r.get("reads") or r.get("reads_db")


def stats(r):
    return r.get("analytics") or r.get("analytics_db")


def ratio_text(a, b, unit_faster="faster"):
    """a and b are latencies (lower is better): who is faster, by how much."""
    return ("MorletDB", b / a) if a < b else ("other", a / b)


def lat_row(name, m, o, other):
    (mp, m95), (op, o95) = m, o
    w50, f50 = ratio_text(mp, op)
    w95, f95 = ratio_text(m95, o95)
    if w50 == w95 and f50 >= 1.1 and f95 >= 1.1:
        who = "MorletDB" if w50 == "MorletDB" else other
        st = f"{who} **{f50:.1f}× / {f95:.1f}×**" if min(f50, f95) >= 1.5 else f"{who} {f50:.2f}× / {f95:.2f}×"
    else:
        st = f"no statement ({'MorletDB' if w50 == 'MorletDB' else other} {f50:.2f}× at p50, " \
             f"{'MorletDB' if w95 == 'MorletDB' else other} {f95:.2f}× at p95)"
    return f"| {name} | {mp:.2f} / {m95:.2f} ms | {op:.2f} / {o95:.2f} ms | {st} |"


def machine(d):
    loads, foreign = [], []
    for f in sorted(glob.glob(os.path.join(d, "machine", "*.txt"))):
        for block in open(f).read().split("\n20")[0:]:
            mo = re.search(r"load averages?: ([\d.,]+)", block)
            if mo:
                loads.append(float(mo.group(1).replace(",", ".")))
            for line in block.splitlines():
                mm = re.match(r"\s*([\d.]+)\s+(\S+)", line)
                if mm and float(mm.group(1)) > 50 and not re.search(r"Virtualization|com\.docker|%CPU", line):
                    foreign.append(os.path.basename(mm.group(2)))
    return loads, foreign


def pair(d, other):
    print(f"## {d}\n")
    bad = [f for f in glob.glob(os.path.join(d, "load_*.json")) if not json.load(open(f)).get("correct")]
    n = len(glob.glob(os.path.join(d, "load_*.json")))
    print(f"Correctness: {n - len(bad)} of {n} loads passed" + (f"; failed: {bad}" if bad else "") + "\n")
    print(f"| | MorletDB | {other} | Statement |\n|---|---:|---:|---|")
    for dur in ("D2", "D1"):
        mr = [rate(r) for r in (load(d, M, dur, k) for k in (1, 2, 3)) if r]
        orr = [rate(r) for r in (load(d, other, dur, k) for k in (1, 2, 3)) if r]
        bound = min(mr) / max(orr) if min(mr) > max(orr) else max(orr) / min(mr)
        who = "MorletDB" if min(mr) > max(orr) else (other if max(mr) < min(orr) else None)
        st = f"{who} **{statistics.median(mr) / statistics.median(orr):.1f}×** faster (all-loads bound {bound:.1f})" \
            if who == "MorletDB" and bound >= 1.1 else (f"{other} faster" if who else "no statement")
        print(f"| Ingestion {dur}, median [range] | {statistics.median(mr):.2f} M [{min(mr):.2f}–{max(mr):.2f}] "
              f"samples/s | {statistics.median(orr):.2f} M [{min(orr):.2f}–{max(orr):.2f}] | {st} |")
    m1, o1 = load(d, M, "D2", 1), load(d, other, "D2", 1)
    mdb = m1["storage"]["database"] / MIB
    vol = o1.get("volume_db", {})
    print(f"| Data on disk | {mdb:.1f} MiB (database) | {vol.get('apparent', 0) / MIB:,.1f} MiB "
          f"({json.dumps({k: v for k, v in vol.items() if k not in ('apparent',)})}) | "
          f"{'MorletDB' if mdb < vol.get('apparent', 0) / MIB else other} "
          f"{max(mdb, vol['apparent'] / MIB) / min(mdb, vol['apparent'] / MIB):.1f}× smaller |")
    mr_, or_ = reads(m1), reads(o1)
    for k, name in (("R1", "R1, 4,096 samples, p50 / p95"), ("R2", "R2, 128,000 samples"),
                    ("R3", "R3, 3 × 64,000 samples"), ("R4_recent", "R4 recent"), ("R4_older", "R4 older")):
        print(lat_row(name, (mr_[k]["p50_ms"], mr_[k]["p95_ms"]), (or_[k]["p50_ms"], or_[k]["p95_ms"]), other))
    q5m, q5o = mr_["R5"]["queries_per_s"], or_["R5"]["queries_per_s"]
    print(f"| R5, 4 threads | {q5m:,.0f} queries/s | {q5o:,.0f} queries/s | "
          f"{'MorletDB' if q5m > q5o else other} {max(q5m, q5o) / min(q5m, q5o):.1f}× |")
    sm, so = stats(m1), stats(o1)
    print(lat_row("Interval statistics", (sm["p50_ms"], sm["p95_ms"]), (so["p50_ms"], so["p95_ms"]), other))
    wm = m1["ingest"]["server"]["kernel_memory"]["working_set_peak_mib_50ms"]
    wo = o1["ingest"]["server"]["kernel_memory"]["working_set_peak_mib_50ms"]
    print(f"| Server working set while ingesting | {wm:,.0f} MiB | {wo:,.0f} MiB | MorletDB {wo / wm:.0f}× less |")
    extra = {k: o1.get(k) for k in ("persist_db",) if o1.get(k)}
    print(f"\nOther D2 r1: conversion {o1['ingest'].get('conversion_s')} s of {o1['ingest']['wall_s']} s wall; {extra}; "
          f"server CPU mean {o1['ingest']['server']['cpu_percent_mean']}% vs MorletDB "
          f"{m1['ingest']['server']['cpu_percent_mean']}%")
    starts = sorted(json.load(open(f))["started"] for f in glob.glob(os.path.join(d, "load_*.json")))
    loads, foreign = machine(d)
    print(f"Session {starts[0]} .. {starts[-1]}; snapshot load median {statistics.median(loads):.1f} "
          f"(max {max(loads):.1f}, n {len(loads)}); foreign processes above 50% CPU: {foreign}\n")


def b319(dirs):
    print("## B3.19\n\n| Round | MorletDB 0.3.10 p50 / p95 | CedarDB p50 / p95 | MorletDB faster by (p50 / p95) | Valid |\n"
          "|---|---|---|---|---|")
    valid = []
    for k, d in enumerate(dirs, 1):
        m, c = load(d, M, "D2", 1), load(d, "cedardb", "D2", 1)
        ok = bool(m and c and m.get("correct") and c.get("correct"))
        sm = stats(m) if m else None
        sc = stats(c) if c and c.get("correct") else None
        if ok:
            f50, f95 = sc["p50_ms"] / sm["p50_ms"], sc["p95_ms"] / sm["p95_ms"]
            valid.append((f50, f95))
        print(f"| {k} | {sm['p50_ms']:.3f} / {sm['p95_ms']:.3f} ms | "
              + (f"{sc['p50_ms']:.3f} / {sc['p95_ms']:.3f} ms | {f50:.3f} / {f95:.3f} |" if sc else "— | — |")
              + f" {'yes' if ok else 'no: correctness failed (' + ('CedarDB' if m and m.get('correct') else 'MorletDB') + ')'} |")
    if len(valid) >= 2 and all(a >= 1.1 and b >= 1.1 for a, b in valid):
        print("\nStatement: MorletDB faster (≥ 10% at p50 and p95 in every valid round).")
    elif len(valid) >= 2 and all(a <= 1 / 1.1 and b <= 1 / 1.1 for a, b in valid):
        print("\nStatement: CedarDB faster.")
    else:
        print(f"\nStatement: none (valid rounds {len(valid)}; the rule needs ≥ 10% at both p50 and p95 in each).")
    for d in dirs:
        loads, foreign = machine(d)
        print(f"{d}: snapshot load median {statistics.median(loads):.1f} (max {max(loads):.1f}); foreign >50%: {foreign}")


if __name__ == "__main__":
    if sys.argv[1] == "pair":
        pair(sys.argv[2], sys.argv[3])
    else:
        b319(sys.argv[2:])
