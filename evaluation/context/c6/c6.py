#!/usr/bin/env python3
"""C6: does resembling another operating condition signal degradation? (PROTOCOL.md)

  run C5_OUT [--out C6_OUT]   reads C5's per-measurement values (C5_OUT/values/BXX.npz, written by
                              c5.py values) and writes C6_OUT/c6.json (default C5_OUT/c6)

Run with the compression environment (numpy), python -I. Synthetic checks: test_c6.py.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "c5"))
import c5  # noqa: E402

# Frozen (PROTOCOL.md).
LIBRARY = c5.CALIB_MEASUREMENTS   # a bearing's first 150 measurements are its healthy library
MIN_MEMBERS = 5                   # a context is eligible with >= 5 library members
MIN_CONTEXTS = 3                  # the own context and >= 2 other eligible contexts
GATE_FACTOR = 0.5                 # healthy flag rate must be <= half the chance rate
MIN_GATE, MIN_SIDE, MIN_UNITS = 20, 20, 5
LATE = 0.25                       # late = last quarter of post-library time
EFFECT = 0.10                     # enrichment that counts as a useful clue
SWAP_FRACTION = 0.10
PAUSE_S = 600


def running_time(t):
    """As C5's change after freeze 2: pauses (gaps > 10 min) shortened to the median interval."""
    t = np.asarray(t, float)
    d = np.diff(t)
    d[d > PAUSE_S] = np.median(d)
    return np.concatenate([[t[0]], t[0] + np.cumsum(d)])


# -- resemblance ----------------------------------------------------------------------
def scales(lib):
    """|library median| per score value, as morlet_history_neighbors; zero or negligible medians skipped."""
    med = np.median(lib, axis=0)
    floor = abs(med[c5.RMS]) * c5.NEGLIGIBLE_FRACTION
    return {j: float(abs(med[j])) for j in c5.SCORE if abs(med[j]) > (floor if c5.IN_CHANNEL_UNITS[j] else 0.0)}


def distances(x, lib, sc):
    """morlet_history_neighbors' distance from x to every library row: RMS over usable score values
    of (x - row) / |median|."""
    js = sorted(sc)
    z = (np.asarray(x)[js] - lib[:, js]) / np.array([sc[j] for j in js])
    return np.sqrt(np.mean(z ** 2, axis=1))


def resemblance(x, own, lib, lib_ctx, sc):
    """S = log(D(own) / min over other contexts of D(c)), D(c) = median distance to the library members
    of context c, over eligible contexts. None unless the own context and >= 2 others are eligible.
    S > 0: x resembles another condition more than its own."""
    if not sc:
        return None
    d = distances(x, lib, sc)
    D = {}
    for c in sorted(set(lib_ctx.tolist())):
        m = lib_ctx == c
        if m.sum() >= MIN_MEMBERS:
            D[c] = float(np.median(d[m]))
    if own not in D or len(D) < MIN_CONTEXTS:
        return None
    other = min((c for c in D if c != own), key=lambda c: (D[c], c))
    if D[own] <= 0 or D[other] <= 0:
        return None
    return {"S": float(np.log(D[own] / D[other])), "other": other, "K": len(D), "eligible": sorted(D)}


# -- one bearing ----------------------------------------------------------------------
def gate(v, ctx):
    """Leave-one-out over the library: how often does a healthy measurement resemble another condition?"""
    flags, chance = [], []
    for i in range(LIBRARY):
        keep = np.arange(LIBRARY) != i
        r = resemblance(v[i], ctx[i], v[:LIBRARY][keep], ctx[:LIBRARY][keep], scales(v[:LIBRARY][keep]))
        if r:
            flags.append(r["S"] > 0)
            chance.append((r["K"] - 1) / r["K"])
    out = {"items": len(flags)}
    if flags:
        out.update(flag_rate=float(np.mean(flags)), chance=float(np.mean(chance)))
    out["pass"] = bool(len(flags) >= MIN_GATE and out["flag_rate"] <= GATE_FACTOR * out["chance"])
    return out


def split(flag, use, late):
    early, lt = use & ~late, use & late
    out = {"early": int(early.sum()), "late": int(lt.sum())}
    if early.sum() >= MIN_SIDE and lt.sum() >= MIN_SIDE:
        out.update(rate_early=float(flag[early].mean()), rate_late=float(flag[lt].mean()))
        out["enrichment"] = out["rate_late"] - out["rate_early"]
    return out


def direction(own, other, mask):
    """Among masked flagged measurements: does the resembled context have a higher load / speed band?"""
    idx = [i for i in np.flatnonzero(mask) if other[i] is not None]
    if not idx:
        return {"n": 0}
    o = [ast.literal_eval(own[i]) for i in idx]
    r = [ast.literal_eval(other[i]) for i in idx]
    load = np.array([b[1] + b[2] - (a[1] + a[2]) for a, b in zip(o, r)])
    speed = np.array([b[0] - a[0] for a, b in zip(o, r)])
    return {"n": len(idx), "load_higher": float(np.mean(load > 0)), "load_lower": float(np.mean(load < 0)),
            "speed_higher": float(np.mean(speed > 0)), "speed_lower": float(np.mean(speed < 0))}


