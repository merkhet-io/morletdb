#!/usr/bin/env python3
"""C7: pinned healthy baseline vs all earlier history on XJTU-SY bearings (PROTOCOL.md).

  values DATA_DIR OUT [--only Bearing1_1,...]   simple-v1 values of both channels per snapshot
                                                 -> OUT/values/<bearing>.npz
  run OUT                                        arms, alarms and the frozen decisions -> OUT/c7.json
  paderborn C5_OUT [--out DIR]                   exploratory H-PIN on consumed Paderborn values
                                                 (only after the XJTU-SY verdicts are committed)

Run with the compression environment (numpy), python -I. Synthetic checks: test_c7.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "c5"))
import c5  # noqa: E402

RATE = 25600.0
SAMPLES = 32768
INTERVAL_S = 60.0
PIN = 30                      # snapshots in the pin = the calibration window
PADERBORN_PIN = 150           # measurements (C5's calibration window)
LATE = 0.9                    # alarms at or after this fraction of life are "late"
BEARINGS = [f"Bearing{c}_{i}" for c in (1, 2, 3) for i in range(1, 6)]
CHANNELS = ("horizontal", "vertical")


# -- values ---------------------------------------------------------------------------
def bearing_dir(root: Path, bearing):
    found = [p for p in root.rglob(bearing) if p.is_dir()]
    if len(found) != 1:
        raise SystemExit(f"{bearing}: expected one directory under {root}, found {len(found)}")
    return found[0]


def snapshot_files(d: Path):
    files = [p for p in d.iterdir() if re.fullmatch(r"\d+\.csv", p.name)]
    files.sort(key=lambda p: int(p.stem))
    numbers = [int(p.stem) for p in files]
    if numbers != list(range(1, len(files) + 1)):
        raise SystemExit(f"{d}: snapshot numbers are not 1..{len(files)}")
    return files


def load_snapshot(path: Path):
    """Two columns (horizontal, vertical); a non-numeric first line is a header."""
    with open(path, encoding="ascii") as f:
        first = f.readline()
    skip = 0 if re.match(r"\s*[-+0-9.]", first) else 1
    x = np.loadtxt(path, delimiter=",", skiprows=skip, dtype=np.float64)
    if x.shape != (SAMPLES, 2):
        raise SystemExit(f"{path}: shape {x.shape}, expected ({SAMPLES}, 2)")
    return x[:, 0], x[:, 1]


def bearing_values(root: Path, bearing):
    files = snapshot_files(bearing_dir(root, bearing))
    vals = np.empty((2, len(files), len(c5.NAMES)))
    for i, p in enumerate(files):
        h, v = load_snapshot(p)
        vals[0, i], vals[1, i] = c5.simple_values(h, RATE), c5.simple_values(v, RATE)
    return {"values": vals, "t": np.arange(len(files)) * INTERVAL_S}


# -- arms -----------------------------------------------------------------------------
def history_of(values):
    h = c5.History()
    for v in values:
        h.add(v)
    return h


def score_against(v, h, which):
    """Product summary score: max relative deviation over `which`, the value behind it; NaN if none."""
    if len(h) < c5.MIN_HISTORY:
        return np.nan, -1
    dev = {j: d for j, d in c5.relative_deviations(v, h, which).items() if d is not None}
    if not dev:
        return np.nan, -1
    j = max(dev, key=dev.get)
    return dev[j], j


def pinned_scores(values, which, pin=PIN):
    """H-PIN: pinned snapshots against the other pinned ones, later snapshots against the pin."""
    n = len(values)
    score, top = np.full(n, np.nan), np.full(n, -1)
    pinned = values[:pin]
    for i in range(min(pin, n)):
        score[i], top[i] = score_against(values[i], history_of(np.delete(pinned, i, axis=0)), which)
    h = history_of(pinned)
    for i in range(pin, n):
        score[i], top[i] = score_against(values[i], h, which)
    return score, top


def threshold(raw, calibration):
    """The alarm rule's threshold over the first `calibration` snapshots (scored ones, extended as c5.alarm)."""
    scored = np.flatnonzero(np.isfinite(raw))
    sm = c5.causal_median(raw[scored])
    ncal = max(int(np.sum(scored < calibration)), c5.CALIB_MIN_SCORED)
    cal = sm[:ncal]
    med = float(np.median(cal))
    return med + c5.K_MAD * 1.4826 * float(np.median(np.abs(cal - med)))


