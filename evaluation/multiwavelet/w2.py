#!/usr/bin/env python3
"""W2: Legendre multiwavelet vs kurtogram for bearing fault-frequency identification (PROTOCOL.md).

    .venv-seismic/bin/python -I evaluation/multiwavelet/w2.py \
        data/xjtu-sy/extracted/XJTU-SY_Bearing_Datasets evaluation/multiwavelet/results
"""
import json
import sys
from pathlib import Path

import numpy as np
from numpy.polynomial import legendre as npleg
from scipy.signal import butter, find_peaks, hilbert, sosfiltfilt

FS = 25600.0
LAST = 10
WINDOW_REL, WINDOW_ABS = 0.02, 1.0
BAND = (5.0, 500.0)
TOP_PEAKS = 5
DETECT_MAJORITY = 6
MIN_CARRIER = 500.0
FK_LEVELS = (1, 1.6, 2, 2.6, 3, 3.6, 4, 4.6)
R_MULT = 4
LMWT_LEVELS = (1, 2, 3, 4)
EWT_MODES = 5
EWT_SMOOTH = 64
MARGIN = 0.10
RESAMPLES = 2000
SEED = 7

N_BALLS, BALL_D, PITCH_D = 8, 7.92, 34.55
CONDITIONS = {"35Hz12kN": 35.0, "37.5Hz11kN": 37.5, "40Hz10kN": 40.0}
FAULTS = {"1_1": ["outer"], "1_2": ["outer"], "1_3": ["outer"], "1_4": ["cage"], "1_5": ["inner", "outer"],
          "2_1": ["inner"], "2_2": ["outer"], "2_3": ["cage"], "2_4": ["outer"], "2_5": ["outer"],
          "3_1": ["outer"], "3_2": ["inner", "ball", "cage", "outer"], "3_3": ["inner"], "3_4": ["inner"],
          "3_5": ["outer"]}
COMPOUND = ("1_5", "3_2")
ARMS = ("RAW", "FK", "FK-RER", "LMWT-K", "LMWT-RER", "EWT-K", "EWT-RER")


def char_freqs(fr):
    r = BALL_D / PITCH_D
    return {"outer": N_BALLS / 2 * fr * (1 - r), "inner": N_BALLS / 2 * fr * (1 + r),
            "ball": PITCH_D / (2 * BALL_D) * fr * (1 - r * r), "cage": fr / 2 * (1 - r)}


# -- envelope spectrum and measures ------------------------------------------------------------
def envelope_spectrum(y, fs=FS):
    e = np.abs(hilbert(y))
    e = e - e.mean()
    a = np.abs(np.fft.rfft(e * np.hanning(len(e))))
    f = np.fft.rfftfreq(len(e), 1 / fs)
    keep = (f >= BAND[0]) & (f <= BAND[1])
    return f[keep], a[keep]


def in_window(f, target):
    return np.abs(f - target) <= max(WINDOW_REL * target, WINDOW_ABS)


def window_max(f, a, target):
    m = in_window(f, target)
    return float(a[m].max()) if m.any() else 0.0


def peak_ratio(f, a, fc):
    return max(window_max(f, a, fc), window_max(f, a, 2 * fc)) / float(np.median(a))


def detected(f, a, fc):
    df = f[1] - f[0]
    p, _ = find_peaks(a, distance=max(1, int(round(1.0 / df))))
    top = p[np.argsort(a[p])[::-1][:TOP_PEAKS]]
    return bool(np.any(in_window(f[top], fc) | in_window(f[top], 2 * fc)))


def rer(f, a, freqs):
    num = sum(window_max(f, a, h * fc) ** 2 for fc in freqs.values() for h in (1, 2))
    return num / float(np.sum(a ** 2))


def spectral_kurtosis(y):
    c = hilbert(y)
    p = np.abs(c) ** 2
    return float(np.mean(p ** 2) / np.mean(p) ** 2 - 2)


