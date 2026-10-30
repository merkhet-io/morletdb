#!/usr/bin/env python3
"""G1: gammatone filter bank vs kurtogram bands for bearing fault-frequency identification (PROTOCOL.md).

    .venv-seismic/bin/python -I evaluation/gammatone/g1.py cwru data/cwru/raw evaluation/gammatone/results
    .venv-seismic/bin/python -I evaluation/gammatone/g1.py xjtu \
        data/xjtu-sy/extracted/XJTU-SY_Bearing_Datasets evaluation/gammatone/results
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.io import loadmat
from scipy.signal import fftconvolve

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "multiwavelet"))
import w2  # noqa: E402  (envelope spectrum, measures, Butterworth bands, bootstrap, XJTU-SY reader)

ORDER = 4
GT_ERB_FACTOR = math.pi * math.factorial(2 * ORDER - 2) / (2 ** (2 * ORDER - 2) * math.factorial(ORDER - 1) ** 2)
IR_DECAY = 20.0                      # impulse response length: 20 / (2 pi b), envelope < 2e-5 of its peak
ERB_STEP = 1.0                       # ERB-bank centre spacing on the ERB-number scale
ARMS = ("RAW", "FK", "FK-RER", "GTFK-K", "GTFK-RER", "GTERB-K", "GTERB-RER")

# -- CWRU (12 kHz drive end, SKF 6205-2RS) ------------------------------------------------------
CWRU_FS = 12000.0
CWRU_N = 120000                      # first 10 s of every recording
CWRU_FK_LEVELS = (1, 1.6, 2, 2.6, 3, 3.6)   # bandwidth >= 500 Hz at 12 kHz
CWRU_MULT = {"inner": 5.4152, "outer": 3.5848, "cage": 0.39828, "ball": 4.7135}
CWRU_RPM = {0: 1797, 1: 1772, 2: 1750, 3: 1730}
NORMAL = {97: 0, 98: 1, 99: 2, 100: 3}


def _cwru_files():
    out = {}

    def put(ids, element, size, where):
        for load, i in enumerate(ids):
            out[i] = {"element": element, "size": size, "load": load, "position": where,
                      "cluster": f"{element}{'@' + where if where else ''} {size}"}
    put((105, 106, 107, 108), "inner", "007", "")
    put((118, 119, 120, 121), "ball", "007", "")
    put((130, 131, 132, 133), "outer", "007", "6")
    put((144, 145, 146, 147), "outer", "007", "3")
    put((156, 158, 159, 160), "outer", "007", "12")
    put((169, 170, 171, 172), "inner", "014", "")
    put((185, 186, 187, 188), "ball", "014", "")
    put((197, 198, 199, 200), "outer", "014", "6")
    put((209, 210, 211, 212), "inner", "021", "")
    put((222, 223, 224, 225), "ball", "021", "")
    put((234, 235, 236, 237), "outer", "021", "6")
    put((246, 247, 248, 249), "outer", "021", "3")
    put((258, 259, 260, 261), "outer", "021", "12")
    put((3001, 3002, 3003, 3004), "inner", "028", "")
    put((3005, 3006, 3007, 3008), "ball", "028", "")
    return out


CWRU_FAULTS = _cwru_files()
PREVIOUSLY_USED = (105, 106, 118, 119, 130, 131)   # early retrieval baseline (evaluation/prepare_cwru.py)


def cwru_freqs(load):
    fr = CWRU_RPM[load] / 60.0
    return {k: m * fr for k, m in CWRU_MULT.items()}


def read_cwru(path, channel="DE"):
    """One channel of a CWRU file; None if the file has no such channel (the 0.028" files lack FE)."""
    i = int(Path(path).stem)
    m = loadmat(path)
    keys = [k for k in m if k.endswith(f"_{channel}_time")]
    own = [k for k in keys if k.startswith(f"X{i:03d}_")]
    if own:
        key = own[0]
    elif len(keys) == 1:
        key = keys[0]
    elif not keys:
        return None
    else:
        raise ValueError(f"ambiguous {channel} channel in {path}: {keys}")
    return np.asarray(m[key], float).ravel()


# -- gammatone -----------------------------------------------------------------------------------
def erb(f):
    """Glasberg & Moore (1990) equivalent rectangular bandwidth in Hz."""
    return 24.7 * (4.37 * f / 1000 + 1)


def erb_number(f):
    return 21.4 * np.log10(1 + 0.00437 * f)


def erb_number_inv(e):
    return (10 ** (e / 21.4) - 1) / 0.00437


def gammatone_ir(fc, bw, fs):
    """Causal 4th-order gammatone impulse response with equivalent rectangular bandwidth bw, unit gain at fc."""
    b = bw / GT_ERB_FACTOR
    t = np.arange(int(np.ceil(IR_DECAY / (2 * np.pi * b) * fs)) + 1) / fs
    g = t ** (ORDER - 1) * np.exp(-2 * np.pi * b * t) * np.cos(2 * np.pi * fc * t)
    return g / abs(np.sum(g * np.exp(-2j * np.pi * fc * t)))


def gammatone(y, fc, bw, fs):
    return fftconvolve(y, gammatone_ir(fc, bw, fs))[:len(y)]


def erb_bank(fs, fmin=w2.MIN_CARRIER):
    """Centres one ERB apart; every filter lies within [fmin, fs/2] at +- half its ERB."""
    nyq = fs / 2
    e = erb_number(fmin + erb(fmin) / 2)
    out = []
    while True:
        fc = float(erb_number_inv(e))
        if fc + erb(fc) / 2 > nyq:
            return out
        if fc - erb(fc) / 2 >= fmin:
            out.append((fc, erb(fc)))
        e += ERB_STEP


def fk_bands(fs, levels):
    nyq = fs / 2
    out = []
    for lv in levels:
        k = int(lv)
        nb = 2 ** k if lv == k else 3 * 2 ** (k - 1)
        bw = nyq / nb
        out.extend((lv, i * bw, (i + 1) * bw) for i in range(nb) if i * bw >= w2.MIN_CARRIER)
    return out


# -- arms ----------------------------------------------------------------------------------------
def candidates(y, fs, levels):
    bands = fk_bands(fs, levels)
    return {
        "FK": [(f"L{lv} {lo:.0f}-{hi:.0f}", w2.bandpass(y, lo, hi, fs)) for lv, lo, hi in bands],
        "GTFK": [(f"L{lv} {lo:.0f}-{hi:.0f}", gammatone(y, (lo + hi) / 2, hi - lo, fs)) for lv, lo, hi in bands],
        "GTERB": [(f"{fc:.0f}+-{bw / 2:.0f}", gammatone(y, fc, bw, fs)) for fc, bw in erb_bank(fs)],
    }


def snapshot_arms(y, freqs, fs, levels):
    y = y - y.mean()
    out = {}
    for fam, cands in candidates(y, fs, levels).items():
        spectra = [w2.envelope_spectrum(s, fs) for _, s in cands]
        k = int(np.argmax([w2.spectral_kurtosis(s) for _, s in cands]))
        e = int(np.argmax([w2.rer(f, a, freqs) for f, a in spectra]))
        out["FK" if fam == "FK" else f"{fam}-K"] = (cands[k][0], spectra[k])
        out[f"{fam}-RER"] = (cands[e][0], spectra[e])
    out["RAW"] = ("raw", w2.envelope_spectrum(y, fs))
    return out


def score_rows(arms, freqs, elements, base):
    rows = []
    for arm, (label, (f, a)) in arms.items():
        for el in elements:
            rows.append(dict(base, arm=arm, selected=label, element=el,
                             score=float(np.log10(w2.peak_ratio(f, a, freqs[el]))),
                             detected=w2.detected(f, a, freqs[el])))
    return rows


# -- CWRU run ------------------------------------------------------------------------------------
def analyse_cwru(root):
    rows, normals = [], []
    for i, meta in sorted(CWRU_FAULTS.items()):
        freqs = cwru_freqs(meta["load"])
        for ch in ("DE", "FE"):
            x = read_cwru(Path(root) / f"{i}.mat", ch)
            if x is None:
                continue
            arms = snapshot_arms(x[:CWRU_N], freqs, CWRU_FS, CWRU_FK_LEVELS)
            rows += score_rows(arms, freqs, [meta["element"]], dict(meta, file=i, channel=ch))
        print(i, meta["cluster"], "load", meta["load"], flush=True)
    for i, load in NORMAL.items():
        freqs = cwru_freqs(load)
        x = read_cwru(Path(root) / f"{i}.mat", "DE")
        arms = snapshot_arms(x[:CWRU_N], freqs, CWRU_FS, CWRU_FK_LEVELS)
        normals += score_rows(arms, freqs, ["outer", "inner", "ball"], {"file": i, "load": load, "channel": "DE"})
    return rows, normals


def cwru_delta(rows, a, b, rng, channel="DE"):
    """Mean per-file score difference; 95% CI by bootstrap over the 15 fault conditions (clusters)."""
    sa = {r["file"]: r for r in rows if r["arm"] == a and r["channel"] == channel}
    sb = {r["file"]: r for r in rows if r["arm"] == b and r["channel"] == channel}
    d = {i: sa[i]["score"] - sb[i]["score"] for i in sa}
    clusters = sorted({sa[i]["cluster"] for i in sa})
    by = {c: [d[i] for i in d if sa[i]["cluster"] == c] for c in clusters}
    boots = []
    for _ in range(w2.RESAMPLES):
        pick = rng.choice(len(clusters), len(clusters))
        boots.append(np.mean([v for j in pick for v in by[clusters[j]]]))
    return {"candidate": a, "comparator": b, "files": len(d), "clusters": len(clusters),
            "delta": float(np.mean(list(d.values()))),
            "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "files_ahead": int(sum(v > 0 for v in d.values()))}


def decide(primary, det_cand, det_base):
    lo, hi = primary["ci95"]
    if primary["delta"] >= w2.MARGIN and lo > 0 and det_cand >= det_base:
        return "niche"
    if hi < w2.MARGIN:
        return "close"
    return "inconclusive"


def summarize_cwru(rows, normals):
    rng = np.random.default_rng(w2.SEED)
    res = {"arms": {}, "comparisons": {}, "normal_false_detections": {}}
    for ch in ("DE", "FE"):
        for arm in ARMS:
            rr = [r for r in rows if r["arm"] == arm and r["channel"] == ch]
            res["arms"][f"{arm} {ch}"] = {
                "mean_score": float(np.mean([r["score"] for r in rr])),
                "detected": int(sum(r["detected"] for r in rr)), "files": len(rr),
                "by_element": {el: f"{sum(r['detected'] for r in rr if r['element'] == el)}/"
                                   f"{sum(r['element'] == el for r in rr)}" for el in ("inner", "ball", "outer")}}
    for arm in ARMS:
        rr = [r for r in normals if r["arm"] == arm]
        res["normal_false_detections"][arm] = f"{sum(r['detected'] for r in rr)}/{len(rr)}"
    c = res["comparisons"]
    c["primary GTFK-RER vs FK-RER"] = cwru_delta(rows, "GTFK-RER", "FK-RER", rng)
    c["secondary GTERB-RER vs FK-RER"] = cwru_delta(rows, "GTERB-RER", "FK-RER", rng)
    for a, b in (("GTFK-K", "FK"), ("GTERB-K", "FK"), ("FK-RER", "FK"), ("RAW", "FK"), ("GTERB-RER", "GTFK-RER")):
        c[f"{a} vs {b}"] = cwru_delta(rows, a, b, rng)
    c["FE GTFK-RER vs FK-RER"] = cwru_delta(rows, "GTFK-RER", "FK-RER", rng, channel="FE")
    det = {a: res["arms"][f"{a} DE"]["detected"] for a in ARMS}
    res["decision"] = decide(c["primary GTFK-RER vs FK-RER"], det["GTFK-RER"], det["FK-RER"])
    res["secondary_rule"] = decide(c["secondary GTERB-RER vs FK-RER"], det["GTERB-RER"], det["FK-RER"])
    return res


# -- XJTU-SY run (descriptive, not blind: W2 already used these snapshots) -----------------------
def analyse_xjtu(root):
    rows = []
    for name, fr, d in w2.bearing_dirs(root):
        freqs = w2.char_freqs(fr)
        for p in sorted(d.glob("*.csv"), key=lambda p: int(p.stem))[-w2.LAST:]:
            x = w2.read_snapshot(p)
            for ch, col in (("horizontal", 0), ("vertical", 1)):
                arms = snapshot_arms(x[:, col], freqs, w2.FS, w2.FK_LEVELS)
                rows += score_rows(arms, freqs, w2.FAULTS[name],
                                   {"bearing": name, "snapshot": int(p.stem), "channel": ch})
        print(name, flush=True)
    return rows


def summarize_xjtu(rows):
    rng = np.random.default_rng(w2.SEED)
    res = {"arms": {}, "comparisons": {}}
    for ch in ("horizontal", "vertical"):
        for arm in ARMS:
            p = w2.pairs(rows, arm, ch)
            res["arms"][f"{arm} {ch}"] = {"mean_score": float(np.mean([v["score"] for v in p.values()])),
                                          "detected": int(sum(v["detected"] for v in p.values())), "pairs": len(p)}
    for a, b in (("GTFK-RER", "FK-RER"), ("GTERB-RER", "FK-RER"), ("GTFK-K", "FK"), ("GTERB-K", "FK")):
        res["comparisons"][f"{a} vs {b}"] = w2.delta(rows, a, b, rng)
    return res


def main():
    which, root, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    if which == "cwru":
        rows, normals = analyse_cwru(root)
        (out / "cwru_files.json").write_text(json.dumps({"faults": rows, "normals": normals}))
        res = summarize_cwru(rows, normals)
        (out / "g1_cwru.json").write_text(json.dumps(res, indent=1))
    else:
        rows = analyse_xjtu(root)
        (out / "xjtu_snapshots.json").write_text(json.dumps(rows))
        res = summarize_xjtu(rows)
        (out / "g1_xjtu.json").write_text(json.dumps(res, indent=1))
    for k, v in res["arms"].items():
        print(f"{k:22s} score {v['mean_score']:.3f} detected {v['detected']}/{v.get('files', v.get('pairs'))}"
              + (f" {v['by_element']}" if "by_element" in v else ""))
    for k, v in res["comparisons"].items():
        print(f"{k:34s} {v['delta']:+.3f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]")
    for k in ("normal_false_detections", "decision", "secondary_rule"):
        if k in res:
            print(k, res[k])


if __name__ == "__main__":
    main()
