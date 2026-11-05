#!/usr/bin/env python3
"""F2 development, edge focus: approximation per byte, and whether diagnosis survives (not a frozen study).

Every method produces actual bytes: wavelet thresholding with the coder in shrink.encode (level-wise or global
noise scale; optional posterior selection), SZ3 and ZFP at an absolute error bound, decimation with the same varint
and zlib coding. Rate control as a device would do it: per window, the setting with the smallest relative error
among those within the byte budget (the device has the original).

  .venv-f2/bin/python evaluation/edge_relevance_f2/f2_edge.py [cwru] [ims] [seismic] [pyscrew] [cost] \
      --seismic-json F
"""
import csv
import io
import json
import sys
import time
import zlib
from pathlib import Path

import h5py
import hdf5plugin
import numpy as np
import pywt
import zfpy
from scipy.io import loadmat
from scipy.signal import butter, decimate, hilbert, sosfiltfilt
from scipy.stats import kurtosis

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import shrink as S  # noqa: E402

OUT = HERE / "results" / "dev"
BUDGETS = (0.05, 0.1, 0.2, 0.3)
CS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0)
QA = (0.25, 0.5, 1.0, 2.0)
QS = (0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
TOLS = (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512)


def rel_err(x, r):
    return float(np.linalg.norm(x - r) / (np.linalg.norm(x - x.mean()) or 1e-12))


def robust_sigma(x, wavelet="sym8"):
    return S.noise_sigma(x, wavelet)


def sz3(x, abs_err):
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        d = f.create_dataset("x", data=x, chunks=(len(x),), **hdf5plugin.SZ3(absolute=float(abs_err)))
        size = d.id.get_storage_size()
    with h5py.File(buf, "r") as f:
        return size, f["x"][:]


def candidates(x, levels, bayes_prior=None):
    """{method: [(bytes, reconstruction, setting)]}."""
    n = len(x)
    sig = robust_sigma(x)
    out = {"wavelet": [], "sz3": [], "zfp": [], "decimate": []}
    for c in CS:
        for q in QA:
            b, r = S.encode_abs(x, c, q, levels=levels)
            out["wavelet"].append((len(b), r, f"c={c},q={q}"))
    if bayes_prior is not None:
        out["wavelet-bayes"] = []
        for tau in (0.5, 0.9, 0.99):
            def sel(j, d, tau=tau):
                pi, _ = S.posterior(bayes_prior, d / bayes_prior.sigma, bayes_prior.per_level[j])
                return pi > tau
            for c in CS:                       # c sets the quantisation step only (T = c * std, step = q * T)
                b, r = S.encode_abs(x, c, 0.5, levels=levels, select=sel)
                out["wavelet-bayes"].append((len(b), r, f"tau={tau},c={c}"))
    for tol in TOLS:
        size, r = sz3(x, tol * sig)
        out["sz3"].append((size, r, f"abs={tol}"))
        c = zfpy.compress_numpy(np.ascontiguousarray(x), tolerance=tol * sig)
        out["zfp"].append((len(c), zfpy.decompress_numpy(c), f"tol={tol}"))
    for k in (2, 4, 8, 16, 32, 64):
        if n // k < 8:
            continue
        d = decimate(x, k, ftype="fir", zero_phase=True)
        for q in QS:
            qi = np.round(d / (q * sig)).astype(np.int64)
            blob = zlib.compress(S._varint(np.array([n, k], np.int64)) + np.float32(q * sig).tobytes()
                                 + S._varint(S._zigzag(np.diff(qi, prepend=0))), 9)
            r = np.interp(np.arange(n), np.arange(len(d)) * k, qi * q * sig)
            out["decimate"].append((len(blob), r, f"k={k},q={q}"))
    return out


def rate_control(x, cands, budget):
    """Per method, the candidate with the smallest relative error within budget bytes/sample (None if none)."""
    n = len(x)
    pick = {}
    for m, lst in cands.items():
        ok = [(rel_err(x, r), b, r, s) for b, r, s in lst if b / n <= budget]
        pick[m] = min(ok, key=lambda t: t[0]) if ok else None
    return pick


# ---------------------------------------------------------------- CWRU: error per byte
CWRU_FAULT = [105, 118, 130, 169, 185, 197, 209, 222, 234]


def run_cwru():
    rows = []
    for fid in CWRU_FAULT + [97, 98]:
        d = loadmat(ROOT / f"data/cwru/raw/{fid}.mat")
        x = np.asarray(d[next(k for k in d if k.endswith("_DE_time"))], float).ravel()
        for w in range(0, min(len(x), 4 * 12000) - 12000 + 1, 12000):          # four 1 s windows per file
            e = x[w:w + 12000]
            pr = S.fit(e, "pareto", levels=6)
            cands = candidates(e, 6, pr)
            for budget in BUDGETS:
                for m, p in rate_control(e, cands, budget).items():
                    rows.append({"rec": fid, "win": w, "budget": budget, "method": m,
                                 "rel_err": None if p is None else p[0], "bps": None if p is None else p[1] / len(e),
                                 "setting": None if p is None else p[3]})
        print(f"  cwru {fid}", flush=True)
    return rows


# ---------------------------------------------------------------- IMS set 2, bearing 1: when does the fault show?
def envelope_snr(x, fs, f0, band=(2000, 8000)):
    sos = butter(4, band, btype="band", fs=fs, output="sos")
    env = np.abs(hilbert(sosfiltfilt(sos, x)))
    env -= env.mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env))))
    f = np.fft.rfftfreq(len(env), 1 / fs)
    sel = (f > 20) & (f < 1000)
    i = np.argmin(np.abs(f - f0))
    return float(spec[i - 2:i + 3].max() / np.median(spec[sel]))