# -- kurtogram filter bank (1/3-binary grid) ----------------------------------------------------
def fk_bands(fs=FS):
    nyq = fs / 2
    out = []
    for lv in FK_LEVELS:
        k = int(lv)
        nb = 2 ** k if lv == k else 3 * 2 ** (k - 1)   # Antoni: level k.6 has 3 * 2^(k-1) bands
        bw = nyq / nb
        for i in range(nb):
            lo, hi = i * bw, (i + 1) * bw
            if lo >= MIN_CARRIER:
                out.append((lv, lo, hi))
    return out


def bandpass(y, lo, hi, fs=FS):
    if hi >= fs / 2 - 1e-9:
        sos = butter(4, lo, "highpass", fs=fs, output="sos")
    else:
        sos = butter(4, [lo, hi], "bandpass", fs=fs, output="sos")
    return sosfiltfilt(sos, y)


# -- Legendre multiwavelet (Alpert) ------------------------------------------------------------
def legendre_filters(r=R_MULT):
    """H0, H1 (scaling) and G0, G1 (wavelet), r x r each; [[H0 H1], [G0 G1]] is orthogonal."""
    xg, wg = npleg.leggauss(2 * r + 4)
    u, wu = (xg + 1) / 2, wg / 2                       # quadrature on [0, 1]

    def phi(i, x):
        return np.sqrt(2 * i + 1) * npleg.Legendre.basis(i)(2 * x - 1)

    H0 = np.array([[np.sum(wu * phi(i, u / 2) * phi(j, u)) for j in range(r)] for i in range(r)]) / np.sqrt(2)
    H1 = np.array([[np.sum(wu * phi(i, (u + 1) / 2) * phi(j, u)) for j in range(r)] for i in range(r)]) / np.sqrt(2)
    M = np.hstack([H0, H1])
    q, _ = np.linalg.qr(M.T, mode="complete")
    G = q[:, r:].T
    return H0, H1, G[:, :r], G[:, r:]


def discrete_poly_basis(r=R_MULT):
    """Orthogonal r x r matrix whose rows are discrete orthonormal polynomials of degree 0..r-1 on r points."""
    v = np.vander(np.linspace(-1, 1, r), r, increasing=True)
    q, rr = np.linalg.qr(v)
    q = q * np.sign(np.diag(rr))
    return q.T


FILTERS = legendre_filters()
PREFILTER = discrete_poly_basis()


def lmwt(x, levels=max(LMWT_LEVELS)):
    H0, H1, G0, G1 = FILTERS
    s = np.asarray(x, float).reshape(-1, R_MULT) @ PREFILTER.T
    details = []
    for _ in range(levels):
        a, b = s[0::2], s[1::2]
        details.append(a @ G0.T + b @ G1.T)
        s = a @ H0.T + b @ H1.T
    return details, s


def ilmwt(details, s):
    H0, H1, G0, G1 = FILTERS
    for d in reversed(details):
        out = np.empty((2 * len(s), R_MULT))
        out[0::2] = s @ H0 + d @ G0
        out[1::2] = s @ H1 + d @ G1
        s = out
    return (s @ PREFILTER).ravel()


def lmwt_level_signal(x, level):
    details, s = lmwt(x)
    keep = [d if j == level else np.zeros_like(d) for j, d in enumerate(details, 1)]
    return ilmwt(keep, np.zeros_like(s))


# -- empirical wavelet transform (Gilles 2013) -------------------------------------------------
def _beta(x):
    x = np.clip(x, 0, 1)
    return x ** 4 * (35 - 84 * x + 70 * x ** 2 - 20 * x ** 3)


def ewt_boundaries(y, fs=FS, modes=EWT_MODES):
    a = np.abs(np.fft.rfft(y))
    a = np.convolve(a, np.ones(EWT_SMOOTH) / EWT_SMOOTH, mode="same")
    f = np.fft.rfftfreq(len(y), 1 / fs)
    p, _ = find_peaks(a)
    top = np.sort(p[np.argsort(a[p])[::-1][:modes]])
    return (f[top[:-1]] + f[top[1:]]) / 2


