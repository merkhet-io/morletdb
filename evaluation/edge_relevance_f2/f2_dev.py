#!/usr/bin/env python3
"""F2 development exploration (not a frozen study): approximation at small sizes and label-free surprise.

Development data only: CWRU 12 kHz drive end (normal 97-100; inner, ball, outer@6 faults 0.007-0.021 in), PyScrew
s01, the CX development gallery of 2025-03 with the EMSC catalogue (arrivals from prep_seismic.py).

  .venv-f2/bin/python evaluation/edge_relevance_f2/f2_dev.py [cwru] [pyscrew] [seismic] [cost] --seismic-json F
"""
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import pywt
import zfpy
import h5py
import hdf5plugin
import io
from scipy.io import loadmat
from scipy.signal import decimate, hilbert, butter, sosfiltfilt
from scipy.stats import kurtosis

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import shrink as S  # noqa: E402

OUT = HERE / "results" / "dev"
PRIORS = {"gauss": ("gauss", 1.5), "laplace": ("laplace", 1.5), "pareto": ("pareto", 1.5),
          "pareto1": ("pareto", 1.0)}         # pareto1: alpha 1, Cauchy-like tail (closest to a quasi-Cauchy slab)


def fit_all(x, **kw):
    return {name: S.fit(x, kind, alpha=alpha, **kw) for name, (kind, alpha) in PRIORS.items()}
Q_GRID = (0.25, 0.5, 1.0, 2.0, 4.0)
TARGETS = (0.05, 0.1, 0.2, 0.3)

CWRU = {"normal": [97, 98, 99, 100],
        "inner": [105, 106, 107, 108, 169, 170, 171, 172, 209, 210, 211, 212],
        "ball": [118, 119, 120, 121, 185, 186, 187, 188, 222, 223, 224, 225],
        "outer": [130, 131, 132, 133, 197, 198, 199, 200, 234, 235, 236, 237]}
# 6205-2RS drive-end bearing, multiples of shaft frequency (CWRU bearing data centre).
ORDERS = {"inner": 5.4152, "outer": 3.5848, "ball": 2 * 2.357}


def auroc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    allv = np.concatenate([pos, neg])
    ranks = np.argsort(np.argsort(allv, kind="mergesort"), kind="mergesort") + 1.0
    # average ranks for ties
    _, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, ranks)
    ranks = (sums / cnt)[inv]
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def rel_err(x, r):
    return float(np.linalg.norm(x - r) / (np.linalg.norm(x - x.mean()) or 1e-12))


# ---------------------------------------------------------------- approximation methods (size, reconstruction)
def method_points(x, priors):
    """[(method, param, bytes_per_sample, reconstruction)] over each method's parameter grid."""
    pts = []
    for kind, pr in priors.items():
        for q in Q_GRID:
            for tau in (0.5, 0.9):
                r, b, _ = S.approximate(x, pr, tau=tau, q=q)
                pts.append((f"bayes-{kind}", f"q={q},tau={tau}", b, r))
                r, b, _ = S.approximate(x, pr, tau=tau, q=q, rule="select")
                pts.append((f"select-{kind}", f"q={q},tau={tau}", b, r))
    pr = priors["laplace"]
    for q in Q_GRID:
        for lam in (2.0, 3.0, 4.0, float(np.sqrt(2 * np.log(len(x))))):
            r, b, _ = S.approximate(x, pr, rule="hard", lam=lam, q=q)
            pts.append(("hard", f"q={q},lam={lam:.2f}", b, r))
    sigma = pr.sigma
    for tol in (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64, 128, 256):
        c = zfpy.compress_numpy(np.ascontiguousarray(x), tolerance=tol * sigma)
        pts.append(("zfp", f"tol={tol}", len(c) / len(x), zfpy.decompress_numpy(c)))
    for eb in (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64, 128, 256):
        b, r = sz3(x, eb * sigma)
        pts.append(("sz3", f"abs={eb}", b, r))
    for k in (2, 4, 8, 16, 32):
        for q in (0.25, 0.5, 1.0, 2.0):
            d = decimate(x, k, ftype="fir", zero_phase=True) if k > 1 else x
            step = q * sigma
            qi = np.round(d / step).astype(np.int64)
            b = S.entropy_bits(qi) / 8 / len(x)
            r = np.interp(np.arange(len(x)), np.arange(len(d)) * k, qi * step)
            pts.append(("decimate", f"k={k},q={q}", b, r))
    return pts


def sz3(x, abs_err):
    """SZ3 (hdf5plugin) with an absolute error bound: bytes per sample of the stored chunk, reconstruction."""
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        d = f.create_dataset("x", data=x, chunks=(len(x),), **hdf5plugin.SZ3(absolute=float(abs_err)))
        size = d.id.get_storage_size()
    with h5py.File(buf, "r") as f:
        r = f["x"][:]
    return size / len(x), r


def log_dev(a, b):
    """|log(a / b)|: how far a measure on the reconstruction is from its value on the original (0 = same)."""
    if not (a > 0 and b > 0) or not np.isfinite(a):
        return float("inf")
    return float(abs(np.log(a / b)))