def gated_scores(values, which, pin=PIN):
    """H-GATED: the pin plus later snapshots whose smoothed H-GATED indicator was at or below the
    threshold. Inside the pin it equals H-PIN, so the threshold is H-PIN's calibration threshold."""
    pinned, pinned_top = pinned_scores(values, which, pin)
    score, top = pinned.copy(), pinned_top.copy()
    if len(values) <= pin:
        return score, top
    thr = threshold(pinned, pin)
    n = len(values)
    h = history_of(values[:pin])
    recent = [s for s in score[:pin] if np.isfinite(s)][-c5.SMOOTH:]
    for i in range(pin, n):
        score[i], top[i] = score_against(values[i], h, which)
        if not np.isfinite(score[i]):
            continue
        recent = (recent + [score[i]])[-c5.SMOOTH:]
        if np.median(recent) <= thr:
            h.add(values[i])
    return score, top


def all_history_scores(values, which):
    return c5.history_scores(values, np.zeros(len(values), dtype=int), which, False)


def arms(values):
    out = {}
    out["H-PIN"], out["H-PIN_top"] = pinned_scores(values, c5.SCORE)
    out["H-ALL"], out["H-ALL_top"] = all_history_scores(values, c5.SCORE)
    out["B-RMS"] = values[:, c5.RMS].copy()
    out["H-GATED"], out["H-GATED_top"] = gated_scores(values, c5.SCORE)
    out["H-PIN-no-crest"], _ = pinned_scores(values, c5.SCORE_NO_CREST)
    out["H-ALL-no-crest"], _ = all_history_scores(values, c5.SCORE_NO_CREST)
    return out


ARMS = ("H-PIN", "H-ALL", "B-RMS", "H-GATED", "H-PIN-no-crest", "H-ALL-no-crest")


def share_above_after(raw, res, after):
    """Share of scored snapshots after index `after` whose smoothed indicator exceeds the arm's threshold."""
    if "threshold" not in res:
        return None
    scored = np.flatnonzero(np.isfinite(raw))
    sm = c5.causal_median(raw[scored])
    later = scored > after
    return float((sm[later] > res["threshold"]).mean()) if later.any() else None


def unit_rows(values, t, calibration=PIN, arm_ids=ARMS, armset=None):
    a = armset if armset is not None else arms(values)
    rows = {}
    for k in arm_ids:
        r = c5.alarm(a[k], t, t[-1], calibration)
        if r["alarm"] is not None and k + "_top" in a:
            r["top_value"] = c5.NAMES[int(a[k + "_top"][r["alarm"]])]
        rows[k] = r
    rms = rows.get("B-RMS")
    if rms and rms["stable"]:
        rows["absorption"] = {k: share_above_after(a[k], rows[k], rms["alarm"])
                              for k in arm_ids if k != "B-RMS"}
    rows["snapshots"] = int(len(t))
    rows["minutes"] = float((t[-1] - t[0]) / 60)
    return rows


# -- run ------------------------------------------------------------------------------
def summary(rows, units, arm_ids):
    late = {k: sum(rows[u][k]["alarm"] is not None and rows[u][k].get("life_fraction", 0) >= LATE for u in units)
            for k in arm_ids}
    absorbed = {}
    for k in arm_ids:
        if k == "B-RMS":
            continue
        xs = [rows[u]["absorption"][k] for u in units if "absorption" in rows[u] and rows[u]["absorption"][k] is not None]
        absorbed[k] = {"bearings": len(xs), "mean_share_above_after_rms_alarm": float(np.mean(xs)) if xs else None}
    return {"late_alarms": late, "absorption": absorbed}


def channel_result(per_unit, units):
    return {
        "Q1": c5.decide(per_unit, units, "H-PIN", "H-ALL"),
        "Q2": c5.decide(per_unit, units, "H-PIN", "B-RMS"),
        "no crest": {"Q1": c5.decide(per_unit, units, "H-PIN-no-crest", "H-ALL-no-crest"),
                     "Q2": c5.decide(per_unit, units, "H-PIN-no-crest", "B-RMS")},
        "gated": {"vs H-ALL": c5.decide(per_unit, units, "H-GATED", "H-ALL"),
                  "vs B-RMS": c5.decide(per_unit, units, "H-GATED", "B-RMS")},
        **summary(per_unit, units, ARMS),
    }


def cmd_values(a):
    out = a.out / "values"
    out.mkdir(parents=True, exist_ok=True)
    for b in (a.only.split(",") if a.only else BEARINGS):
        path = out / f"{b}.npz"
        if path.exists():
            continue
        d = bearing_values(a.data, b)
        np.savez(path, **d)
        print(b, d["values"].shape[1], "snapshots", flush=True)