def detection_index(snr, healthy=100, k=4.0, run=5):
    base = np.asarray(snr[:healthy])
    thr = base.mean() + k * base.std()
    above = np.asarray(snr) > thr
    for i in range(healthy, len(snr) - run + 1):
        if above[i:i + run].all():
            return i
    return None


def run_ims(step_files=1):
    z = np.load(ROOT / "data/ims/npz/set2.npz")
    xs, step = z["x"][:, :, 0], float(z["step"])
    fs, bpfo = 20480.0, 236.4
    orig, rec = [], {}
    rows = []
    for fi in range(0, len(xs), step_files):
        x = xs[fi].astype(float) * step
        orig.append(envelope_snr(x, fs, bpfo))
        cands = candidates(x, 6)
        for budget in BUDGETS:
            for m, p in rate_control(x, cands, budget).items():
                rec.setdefault((m, budget), []).append(np.nan if p is None else envelope_snr(p[2], fs, bpfo))
                rows.append({"file": fi, "budget": budget, "method": m, "rel_err": None if p is None else p[0],
                             "bps": None if p is None else p[1] / len(x)})
        if fi % 100 == 0:
            print(f"  ims {fi}/{len(xs)}", flush=True)
    det0 = detection_index(orig)
    detect = {"original": det0}
    for (m, budget), snr in rec.items():
        di = detection_index(np.nan_to_num(snr, nan=0.0))
        detect[f"{m}@{budget}"] = di
    return rows, detect, orig


# ---------------------------------------------------------------- seismic: do triggers survive?
BAND = butter(4, [2.0, 20.0], btype="band", fs=100.0, output="sos")


def stalta(x, fs=100.0, sta=1.0, lta=30.0):
    e = sosfiltfilt(BAND, x - np.median(x)) ** 2
    c = np.cumsum(np.concatenate([[0.0], e]))
    ns, nl = int(sta * fs), int(lta * fs)
    r = np.zeros(len(x))
    s = (c[nl:] - c[nl - ns:-ns]) / ns
    l_ = (c[nl:] - c[:-nl]) / nl
    r[nl - 1:len(x)] = s / np.maximum(l_, 1e-12)
    return r


def triggers(ratio, on=8.0, off=1.5):
    out, active, start = [], False, 0
    for i, v in enumerate(ratio):
        if not active and v > on:
            active, start = True, i
        elif active and v < off:
            active = False
            out.append(start)
    if active:
        out.append(start)
    return np.array(out)


def match(trig, arrivals, tol):
    hit = sum(any(abs(t - a) <= tol for t in trig) for a in arrivals)
    false = sum(not any(abs(t - a) <= tol for a in arrivals) for t in trig)
    return hit, false


