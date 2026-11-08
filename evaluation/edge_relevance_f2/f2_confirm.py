#!/usr/bin/env python3
"""F2 confirmation (PROTOCOL.md, frozen): one run per dataset on held-out data.

Datasets and measures as frozen:
  xjtu       XJTU-SY run-to-failure bearings with a single outer- or inner-race fault (11 bearings), horizontal
             channel, 25.6 kHz, one window per file (32,768 samples): error per byte; fault detection in the
             envelope spectrum (fault order, healthy baseline = first min(100, 30%) files, + 4 sd, five
             consecutive files), delay and isolated alarms.
  paderborn  Paderborn bearings with real damage (KA04, KA15, KA16, KA22, KA30, KI04, KI14, KI16, KI18, KI21) and
             healthy (K001-K006), condition N15_M07_F10, files 1-20, vibration_1 at 64 kHz, three windows of
             65,536 per file: error per byte; AUROC of kurtosis, damaged against healthy.
  pyscrew    PyScrew s02, every cycle of at least 128 samples: error per byte; AUROC of kurtosis for NOK cycles.
  seismic    CX 2021 held-out gallery, 16 station-days (seeded), windows of 2^15: error per byte; STA/LTA triggers
             at catalogued arrivals and elsewhere.
  bosch      Bosch CNC milling, machines M02 and M03: every bad run and 200 good runs (seeded), three axes,
             windows of 8,192: error per byte; AUROC of the label-free run score; RMS deviation.
H4 (Bayesian selection) on the first 40 windows of xjtu, paderborn and bosch.

  .venv-f2/bin/python evaluation/edge_relevance_f2/f2_confirm.py <dataset> [--seismic-json F]
"""
import csv
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import h5py
import numpy as np
from scipy.io import loadmat
from scipy.stats import kurtosis

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import f2_edge as F  # noqa: E402
import shrink as S  # noqa: E402

OUT = HERE / "results"
SEED = 20261105
BUDGETS = F.BUDGETS
WORKERS = 6