def cmd_run(a):
    res = {"protocol": "C7", "bearings": BEARINGS, "channels": {}}
    data = {b: dict(np.load(a.out / "values" / f"{b}.npz")) for b in BEARINGS}
    for ch, name in enumerate(CHANNELS):
        per_unit = {b: unit_rows(d["values"][ch], d["t"]) for b, d in data.items()}
        res["channels"][name] = {"per_bearing": per_unit, **channel_result(per_unit, BEARINGS)}
    res["decided"] = {q: res["channels"]["horizontal"][q]["verdict"] for q in ("Q1", "Q2")}
    (a.out / "c7.json").write_text(json.dumps(res, indent=1))
    print_result(res)


def print_result(res):
    h = res["channels"]["horizontal"]
    print(f"{'bearing':<12}" + "".join(f"{k:>16}" for k in ARMS[:4]))
    for b, r in h["per_bearing"].items():
        cells = []
        for k in ARMS[:4]:
            x = r[k]
            cells.append("-" if x["alarm"] is None else f"{x['lead']:.2f}" + ("" if x["stable"] else "u"))
        print(f"{b:<12}" + "".join(f"{c:>16}" for c in cells))
    for q in ("Q1", "Q2"):
        d = h[q]
        print(q, d["candidate"], "vs", d["comparator"], f"delta {d['delta']:+.3f}",
              f"CI [{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]", "unstable", d["unstable"], "->", d["verdict"])


# -- exploratory: consumed Paderborn --------------------------------------------------
def pinned_context_scores(values, contexts, which, pin=PADERBORN_PIN):
    """H-PIN restricted to the query's context: pinned measurements of the same context (other than
    the query itself), scored with at least MIN_HISTORY of them."""
    n = len(values)
    score, top = np.full(n, np.nan), np.full(n, -1)
    members = {}
    for i in range(min(pin, n)):
        members.setdefault(contexts[i], []).append(i)
    hists = {c: history_of(values[idx]) for c, idx in members.items()}
    for i in range(n):
        idx = members.get(contexts[i], [])
        if i < pin:
            others = [j for j in idx if j != i]
            if len(others) >= c5.MIN_HISTORY:
                score[i], top[i] = score_against(values[i], history_of(values[others]), which)
        elif len(idx) >= c5.MIN_HISTORY:
            score[i], top[i] = score_against(values[i], hists[contexts[i]], which)
    return score, top


def cmd_paderborn(a):
    out = a.out or a.c5_out / "c7"
    out.mkdir(parents=True, exist_ok=True)
    per_unit = {}
    for b in c5.PRIMARY:
        d = dict(np.load(a.c5_out / "values" / f"{b}.npz"))
        v, ctx, t = d["values"][0], d["context"], d["t"]
        armset = {}
        armset["H-PIN"], armset["H-PIN_top"] = pinned_context_scores(v, ctx, c5.SCORE)
        armset["H-CTX"], armset["H-CTX_top"] = c5.history_scores(v, ctx, c5.SCORE, True)
        armset["B-RMS"] = v[:, c5.RMS].copy()
        per_unit[b] = unit_rows(v, t, c5.CALIB_MEASUREMENTS, ("H-PIN", "H-CTX", "B-RMS"), armset)
        per_unit[b]["pin_scored"] = int(np.isfinite(armset["H-PIN"]).sum())
    res = {"protocol": "C7 exploratory (consumed Paderborn, decides nothing)", "per_bearing": per_unit,
           "H-PIN vs H-CTX": c5.decide(per_unit, c5.PRIMARY, "H-PIN", "H-CTX"),
           "H-PIN vs B-RMS": c5.decide(per_unit, c5.PRIMARY, "H-PIN", "B-RMS"),
           **summary(per_unit, c5.PRIMARY, ("H-PIN", "H-CTX", "B-RMS"))}
    (out / "c7_paderborn.json").write_text(json.dumps(res, indent=1))
    for b, r in per_unit.items():
        print(b, {k: (None if r[k]["alarm"] is None else round(r[k]["lead"], 2), r[k]["stable"])
                  for k in ("H-PIN", "H-CTX", "B-RMS")}, "pin scored", r["pin_scored"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("values")
    v.add_argument("data", type=Path)
    v.add_argument("out", type=Path)
    v.add_argument("--only")
    r = sub.add_parser("run")
    r.add_argument("out", type=Path)
    x = sub.add_parser("paderborn")
    x.add_argument("c5_out", type=Path)
    x.add_argument("--out", type=Path)
    a = p.parse_args(argv)
    {"values": cmd_values, "run": cmd_run, "paderborn": cmd_paderborn}[a.cmd](a)


if __name__ == "__main__":
    main()