def run_seismic(seis_json, recordings=8, win=2 ** 15):
    recs = json.load(open(seis_json))
    rng = np.random.default_rng(20261105)
    pick = sorted(rng.choice(len(recs), recordings, replace=False))
    rows, trig_rows = [], []
    for i in pick:
        r = recs[i]
        x = np.load(r["file"]).astype(float)
        arr = [a["sample"] for a in r["arrivals"]]
        recon = {}
        for w in range(0, len(x) - win + 1, win):
            e = x[w:w + win]
            cands = candidates(e, 8)
            for budget in BUDGETS:
                for m, p in rate_control(e, cands, budget).items():
                    recon.setdefault((m, budget), []).append(np.zeros(win) + np.median(e) if p is None else p[2])
                    rows.append({"rec": r["id"], "win": w, "budget": budget, "method": m,
                                 "rel_err": None if p is None else p[0], "bps": None if p is None else p[1] / win})
        n = (len(x) // win) * win
        t0 = triggers(stalta(x[:n]))
        h0, f0 = match(t0, [a for a in arr if a < n], 10 * 100)
        trig_rows.append({"rec": r["id"], "method": "original", "budget": None, "arrivals": len([a for a in arr if a < n]),
                          "hits": h0, "false": f0})
        for (m, budget), parts in recon.items():
            y = np.concatenate(parts)
            t = triggers(stalta(y))
            h, fa = match(t, [a for a in arr if a < n], 10 * 100)
            trig_rows.append({"rec": r["id"], "method": m, "budget": budget, "arrivals": len([a for a in arr if a < n]),
                              "hits": h, "false": fa})
        print(f"  seismic {r['id']}", flush=True)
    return rows, trig_rows


# ---------------------------------------------------------------- PyScrew s01: per-cycle approximation
def run_pyscrew(max_groups=40):
    base = ROOT / "data/pyscrew/zenodo-15393134/s01"
    labels = list(csv.DictReader(open(base / "labels.csv")))
    rows = []
    for r in labels[: max_groups * 25]:
        d = json.load(open(next(base.glob(f"json/*/{r['file_name']}"))))
        t = np.array([v for s in d["tightening steps"] for v in s["graph"]["torque values"]], float)
        if len(t) < 128:
            continue
        cands = candidates(t, 4)
        for budget in BUDGETS + (0.5, 1.0):
            for m, p in rate_control(t, cands, budget).items():
                rows.append({"run": r["run_id"], "nok": r["workpiece_result"] == "NOK", "budget": budget, "method": m,
                             "rel_err": None if p is None else p[0], "bps": None if p is None else p[1] / len(t),
                             "kurt_orig": float(kurtosis(t)), "kurt_rec": None if p is None else float(kurtosis(p[2]))})
    return rows


# ---------------------------------------------------------------- Bosch CNC (development: machine M01 only)
def _bosch_file(args):
    path, label, budgets = args
    with h5py.File(path, "r") as h:
        v = h["vibration_data"][:].astype(float)
    win = 8192
    n = (len(v) // win) * win
    res = {"file": Path(path).name, "op": Path(path).parts[-3], "bad": label == "bad", "samples": n, "axes": []}
    rows = []
    for ax in range(3):
        x = v[:n, ax]
        rec = {b: [] for b in budgets}
        for w in range(0, n, win):
            e = x[w:w + win]
            c = candidates(e, 6)
            c.pop("zfp", None)
            for b in budgets:
                pick = rate_control(e, c, b)
                rec[b].append({m: (None if p is None else p[2]) for m, p in pick.items()})
                for m, p in pick.items():
                    rows.append({"file": res["file"], "axis": ax, "budget": b, "method": m,
                                 "rel_err": None if p is None else p[0], "bps": None if p is None else p[1] / win})
        feats = {"orig": (float(np.sqrt(np.mean((x - x.mean()) ** 2))), float(kurtosis(x)))}
        for b in budgets:
            for m in ("wavelet", "sz3", "decimate"):
                parts = [r[m] for r in rec[b]]
                if any(p is None for p in parts):
                    feats[f"{m}@{b}"] = None
                    continue
                y = np.concatenate(parts)
                feats[f"{m}@{b}"] = (float(np.sqrt(np.mean((y - y.mean()) ** 2))), float(kurtosis(y)))
        res["axes"].append(feats)
    return res, rows


def run_bosch(good_sample=100, workers=6):
    from multiprocessing import Pool
    base = ROOT / "data/bosch/repo/data/M01"
    bad = sorted(base.glob("*/bad/*.h5"))
    good = sorted(base.glob("*/good/*.h5"))
    rng = np.random.default_rng(20261105)
    good = [good[i] for i in sorted(rng.choice(len(good), min(good_sample, len(good)), replace=False))]
    jobs = [(str(p), "bad", BUDGETS) for p in bad] + [(str(p), "good", BUDGETS) for p in good]
    with Pool(workers) as pool:
        out = pool.map(_bosch_file, jobs)
    files = [r for r, _ in out]
    rows = [x for _, rs in out for x in rs]
    return files, rows


def bosch_auroc(files):
    """Label-free anomaly score per file: the largest robust z-score of RMS or kurtosis over the three axes, against
    all files of the same operation (median and MAD, no labels). AUROC of bad vs good, per representation."""
    import collections
    keys = [k for k in files[0]["axes"][0]]
    out = {}
    for k in keys:
        if any(f["axes"][a][k] is None for f in files for a in range(3)):
            out[k] = None
            continue
        by_op = collections.defaultdict(list)
        for f in files:
            by_op[f["op"]].append(f)
        score = {}
        for op, fs in by_op.items():
            for a in range(3):
                for j in range(2):
                    vals = np.array([f["axes"][a][k][j] for f in fs])
                    med, mad = np.median(vals), np.median(np.abs(vals - np.median(vals))) or 1e-12
                    for f, v in zip(fs, vals):
                        score[f["file"]] = max(score.get(f["file"], 0.0), abs(v - med) / mad)
        pos = [score[f["file"]] for f in files if f["bad"]]
        neg = [score[f["file"]] for f in files if not f["bad"]]
        allv = np.r_[pos, neg]
        r = np.argsort(np.argsort(allv)) + 1.0
        out[k] = round(float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))), 4)
    rms_dev = {}
    for k in keys:
        if k == "orig" or out.get(k) is None:
            continue
        rms_dev[k] = round(float(np.median([abs(f["axes"][a][k][0] / f["axes"][a]["orig"][0] - 1)
                                            for f in files for a in range(3)])), 4)
    return out, rms_dev


