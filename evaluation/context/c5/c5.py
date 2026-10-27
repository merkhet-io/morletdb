#!/usr/bin/env python3
"""C5: like-for-like own history vs plain RMS on Paderborn run-to-failure bearings (PROTOCOL.md).

  values DATA_DIR OUT [--only B02,B03]   simple-v1 values of channels A and C per measurement,
                                         with time, context and temperature -> OUT/values/BXX.npz
  femto FEMTO_ROOT OUT                   the same for the FEMTO learning set (harness check only)
  run OUT                                arms, alarms and the frozen decisions -> OUT/c5.json

Run with the compression environment (numpy, scipy; h5py for MATLAB v7.3 files), python -I.
Synthetic checks: test_c5.py.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# simple-v1 (morlet_core::values), in order.
NAMES = ("duration_s", "mean", "std", "rms", "p75", "peak_abs", "crest", "kurtosis")
IN_CHANNEL_UNITS = (False, True, True, True, True, True, False, False)
RMS = 3
NEGLIGIBLE_FRACTION = 1e-9
SCORE = (0, 2, 6, 7)          # product default score_values: duration_s, std, crest, kurtosis
SCORE_NO_CREST = (0, 2, 7)
MIN_HISTORY = 5

# Frozen alarm rule (PROTOCOL.md, as W1, with a calibration in measurements).
SMOOTH, CALIB_MEASUREMENTS, CALIB_MIN_SCORED, K_MAD, PERSIST, STABLE = 5, 150, 30, 6.0, 6, 0.5
RESAMPLES, SEED = 2000, 7

EXPERIMENTS = [f"B{i:02d}" for i in range(1, 18)]
NO_CRITERION = {"B01", "B05", "B16"}
PRIMARY = [b for b in EXPERIMENTS if b not in NO_CRITERION]
TEMPERATURE_ENDED = ["B03", "B07", "B08", "B15"]
EXCITATION = {b: "sine" if int(b[1:]) <= 6 else "noise" for b in EXPERIMENTS}


def rate_hz(experiment):
    return 128000.0 if int(experiment[1:]) <= 9 else 64000.0


# -- values ------------------------------------------------------------------------
def seq_sum(a):
    """Left-to-right float64 sum, as Rust's Iterator::sum (numpy's sum is pairwise)."""
    return np.cumsum(a)[-1]


def simple_values(x, rate):
    """simple-v1 values of one segment, as morlet_core::values::simple."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0 or not np.isfinite(x).all() or np.abs(x).max() > 1e9:
        raise ValueError("segment rejected by simple-v1")
    n = x.size
    mean = seq_sum(x) / n
    d = x - mean
    d2 = d * d
    m2 = seq_sum(d2) / n
    m4 = seq_sum(d2 * d2) / n
    rms = np.sqrt(seq_sum(x * x) / n)
    peak = float(np.max(np.abs(x)))
    s = np.sort(x)
    pos = 0.75 * (n - 1)
    lo, frac = int(np.floor(pos)), pos - np.floor(pos)
    p75 = s[lo] + frac * (s[lo + 1] - s[lo]) if lo + 1 < n else s[lo]
    return [n / rate, mean, np.sqrt(m2), rms, p75, peak,
            peak / rms if rms > 0 else 0.0, m4 / (m2 * m2) if m2 > 0 else 0.0]


def context_key(speed, stat_load, dyn_load, excitation):
    return (int(np.floor((speed - 1500) / 500)), int(np.floor(stat_load / 500)),
            int(np.floor(dyn_load / 500)), excitation)


# -- loading -----------------------------------------------------------------------
def load_mat(path):
    """Channels A and C of one measurement, as stored (volts)."""
    try:
        from scipy.io import loadmat
        d = loadmat(path, variable_names=["accHorizRear_A", "accHorizFrontal_C"])
        return np.ravel(d["accHorizRear_A"]).astype(np.float64), np.ravel(d["accHorizFrontal_C"]).astype(np.float64)
    except NotImplementedError:  # MATLAB v7.3 (HDF5)
        import h5py
        with h5py.File(path, "r") as f:
            return np.ravel(f["accHorizRear_A"][()]).astype(np.float64), np.ravel(f["accHorizFrontal_C"][()]).astype(np.float64)