def at_targets(rows, key):
    """Per method, the best value of `key` among points within each target size (lower is better unless the key
    starts with 'keep')."""
    out = {}
    better_high = key.startswith("keep")
    for m in sorted({r["method"] for r in rows}):
        rs = [r for r in rows if r["method"] == m]
        out[m] = {}
        for t in TARGETS:
            ok = [r[key] for r in rs if r["bps"] <= t]
            out[m][t] = (max(ok) if better_high else min(ok)) if ok else None
    return out


# ---------------------------------------------------------------- CWRU
def cwru_load(fid):
    d = loadmat(ROOT / f"data/cwru/raw/{fid}.mat")
    key = next(k for k in d if k.endswith("_DE_time"))
    rpmk = [k for k in d if k.endswith("RPM")]
    rpm = float(np.ravel(d[rpmk[0]])[0]) if rpmk else 1797.0
    return np.asarray(d[key], float).ravel(), rpm


def envelope_peak(x, fs, f0):
    """Fault-order peak in the envelope spectrum (band 2-5.5 kHz): amplitude at f0 and 2 f0 over the median."""
    sos = butter(4, [2000, 5500], btype="band", fs=fs, output="sos")
    env = np.abs(hilbert(sosfiltfilt(sos, x)))
    env -= env.mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env))))
    f = np.fft.rfftfreq(len(env), 1 / fs)
    sel = (f > 5) & (f < 600)
    base = np.median(spec[sel])
    amp = sum(spec[np.argmin(np.abs(f - h * f0))] for h in (1, 2))
    return float(amp / 2 / base)