def run_cost():
    rng = np.random.default_rng(1)
    x = np.cumsum(rng.normal(size=64000 * 10)) * 0.01 + rng.normal(size=64000 * 10)   # 10 s at 64 kHz
    out = {}
    for lam, q in ((3.0, 2.0), (4.0, 4.0)):
        t = time.perf_counter()
        for w in range(0, len(x), 65536):
            S.encode(x[w:w + 65536], levels=6, lam=lam, q=q)
        dt = time.perf_counter() - t
        out[f"wavelet lam={lam} q={q}"] = {"samples_per_s": len(x) / dt, "realtime_x_at_64khz": len(x) / dt / 64000}
    return out


def summarise(rows):
    import collections
    agg = collections.defaultdict(list)
    miss = collections.Counter()
    for r in rows:
        if r["rel_err"] is None:
            miss[(r["method"], r["budget"])] += 1
        else:
            agg[(r["method"], r["budget"])].append(r["rel_err"])
    return {f"{m}@{b}": {"median_rel_err": round(float(np.median(v)), 4), "n": len(v), "missed": miss[(m, b)]}
            for (m, b), v in sorted(agg.items())} | {f"{m}@{b}": {"median_rel_err": None, "n": 0, "missed": c}
                                                       for (m, b), c in miss.items() if (m, b) not in agg}


def main():
    args = sys.argv[1:]
    OUT.mkdir(parents=True, exist_ok=True)
    out = {"started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if "cwru" in args:
        rows = run_cwru()
        out["cwru"] = rows
        out["cwru_summary"] = summarise(rows)
        print(json.dumps(out["cwru_summary"]), flush=True)
    if "ims" in args:
        rows, det, snr = run_ims()
        out["ims"], out["ims_detect"], out["ims_snr"] = rows, det, snr
        out["ims_summary"] = summarise(rows)
        print(json.dumps(det), flush=True)
    if "seismic" in args:
        sj = args[args.index("--seismic-json") + 1]
        rows, trig = run_seismic(sj)
        out["seismic"], out["seismic_triggers"] = rows, trig
        out["seismic_summary"] = summarise(rows)
        print(json.dumps(out["seismic_summary"]), flush=True)
    if "pyscrew" in args:
        rows = run_pyscrew()
        out["pyscrew"] = rows
        out["pyscrew_summary"] = summarise(rows)
        print(json.dumps(out["pyscrew_summary"]), flush=True)
    if "bosch" in args:
        files, rows = run_bosch()
        out["bosch_files"], out["bosch"] = files, rows
        out["bosch_summary"] = summarise(rows)
        out["bosch_auroc"], out["bosch_rms_dev"] = bosch_auroc(files)
        print(json.dumps(out["bosch_summary"]), json.dumps(out["bosch_auroc"]), json.dumps(out["bosch_rms_dev"]), flush=True)
    if "cost" in args:
        out["cost"] = run_cost()
        print(out["cost"])
    p = OUT / f"f2_edge_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, default=float))
    print(p)


if __name__ == "__main__":
    main()