def unit(d, channel=0):
    v, ctx, t = d["values"][channel], d["context"], d["t"]
    n = len(t)
    res = {"measurements": int(n), "contexts_in_library": int(len(set(ctx[:LIBRARY].tolist())))}
    if n <= LIBRARY + 2 * MIN_SIDE:
        return res
    res["gate"] = gate(v, ctx)
    lib, lib_ctx = v[:LIBRARY], ctx[:LIBRARY]
    sc = scales(lib)
    S, other, elig = np.full(n, np.nan), [None] * n, [None] * n
    for i in range(LIBRARY, n):
        r = resemblance(v[i], ctx[i], lib, lib_ctx, sc)
        if r:
            S[i], other[i], elig[i] = r["S"], r["other"], r["eligible"]
    t_lib = t[LIBRARY - 1]
    late = t >= t_lib + (1 - LATE) * (t[-1] - t_lib)
    scored, flag = np.isfinite(S), np.nan_to_num(S, nan=-np.inf) > 0
    res["scored"] = int(scored.sum())
    res["Q1"] = split(flag, scored, late)
    # Q2: only measurements the C5 candidate (H-CTX, its frozen alarm threshold, smoothed) does not flag.
    h = c5.history_scores(v, ctx, c5.SCORE, True)[0]
    al = c5.alarm(h, t, t[-1])
    m = np.flatnonzero(np.isfinite(h))
    smooth = np.full(n, np.nan)
    smooth[m] = c5.causal_median(h[m])
    quiet = np.isfinite(smooth) & (smooth <= al["threshold"]) if "threshold" in al else np.zeros(n, bool)
    res["Q2"] = split(flag, scored & quiet, late) | {"h_ctx_threshold": al.get("threshold")}
    res["direction"] = {"late flagged": direction(ctx, other, flag & late), "early flagged": direction(ctx, other, flag & ~late)}
    # Reported: deliberately wrong context on 10% of early scored measurements.
    pool = np.flatnonzero(scored & ~late)
    rng = np.random.default_rng(c5.SEED)
    pick = rng.choice(pool, max(1, int(SWAP_FRACTION * len(pool))), replace=False) if len(pool) else []
    swapped = []
    for i in pick:
        wrong = rng.choice([c for c in elig[i] if c != ctx[i]])
        r = resemblance(v[i], wrong, lib, lib_ctx, sc)
        swapped.append(r is not None and r["S"] > 0)
    res["swap"] = {"n": len(swapped), "flag_rate_swapped": float(np.mean(swapped)) if swapped else None,
                   "flag_rate_true": float(flag[pick].mean()) if len(pick) else None}
    return res


# -- decisions ------------------------------------------------------------------------
def decide(rows, units, q):
    """Mean within-bearing enrichment, 95% bootstrap CI over bearings, frozen thresholds."""
    used = [u for u in units if rows[u].get("gate", {}).get("pass") and "enrichment" in rows[u].get(q, {})]
    out = {"question": q, "units": used, "gate_failed": [u for u in units if not rows[u].get("gate", {}).get("pass")]}
    if len(used) < MIN_UNITS:
        return out | {"verdict": "not testable here"}
    e = np.array([rows[u][q]["enrichment"] for u in used])
    mean, ci = c5.bootstrap_diff(e, np.zeros(len(e)))
    verdict = ("supported" if mean >= EFFECT and ci[0] > 0 else "not supported" if ci[1] < EFFECT
               else "inconclusive")
    return out | {"mean_enrichment": mean, "ci95": ci, "verdict": verdict}


def cmd_run(a):
    out = a.out or a.c5_out / "c6"
    out.mkdir(parents=True, exist_ok=True)
    res = {"channel A": {}, "channel C": {}}
    for ch, label in ((0, "channel A"), (1, "channel C")):
        for b in c5.EXPERIMENTS:
            res[label][b] = unit(dict(np.load(a.c5_out / "values" / f"{b}.npz")), ch)
    rows = res["channel A"]
    res["decisions"] = {"Q1": decide(rows, c5.PRIMARY, "Q1"), "Q2": decide(rows, c5.PRIMARY, "Q2")}
    res["reported"] = {
        "channel C": {q: decide(res["channel C"], c5.PRIMARY, q) for q in ("Q1", "Q2")},
        "all 17": {q: decide(rows, c5.EXPERIMENTS, q) for q in ("Q1", "Q2")},
    }
    run = {}
    for b in c5.EXPERIMENTS:
        d = dict(np.load(a.c5_out / "values" / f"{b}.npz"))
        run[b] = unit(dict(d, t=running_time(d["t"])))
    res["running time"] = run
    res["reported"]["running time"] = {q: decide(run, c5.PRIMARY, q) for q in ("Q1", "Q2")}
    (out / "c6.json").write_text(json.dumps(res, indent=1))
    print("bearing  gate(rate/chance)   Q1 early->late        Q2 early->late")
    for b, r in rows.items():
        g = r.get("gate", {})
        cells = [f"{r[q]['rate_early']:.2f}->{r[q]['rate_late']:.2f}" if "enrichment" in r.get(q, {}) else "-"
                 for q in ("Q1", "Q2")]
        gs = f"{g['flag_rate']:.2f}/{g['chance']:.2f}{'' if g['pass'] else ' FAIL'}" if "flag_rate" in g else "-"
        print(f"{b:8} {gs:19} {cells[0]:21} {cells[1]}")
    print(json.dumps(res["decisions"], indent=1))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("c5_out", type=Path)
    r.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