def parse_time(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S.%f", "%d-%b-%Y %H:%M:%S",
                "%d.%m.%Y %H:%M:%S.%f", "%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    raise ValueError(f"unrecognised dateTime {s!r}")


def read_table(path):
    """CSV rows keyed by column name up to ' /' (e.g. 'setSpeed / rpm' -> 'setSpeed')."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    head = [h.split(" /")[0].strip() for h in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:] if r]


def find_one(root: Path, pattern):
    hits = sorted(root.rglob(pattern))
    if len(hits) != 1:
        raise FileNotFoundError(f"{pattern}: {len(hits)} matches under {root}")
    return hits[0]


def measurement_files(root: Path, experiment):
    num = re.compile(rf"data_{experiment}_M(\d+)\.mat$")
    files = [(int(m.group(1)), p) for p in root.rglob(f"data_{experiment}_M*.mat") if (m := num.search(p.name))]
    files.sort()
    if len({n for n, _ in files}) != len(files):
        raise ValueError(f"{experiment}: duplicate measurement numbers")
    return [p for _, p in files]


def experiment_values(root: Path, experiment):
    """Values per measurement, aligned with operatingConditions rows (k-th file <-> k-th row)."""
    files = measurement_files(root, experiment)
    oc = read_table(find_one(root, f"{experiment}_operatingConditions.csv"))
    if len(files) != len(oc):
        raise ValueError(f"{experiment}: {len(files)} .mat files but {len(oc)} operating-condition rows")
    t = np.array([parse_time(r["dateTime"]) for r in oc])
    if not (np.diff(t) > 0).all():
        raise ValueError(f"{experiment}: measurement times are not increasing")
    temps = read_table(find_one(root, f"{experiment}_meanTemperatures.csv"))
    tt = np.array([parse_time(r["dateTime"]) for r in temps])
    t1 = np.array([float(r["Mean Abs. Temp. T1"]) for r in temps])
    rate = rate_hz(experiment)
    vals = np.empty((2, len(files), len(NAMES)))
    for i, p in enumerate(files):
        a, c = load_mat(p)
        vals[0, i], vals[1, i] = simple_values(a, rate), simple_values(c, rate)
    ctx = [context_key(float(r["setSpeed"]), float(r["setStatLoad"]), float(r["setDynLoad"]), EXCITATION[experiment])
           for r in oc]
    temp = np.interp(t, tt, t1) if len(tt) else np.full(len(t), np.nan)
    return {"t": t, "values": vals, "context": np.array([repr(c) for c in ctx]), "temperature": temp,
            "samples": np.array([len(load_mat(files[0])[0])]), "rate": np.array([rate])}


# -- arms ---------------------------------------------------------------------------
class History:
    """Sorted per-value history of one context, for exact medians as percentile_cont(0.5)."""

    def __init__(self):
        self.cols = [[] for _ in NAMES]

    def __len__(self):
        return len(self.cols[0])

    def median(self, j):
        c = self.cols[j]
        n = len(c)
        return c[n // 2] if n % 2 else (c[n // 2 - 1] + c[n // 2]) / 2.0

    def add(self, v):
        for j, x in enumerate(v):
            bisect.insort(self.cols[j], float(x))


def relative_deviations(x, hist: History, which):
    """|x - med| / |med| per value in which; None where the median is zero or negligible (pg_morlet 0.3.1)."""
    floor = abs(hist.median(RMS)) * NEGLIGIBLE_FRACTION
    out = {}
    for j in which:
        med = hist.median(j)
        f = floor if IN_CHANNEL_UNITS[j] else 0.0
        out[j] = abs(x[j] - med) / abs(med) if abs(med) > f else None
    return out


def history_scores(values, contexts, which, by_context):
    """Per segment: max relative deviation over `which` against earlier segments (same context if
    by_context), the value behind it, or NaN with fewer than MIN_HISTORY earlier segments."""
    hists, score, top = {}, np.full(len(values), np.nan), np.full(len(values), -1)
    for i, (v, c) in enumerate(zip(values, contexts)):
        h = hists.setdefault(c if by_context else None, History())
        if len(h) >= MIN_HISTORY:
            dev = {j: d for j, d in relative_deviations(v, h, which).items() if d is not None}
            if dev:
                j = max(dev, key=dev.get)
                score[i], top[i] = dev[j], j
        h.add(v)
    return score, top


def arms(d, channel=0):
    v, ctx = d["values"][channel], d["context"]
    out = {}
    out["H-CTX"], out["H-CTX_top"] = history_scores(v, ctx, SCORE, True)
    out["H-ALL"], out["H-ALL_top"] = history_scores(v, ctx, SCORE, False)
    out["B-RMS"] = v[:, RMS].copy()
    out["H-RMS"], _ = history_scores(v, ctx, (RMS,), True)
    out["H-CTX-no-crest"], _ = history_scores(v, ctx, SCORE_NO_CREST, True)
    return out


# -- alarm rule -----------------------------------------------------------------------
def causal_median(v, w=SMOOTH):
    return np.array([np.median(v[max(0, i - w + 1):i + 1]) for i in range(len(v))])


def alarm(raw, t, t_end):
    """Frozen rule over scored segments (non-NaN raw), measurement times t, end of life t_end."""
    raw, t = np.asarray(raw, float), np.asarray(t, float)
    measurement = np.flatnonzero(np.isfinite(raw))
    res = {"scored": int(len(measurement)), "alarm": None, "stable": False, "lead": 0.0}
    if len(measurement) == 0:
        return res
    v, ts = causal_median(raw[measurement]), t[measurement]
    ncal = int(np.sum(measurement < CALIB_MEASUREMENTS))
    res["calibration_extended"] = ncal < CALIB_MIN_SCORED
    ncal = max(ncal, CALIB_MIN_SCORED)
    res["calibration"] = ncal
    if len(v) < ncal + PERSIST:
        return res
    cal = v[:ncal]
    med = float(np.median(cal))
    thr = med + K_MAD * 1.4826 * float(np.median(np.abs(cal - med)))
    res["threshold"] = thr
    above = v > thr
    t_cal = ts[ncal - 1]
    for k in range(ncal, len(v) - PERSIST + 1):
        if above[k:k + PERSIST].all():
            stable = bool(above[k:].mean() >= STABLE)
            res.update(alarm=int(measurement[k]), stable=stable, fraction_above_after=float(above[k:].mean()),
                       minutes_after_calibration=float((ts[k] - t_cal) / 60),
                       life_fraction=float((ts[k] - t[0]) / (t_end - t[0])),
                       lead=float((t_end - ts[k]) / (t_end - t_cal)) if stable else 0.0)
            return res
    return res


# -- analysis -------------------------------------------------------------------------
def bootstrap_diff(a, b, resamples=RESAMPLES, seed=SEED):
    a, b = np.asarray(a, float), np.asarray(b, float)
    idx = np.random.default_rng(seed).integers(0, len(a), (resamples, len(a)))
    d = (a[idx] - b[idx]).mean(1)
    return float((a - b).mean()), [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def decide(rows, units, cand, comp):
    lc = [rows[u][cand]["lead"] for u in units]
    lb = [rows[u][comp]["lead"] for u in units]
    unstable = {m: sum(rows[u][m]["alarm"] is not None and not rows[u][m]["stable"] for u in units) for m in (cand, comp)}
    delta, ci = bootstrap_diff(lc, lb)
    supported = delta >= 0.05 and ci[0] > 0 and unstable[cand] <= unstable[comp] + 1
    verdict = "supported" if supported else "not supported" if ci[1] < 0.05 else "inconclusive"
    return {"candidate": cand, "comparator": comp, "units": len(units), "delta": delta, "ci95": ci,
            "mean_lead": {cand: float(np.mean(lc)), comp: float(np.mean(lb))}, "unstable": unstable,
            "verdict": verdict}


def unit_rows(d, channel=0):
    a = arms(d, channel)
    t = d["t"]
    rows = {}
    for k in ("H-CTX", "H-ALL", "B-RMS", "H-RMS", "H-CTX-no-crest"):
        r = alarm(a[k], t, t[-1])
        if r["alarm"] is not None:
            r["temperature_at_alarm"] = float(d["temperature"][r["alarm"]])
            if k + "_top" in a:
                r["top_value"] = NAMES[int(a[k + "_top"][r["alarm"]])]
        rows[k] = r
    rows["contexts"] = int(len(set(d["context"].tolist())))
    rows["measurements"] = int(len(t))
    rows["hours"] = float((t[-1] - t[0]) / 3600)
    return rows


def cmd_values(a):
    out = a.out / "values"
    out.mkdir(parents=True, exist_ok=True)
    for b in (a.only.split(",") if a.only else EXPERIMENTS):
        path = out / f"{b}.npz"
        if path.exists():
            continue
        d = experiment_values(a.data, b)
        np.savez(path, **d)
        print(b, d["values"].shape[1], "measurements", len(set(d["context"].tolist())), "contexts", flush=True)


def femto_values(root: Path, name):
    files = sorted((root / "Learning_set" / name).glob("acc_*.csv"))
    vals = []
    for f in files:
        sep = ";" if ";" in f.read_text().splitlines()[0] else ","
        x = np.loadtxt(f, delimiter=sep, usecols=(4, 5))
        vals.append([simple_values(x[:, 0], 25600.0), simple_values(x[:, 1], 25600.0)])
    vals = np.transpose(np.array(vals), (1, 0, 2))
    n = vals.shape[1]
    return {"t": np.arange(n) * 10.0, "values": vals, "context": np.array(["const"] * n),
            "temperature": np.full(n, np.nan), "samples": np.array([2560]), "rate": np.array([25600.0])}


def cmd_femto(a):
    out = a.out / "femto"
    out.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name in ("Bearing1_1", "Bearing1_2", "Bearing2_1", "Bearing2_2", "Bearing3_1", "Bearing3_2"):
        path = out / f"{name}.npz"
        if not path.exists():
            np.savez(path, **femto_values(a.data, name))
        d = dict(np.load(path))
        rows[name] = unit_rows(d)
    (out / "femto.json").write_text(json.dumps(rows, indent=1))
    print_rows(rows)


def print_rows(rows):
    keys = ("H-CTX", "H-ALL", "B-RMS", "H-RMS", "H-CTX-no-crest")
    print("unit".ljust(12) + "".join(k.rjust(16) for k in keys))
    for u, r in rows.items():
        cells = ["-" if r[k]["alarm"] is None else f"{r[k]['lead']:.2f}{'' if r[k]['stable'] else 'u'}"
                 f" @{r[k]['minutes_after_calibration']:.0f}m" for k in keys]
        print(u.ljust(12) + "".join(c.rjust(16) for c in cells))
    print("cells: stable lead, 'u' = unstable (lead 0), @ minutes after calibration; '-' = no alarm")


def cmd_run(a):
    res = {"channel A": {}, "channel C": {}}
    for ch, label in ((0, "channel A"), (1, "channel C")):
        for b in EXPERIMENTS:
            d = dict(np.load(a.out / "values" / f"{b}.npz"))
            res[label][b] = unit_rows(d, ch)
    rows = res["channel A"]
    res["decisions"] = {"Q1": decide(rows, PRIMARY, "H-CTX", "B-RMS"), "Q2": decide(rows, PRIMARY, "H-CTX", "H-ALL")}
    res["reported"] = {
        "temperature-ended": {q: decide(rows, TEMPERATURE_ENDED, "H-CTX", c) for q, c in (("Q1", "B-RMS"), ("Q2", "H-ALL"))},
        "no-crest vs B-RMS": decide(rows, PRIMARY, "H-CTX-no-crest", "B-RMS"),
        "all 17": {q: decide(rows, EXPERIMENTS, "H-CTX", c) for q, c in (("Q1", "B-RMS"), ("Q2", "H-ALL"))},
        "channel C": {q: decide(res["channel C"], PRIMARY, "H-CTX", c) for q, c in (("Q1", "B-RMS"), ("Q2", "H-ALL"))},
        "H-RMS vs B-RMS": decide(rows, PRIMARY, "H-RMS", "B-RMS"),
    }
    (a.out / "c5.json").write_text(json.dumps(res, indent=1))
    print_rows(rows)
    print(json.dumps(res["decisions"], indent=1))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("values")
    v.add_argument("data", type=Path)
    v.add_argument("out", type=Path)
    v.add_argument("--only")
    f = sub.add_parser("femto")
    f.add_argument("data", type=Path)
    f.add_argument("out", type=Path)
    r = sub.add_parser("run")
    r.add_argument("out", type=Path)
    a = p.parse_args(argv)
    {"values": cmd_values, "femto": cmd_femto, "run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