def ewt_filters(bounds, f, fs=FS):
    w = np.concatenate([[0.0], bounds, [fs / 2]])
    gamma = 0.99 * np.min((w[2:-1] - w[1:-2]) / (w[2:-1] + w[1:-2])) if len(bounds) > 1 else 0.2
    gamma = min(gamma, 0.99 * np.min((fs / 2 - bounds) / (fs / 2 + bounds)))
    out = []
    for n in range(len(w) - 1):
        lo, hi = w[n], w[n + 1]
        psi = np.zeros_like(f)
        if n == 0:
            psi[f <= (1 - gamma) * hi] = 1
        else:
            psi[(f >= (1 + gamma) * lo) & (f <= ((1 - gamma) * hi if hi < fs / 2 else fs))] = 1
            m = (f >= (1 - gamma) * lo) & (f <= (1 + gamma) * lo)
            psi[m] = np.sin(np.pi / 2 * _beta((f[m] - (1 - gamma) * lo) / (2 * gamma * lo)))
        if hi < fs / 2:
            m = (f >= (1 - gamma) * hi) & (f <= (1 + gamma) * hi)
            psi[m] = np.cos(np.pi / 2 * _beta((f[m] - (1 - gamma) * hi) / (2 * gamma * hi)))
        out.append((lo, hi, psi))
    return out


def ewt_modes(y, fs=FS):
    f = np.fft.rfftfreq(len(y), 1 / fs)
    Y = np.fft.rfft(y)
    return [(lo, hi, np.fft.irfft(Y * psi, n=len(y))) for lo, hi, psi in ewt_filters(ewt_boundaries(y, fs), f, fs)]


# -- arms --------------------------------------------------------------------------------------
def candidates(y):
    """Band signals per family: list of (label, signal)."""
    fk = [(f"L{lv} {lo:.0f}-{hi:.0f}", bandpass(y, lo, hi)) for lv, lo, hi in fk_bands()]
    lm = [(f"level {j}", lmwt_level_signal(y, j)) for j in LMWT_LEVELS]
    ew = [(f"{lo:.0f}-{hi:.0f}", s) for lo, hi, s in ewt_modes(y) if hi > MIN_CARRIER]
    return {"FK": fk, "LMWT": lm, "EWT": ew}


def snapshot_arms(y, freqs):
    y = y - y.mean()
    out = {}
    for fam, cands in candidates(y).items():
        spectra = [envelope_spectrum(s) for _, s in cands]
        k = int(np.argmax([spectral_kurtosis(s) for _, s in cands]))
        e = int(np.argmax([rer(f, a, freqs) for f, a in spectra]))
        out[f"{fam}-K" if fam != "FK" else "FK"] = (cands[k][0], spectra[k])
        out[f"{fam}-RER"] = (cands[e][0], spectra[e])
    out["RAW"] = ("raw", envelope_spectrum(y))
    return out


def read_snapshot(path):
    return np.loadtxt(path, delimiter=",", skiprows=1)


def bearing_dirs(root):
    for cond, fr in CONDITIONS.items():
        for b in sorted((Path(root) / cond).glob("Bearing*")):
            yield b.name.replace("Bearing", ""), fr, b


def analyse(root):
    rows = []
    for name, fr, d in bearing_dirs(root):
        freqs = char_freqs(fr)
        files = sorted(d.glob("*.csv"), key=lambda p: int(p.stem))[-LAST:]
        for p in files:
            x = read_snapshot(p)
            for ch, col in (("horizontal", 0), ("vertical", 1)):
                arms = snapshot_arms(x[:, col], freqs)
                for arm, (label, (f, a)) in arms.items():
                    for el in FAULTS[name]:
                        rows.append({"bearing": name, "snapshot": int(p.stem), "channel": ch, "arm": arm,
                                     "selected": label, "element": el,
                                     "score": float(np.log10(peak_ratio(f, a, freqs[el]))),
                                     "detected": detected(f, a, freqs[el])})
        print(name, len(files), "snapshots", flush=True)
    return rows