def auroc(pos, neg):
    allv = np.r_[pos, neg]
    _, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    ranks = np.argsort(np.argsort(allv, kind="mergesort"), kind="mergesort") + 1.0
    ranks = (np.bincount(inv, ranks) / cnt)[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def code_window(x, levels, bayes=False):
    """Per budget and method: (rel_err, bytes, reconstruction)."""
    prior = S.fit(x, "pareto", levels=levels) if bayes else None
    c = F.candidates(x, levels, prior)
    return {b: F.rate_control(x, c, b) for b in BUDGETS}


def rows_of(tag, picks, n):
    out = []
    for b, pick in picks.items():
        for m, p in pick.items():
            out.append({"item": tag, "budget": b, "method": m, "rel_err": None if p is None else p[0],
                        "bps": None if p is None else p[1] / n})
    return out


# ---------------------------------------------------------------- XJTU-SY
XJTU = {"35Hz12kN": (35.0, {"Bearing1_1": "outer", "Bearing1_2": "outer", "Bearing1_3": "outer"}),
        "37.5Hz11kN": (37.5, {"Bearing2_1": "inner", "Bearing2_2": "outer", "Bearing2_4": "outer",
                              "Bearing2_5": "outer"}),
        "40Hz10kN": (40.0, {"Bearing3_1": "outer", "Bearing3_3": "inner", "Bearing3_4": "inner",
                            "Bearing3_5": "outer"})}
ORDER = {"outer": 4 * (1 - 7.92 / 34.55), "inner": 4 * (1 + 7.92 / 34.55)}   # 8 balls, contact angle 0


def _xjtu_file(args):
    path, f0, i, bayes = args
    x = np.loadtxt(path, delimiter=",", skiprows=1, usecols=0)
    picks = code_window(x, 6, bayes)
    snr = {"original": F.envelope_snr(x, 25600.0, f0)}
    for b, pick in picks.items():
        for m, p in pick.items():
            snr[f"{m}@{b}"] = float("nan") if p is None else F.envelope_snr(p[2], 25600.0, f0)
    return i, rows_of(f"{Path(path).parent.name}/{Path(path).name}", picks, len(x)), snr


def run_xjtu():
    base = ROOT / "data/xjtu-sy/extracted/XJTU-SY_Bearing_Datasets"
    rows, det = [], {}
    nbayes = 0
    for cond, (fr, bearings) in XJTU.items():
        for name, fault in bearings.items():
            files = sorted((base / cond / name).glob("*.csv"), key=lambda p: int(p.stem))
            f0 = ORDER[fault] * fr
            jobs = []
            for i, p in enumerate(files):
                bayes = nbayes < 40
                nbayes += bayes
                jobs.append((str(p), f0, i, bayes))
            with Pool(WORKERS) as pool:
                res = sorted(pool.map(_xjtu_file, jobs), key=lambda t: t[0])
            for _, r, _ in res:
                rows += r
            series = {}
            for _, _, snr in res:
                for k, v in snr.items():
                    series.setdefault(k, []).append(v)
            n = len(files)
            healthy = min(100, int(0.3 * n))
            d0 = F.detection_index(series["original"], healthy=healthy)
            entry = {"files": n, "healthy": healthy, "fault": fault, "f0": f0, "original": d0,
                     "false_alarms": {"original": F.false_alarms(series["original"], d0 or n, healthy=healthy)}}
            for k, s in series.items():
                if k == "original":
                    continue
                s = np.nan_to_num(np.asarray(s, float), nan=0.0)
                entry[k] = F.detection_index(s, healthy=healthy)
                entry["false_alarms"][k] = F.false_alarms(s, d0 or n, healthy=healthy)
            det[name] = entry
            print(f"  xjtu {name}: {n} files, detected at {d0}", flush=True)
    return rows, det


# ---------------------------------------------------------------- Paderborn
PB_DAMAGED = ["KA04", "KA15", "KA16", "KA22", "KA30", "KI04", "KI14", "KI16", "KI18", "KI21"]
PB_HEALTHY = ["K001", "K002", "K003", "K004", "K005", "K006"]


def _pb_file(args):
    path, label, bayes = args
    d = loadmat(path, squeeze_me=True, struct_as_record=False)
    s = d[[k for k in d if not k.startswith("__")][0]]
    x = next(np.asarray(y.Data, float) for y in np.atleast_1d(s.Y) if y.Name == "vibration_1")
    wins = [x[i:i + 65536] for i in range(0, len(x) - 65536 + 1, 65536)]
    rows, recs = [], {}
    for j, w in enumerate(wins):
        picks = code_window(w, 6, bayes and j == 0)
        rows += rows_of(f"{Path(path).name}#{j}", picks, len(w))
        for b, pick in picks.items():
            for m, p in pick.items():
                recs.setdefault(f"{m}@{b}", []).append(None if p is None else p[2])
    xs = np.concatenate(wins)
    k = {"original": float(kurtosis(xs))}
    for key, parts in recs.items():
        k[key] = None if any(p is None for p in parts) else float(kurtosis(np.concatenate(parts)))
    return {"file": Path(path).name, "damaged": label == "damaged", "kurtosis": k}, rows


def run_paderborn():
    base = ROOT / "data/paderborn/zenodo-15845309/extracted"
    jobs, nb = [], 0
    for lab, names in (("damaged", PB_DAMAGED), ("healthy", PB_HEALTHY)):
        for b in names:
            for i in range(1, 21):
                p = base / b / f"N15_M07_F10_{b}_{i}.mat"
                if p.exists():
                    jobs.append((str(p), lab, nb < 13))
                    nb += 1
    with Pool(WORKERS) as pool:
        out = pool.map(_pb_file, jobs)
    files = [f for f, _ in out]
    rows = [r for _, rs in out for r in rs]
    au = {}
    keys = set.intersection(*(set(f["kurtosis"]) for f in files))   # the Bayesian arm exists for a subset only
    for key in sorted(keys):
        if any(f["kurtosis"][key] is None for f in files):
            au[key] = None
            continue
        au[key] = round(auroc([f["kurtosis"][key] for f in files if f["damaged"]],
                              [f["kurtosis"][key] for f in files if not f["damaged"]]), 4)
    return rows, files, au


# ---------------------------------------------------------------- PyScrew s02
def _ps_cycle(args):
    path, run, nok = args
    d = json.load(open(path))
    t = np.array([v for s in d["tightening steps"] for v in s["graph"]["torque values"]], float)
    if len(t) < 128:
        return None
    picks = code_window(t, 4)
    k = {"original": float(kurtosis(t))}
    for b, pick in picks.items():
        for m, p in pick.items():
            k[f"{m}@{b}"] = None if p is None else float(kurtosis(p[2]))
    return {"run": run, "nok": nok, "kurtosis": k}, rows_of(run, picks, len(t))


def run_pyscrew():
    base = ROOT / "data/pyscrew/zenodo-15393134/s02"
    labels = list(csv.DictReader(open(base / "labels.csv")))
    index = {p.name: p for p in base.glob("json/*/*.json")}
    jobs = [(str(index[r["file_name"]]), r["run_id"], r["workpiece_result"] == "NOK") for r in labels
            if r["file_name"] in index]
    with Pool(WORKERS) as pool:
        out = [o for o in pool.map(_ps_cycle, jobs, chunksize=50) if o is not None]
    cyc = [c for c, _ in out]
    rows = [r for _, rs in out for r in rs]
    au = {}
    for key in cyc[0]["kurtosis"]:
        have = [c for c in cyc if c["kurtosis"][key] is not None]
        au[key] = {"auroc": round(auroc([c["kurtosis"][key] for c in have if c["nok"]],
                                        [c["kurtosis"][key] for c in have if not c["nok"]]), 4),
                   "cycles": len(have), "of": len(cyc)}
    return rows, au


# ---------------------------------------------------------------- seismic
def _seis_rec(args):
    r, win = args
    x = np.load(r["file"]).astype(float)
    arr = [a["sample"] for a in r["arrivals"]]
    recon, rows = {}, []
    n = (len(x) // win) * win
    for w in range(0, n, win):
        e = x[w:w + win]
        picks = code_window(e, 8)
        rows += rows_of(f"{r['id']}#{w}", picks, win)
        for b, pick in picks.items():
            for m, p in pick.items():
                recon.setdefault((m, b), []).append(np.zeros(win) + np.median(e) if p is None else p[2])
    a_in = [a for a in arr if a < n]
    trig = []
    h, f = F.match(F.triggers(F.stalta(x[:n])), a_in, 1000)
    trig.append({"rec": r["id"], "method": "original", "budget": None, "arrivals": len(a_in), "hits": h, "false": f})
    for (m, b), parts in recon.items():
        h, f = F.match(F.triggers(F.stalta(np.concatenate(parts))), a_in, 1000)
        trig.append({"rec": r["id"], "method": m, "budget": b, "arrivals": len(a_in), "hits": h, "false": f})
    return rows, trig


def run_seismic(seis_json, recordings=16):
    recs = json.load(open(seis_json))
    rng = np.random.default_rng(SEED)
    pick = [recs[i] for i in sorted(rng.choice(len(recs), recordings, replace=False))]
    with Pool(WORKERS) as pool:
        out = pool.map(_seis_rec, [(r, 2 ** 15) for r in pick])
    return [x for rs, _ in out for x in rs], [t for _, ts in out for t in ts]


# ---------------------------------------------------------------- Bosch M02, M03
def run_bosch():
    base = ROOT / "data/bosch/repo/data"
    bad = sorted(base.glob("M0[23]/*/bad/*.h5"))
    good = sorted(base.glob("M0[23]/*/good/*.h5"))
    rng = np.random.default_rng(SEED)
    good = [good[i] for i in sorted(rng.choice(len(good), 200, replace=False))]
    jobs = [(str(p), "bad", BUDGETS) for p in bad] + [(str(p), "good", BUDGETS) for p in good]
    with Pool(WORKERS) as pool:
        out = pool.map(F._bosch_file, jobs)
    files = [r for r, _ in out]
    rows = [x for _, rs in out for x in rs]
    au, rms = F.bosch_auroc(files)
    return rows, files, au, rms


def main():
    args = sys.argv[1:]
    ds = args[0]
    out = {"dataset": ds, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "budgets": BUDGETS}
    t0 = time.time()
    if ds == "xjtu":
        out["rows"], out["detection"] = run_xjtu()
    elif ds == "paderborn":
        out["rows"], out["files"], out["auroc_kurtosis"] = run_paderborn()
    elif ds == "pyscrew":
        out["rows"], out["auroc_kurtosis"] = run_pyscrew()
    elif ds == "seismic":
        out["rows"], out["triggers"] = run_seismic(args[args.index("--seismic-json") + 1])
    elif ds == "bosch":
        out["rows"], out["files"], out["auroc_score"], out["rms_dev"] = run_bosch()
    out["summary"] = F.summarise(out["rows"])
    out["seconds"] = round(time.time() - t0)
    p = OUT / f"f2_confirm_{ds}_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, default=float))
    print(json.dumps({k: v for k, v in out.items() if k not in ("rows", "files")}, default=float)[:3000])
    print(p)


if __name__ == "__main__":
    main()