def run_cwru():
    fs, seg = 12000, 2 ** 17
    rows, surprise_rows = [], []
    data = {c: [(fid, *cwru_load(fid)) for fid in ids] for c, ids in CWRU.items()}
    for cls in ("inner", "ball", "outer"):
        for fid, x, rpm in data[cls][::3]:                          # one file per severity for the size curves
            x = x[:seg]
            priors = fit_all(x)
            f0 = ORDERS[cls] * rpm / 60
            ref = envelope_peak(x, fs, f0)
            for m, p, b, r in method_points(x, priors):
                rows.append({"data": "cwru", "rec": fid, "cls": cls, "method": m, "param": p, "bps": b,
                             "rel_err": rel_err(x, r), "peak_dev": log_dev(envelope_peak(r, fs, f0), ref)})
    # Surprise: per load, fit on all recordings of that load (a fleet window); sigma from the healthy reference.
    ep = 8192
    for load in range(4):
        recs = [(c, fid, x) for c, lst in data.items() for (fid, x, _) in lst if CWRU[c].index(fid) % 4 == load]
        healthy = next(x for c, fid, x in recs if c == "normal")
        ref_part, test_part = healthy[: len(healthy) // 2], healthy[len(healthy) // 2:]
        pool = np.concatenate([test_part] + [x for c, _, x in recs if c != "normal"])
        priors = fit_all(pool, background=ref_part)
        mu, sd = ref_part.mean(), ref_part.std()
        for c, fid, x in recs:
            xs = test_part if c == "normal" else x
            for i in range(0, len(xs) - ep + 1, ep):
                e = xs[i:i + ep]
                row = {"load": load, "cls": c, "rec": fid, "fault": c != "normal",
                       "rms": float(np.sqrt(np.mean((e - mu) ** 2)) / sd), "kurtosis": float(kurtosis(e)),
                       "crest": float(np.max(np.abs(e - mu)) / np.sqrt(np.mean((e - mu) ** 2)))}
                for k, pr in priors.items():
                    row[f"surprise-{k}"] = S.surprise(e, pr)
                surprise_rows.append(row)
    return rows, surprise_rows


# ---------------------------------------------------------------- PyScrew s01
def run_pyscrew():
    base = ROOT / "data/pyscrew/zenodo-15393134/s01"
    labels = list(csv.DictReader(open(base / "labels.csv")))
    groups = {}
    for r in labels:
        groups.setdefault((r["workpiece_id"], r["workpiece_location"]), []).append(r)
    rows = []
    for (wp, loc), rs in sorted(groups.items()):
        rs.sort(key=lambda r: int(r["run_id"]))
        cycles = []
        for r in rs:
            d = json.load(open(next(base.glob(f"json/*/{r['file_name']}"))))
            t = np.array([v for s in d["tightening steps"] for v in s["graph"]["torque values"]], float)
            cycles.append((r, t))
        x = np.concatenate([t for _, t in cycles])
        levels = 4
        priors = fit_all(x, levels=levels)
        for r, t in cycles:
            if len(t) < 64:
                continue
            row = {"rec": f"{wp}-{loc}", "nok": r["workpiece_result"] == "NOK",
                   "label": r.get("class_value"), "rms": float(np.sqrt(np.mean(t ** 2))), "kurtosis": float(kurtosis(t)),
                   "max": float(t.max()), "len": len(t)}
            for k, pr in priors.items():
                row[f"surprise-{k}"] = S.surprise(t, pr)
            rows.append(row)
    return rows


# ---------------------------------------------------------------- seismic
def stalta_max(x, fs=100.0, sta=1.0, lta=30.0):
    e = x.astype(float) - np.median(x)
    e = e * e
    c = np.cumsum(np.concatenate([[0], e]))
    ns, nl = int(sta * fs), int(lta * fs)
    if len(e) <= nl:
        return 0.0
    s = (c[nl:] - c[nl - ns:-ns]) / ns
    l_ = (c[nl:] - c[:-nl]) / nl
    return float(np.max(s / np.maximum(l_, 1e-12)))


def run_seismic(seis_json, recordings=16, ep=61440):
    recs = json.load(open(seis_json))
    rng = np.random.default_rng(20261105)
    pick = sorted(rng.choice(len(recs), min(recordings, len(recs)), replace=False))
    rows, approx = [], []
    for n_rec, i in enumerate(pick):
        r = recs[i]
        x = np.load(r["file"]).astype(float)
        x = x - np.median(x)
        arr = [a["sample"] for a in r["arrivals"]]
        priors = fit_all(x, levels=8)
        for a in range(0, len(x) - ep + 1, ep):
            e = x[a:a + ep]
            row = {"rec": r["id"], "start": a, "event": any(a <= p < a + ep for p in arr),
                   "rms": float(np.sqrt(np.mean(e ** 2))), "kurtosis": float(kurtosis(e)), "stalta": stalta_max(e)}
            for k, pr in priors.items():
                row[f"surprise-{k}"] = S.surprise(e, pr)
            rows.append(row)
        if n_rec < 4:                       # approximation curves: windows around arrivals and random quiet ones
            wins = [p - 3000 for p in arr if 3000 <= p < len(x) - 9000][:6]
            wins += list(rng.integers(0, len(x) - 12288, 6))
            for w in wins:
                e = x[w:w + 12288]
                base_ev = w + 3000 in [p for p in arr]
                ref = stalta_max(e)
                for m, p, b, rc in method_points(e, priors):
                    approx.append({"data": "seismic", "rec": r["id"], "event": bool(base_ev), "method": m, "param": p,
                                   "bps": b, "rel_err": rel_err(e, rc), "stalta_dev": log_dev(stalta_max(rc), ref)})
        print(f"  seismic {n_rec + 1}/{len(pick)} {r['id']}", flush=True)
    return rows, approx


# ---------------------------------------------------------------- cost
def run_cost():
    rng = np.random.default_rng(1)
    x = rng.normal(size=100 * 3600)                          # one hour at 100 Hz
    out = {}
    for k, (kind, alpha) in PRIORS.items():
        t = time.perf_counter(); pr = S.fit(x, kind, alpha=alpha, levels=8); tf = time.perf_counter() - t
        t = time.perf_counter(); S.approximate(x, pr); ta = time.perf_counter() - t
        out[k] = {"fit_s_per_hour_100hz": tf, "approx_and_score_s_per_hour_100hz": ta,
                  "samples_per_s": len(x) / ta}
    return out


def summarise_auroc(rows, label, scores):
    pos = [r for r in rows if r[label]]
    neg = [r for r in rows if not r[label]]
    return {s: round(auroc([r[s] for r in pos], [r[s] for r in neg]), 4) for s in scores}, len(pos), len(neg)


def main():
    args = sys.argv[1:]
    seis_json = args[args.index("--seismic-json") + 1] if "--seismic-json" in args else None
    OUT.mkdir(parents=True, exist_ok=True)
    out = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "pywt": pywt.__version__}
    sc = [f"surprise-{k}" for k in PRIORS]
    if "cwru" in args:
        rows, srows = run_cwru()
        out["cwru_approx"] = [{k: v for k, v in r.items()} for r in rows]
        out["cwru_at_targets"] = {"rel_err": at_targets(rows, "rel_err"), "peak_dev": at_targets(rows, "peak_dev")}
        out["cwru_auroc"] = summarise_auroc(srows, "fault", sc + ["rms", "kurtosis", "crest"])
        print("cwru", out["cwru_at_targets"])
        print("cwru auroc", out["cwru_auroc"], flush=True)
    if "pyscrew" in args:
        rows = run_pyscrew()
        out["pyscrew_rows"] = rows
        out["pyscrew_auroc"] = summarise_auroc(rows, "nok", sc + ["rms", "kurtosis", "max", "len"])
        print("pyscrew auroc", out["pyscrew_auroc"], flush=True)
    if "seismic" in args:
        rows, approx = run_seismic(seis_json)
        out["seismic_rows"] = rows
        out["seismic_auroc"] = summarise_auroc(rows, "event", sc + ["rms", "kurtosis", "stalta"])
        out["seismic_approx"] = approx
        out["seismic_at_targets"] = {"rel_err": at_targets(approx, "rel_err"),
                                     "stalta_dev": at_targets([a for a in approx if a["event"]], "stalta_dev")}
        print("seismic auroc", out["seismic_auroc"])
        print("seismic", out["seismic_at_targets"], flush=True)
    if "cost" in args:
        out["cost"] = run_cost()
        print("cost", out["cost"])
    p = OUT / f"f2_dev_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, default=float))
    print(p)


if __name__ == "__main__":
    main()