def pairs(rows, arm, channel="horizontal"):
    out = {}
    for r in rows:
        if r["arm"] == arm and r["channel"] == channel:
            out.setdefault((r["bearing"], r["element"]), []).append(r)
    return {k: {"score": float(np.median([r["score"] for r in v])),
                "detected": sum(r["detected"] for r in v) >= DETECT_MAJORITY,
                "selected": sorted({r["selected"] for r in v})} for k, v in out.items()}


def delta(rows, a, b, rng, channel="horizontal", subset=None):
    pa, pb = pairs(rows, a, channel), pairs(rows, b, channel)
    keys = [k for k in sorted(pa) if subset is None or k[0] in subset]
    d = {k: pa[k]["score"] - pb[k]["score"] for k in keys}
    bearings = sorted({k[0] for k in keys})
    by = {bb: [d[k] for k in keys if k[0] == bb] for bb in bearings}
    boots = []
    for _ in range(RESAMPLES):
        pick = rng.choice(bearings, len(bearings))
        boots.append(np.mean([v for bb in pick for v in by[bb]]))
    return {"candidate": a, "comparator": b, "pairs": len(keys), "delta": float(np.mean(list(d.values()))),
            "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "pairs_ahead": int(sum(v > 0 for v in d.values()))}


def decide(primary, control, det_cand, det_base):
    lo, hi = primary["ci95"]
    c1 = primary["delta"] >= MARGIN and lo > 0
    c2 = control["ci95"][0] > 0
    c3 = det_cand >= det_base
    if c1 and c2 and c3:
        return "niche"
    if hi < MARGIN or (c1 and not c2):
        return "close"
    return "inconclusive"


def summarize(rows):
    rng = np.random.default_rng(SEED)
    res = {"arms": {}, "comparisons": {}}
    for ch in ("horizontal", "vertical"):
        for arm in ARMS:
            p = pairs(rows, arm, ch)
            res["arms"][f"{arm} {ch}"] = {
                "mean_score": float(np.mean([v["score"] for v in p.values()])),
                "detected": int(sum(v["detected"] for v in p.values())), "pairs": len(p),
                "compound_detected": int(sum(v["detected"] for k, v in p.items() if k[0] in COMPOUND)),
                "per_pair": {f"{k[0]} {k[1]}": v for k, v in p.items()}}
    c = res["comparisons"]
    c["primary LMWT-RER vs FK"] = delta(rows, "LMWT-RER", "FK", rng)
    c["control LMWT-RER vs FK-RER"] = delta(rows, "LMWT-RER", "FK-RER", rng)
    for a, b in (("LMWT-K", "FK"), ("FK-RER", "FK"), ("EWT-RER", "FK"), ("LMWT-RER", "EWT-RER"), ("RAW", "FK")):
        c[f"{a} vs {b}"] = delta(rows, a, b, rng)
    c["compound LMWT-RER vs FK"] = delta(rows, "LMWT-RER", "FK", rng, subset=COMPOUND)
    c["vertical LMWT-RER vs FK"] = delta(rows, "LMWT-RER", "FK", rng, channel="vertical")
    res["decision"] = decide(c["primary LMWT-RER vs FK"], c["control LMWT-RER vs FK-RER"],
                             res["arms"]["LMWT-RER horizontal"]["detected"], res["arms"]["FK horizontal"]["detected"])
    return res


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = analyse(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "snapshots.json").write_text(json.dumps(rows))
    res = summarize(rows)
    (out / "w2.json").write_text(json.dumps(res, indent=1))
    for k, v in res["arms"].items():
        print(f"{k:24s} score {v['mean_score']:.3f} detected {v['detected']}/{v['pairs']} (compound {v['compound_detected']}/6)")
    for k, v in res["comparisons"].items():
        print(f"{k:32s} {v['delta']:+.3f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}] pairs ahead {v['pairs_ahead']}/{v['pairs']}")
    print("decision:", res["decision"])


if __name__ == "__main__":
    main()
