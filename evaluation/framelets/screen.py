#!/usr/bin/env python3
"""F1 screen: framelet and multiwavelet features for sleep staging by retrieval (SCREEN.md).

    .venv-seismic/bin/python -I evaluation/framelets/screen.py \
        data/sleep-edfx/1.0.0/sleep-cassette data/sleep-edfx/run evaluation/framelets/results

Development subjects only. Reads S1's stored Morlet/FFT features (data/sleep-edfx/run/features)
and the EDF files; never reads a held-out subject.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import convolve1d
from scipy.signal import butter, sosfiltfilt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "sleep"))
import s1  # noqa: E402  (reader, split, folds, KNN, vote and kappa exactly as in S1)

LEVELS = 6
K = 40
ARMS = ("F", "M", "FR-E", "MW-E", "FR-S", "B-S")
RESAMPLES = 2000

# -- undecimated piecewise-linear B-spline framelet (Ron & Shen) ----------------------------
H0 = np.array([1, 2, 1]) / 4
H1 = np.sqrt(2) / 4 * np.array([1, 0, -1])
H2 = np.array([-1, 2, -1]) / 4


def dilate(h, j):
    out = np.zeros((len(h) - 1) * 2 ** (j - 1) + 1)
    out[::2 ** (j - 1)] = h
    return out


def framelet(x, levels=LEVELS):
    """Rows of x are signals. Returns [(c1, c2) per level], low-pass residual; periodic boundary."""
    low, bands = np.asarray(x, float), []
    for j in range(1, levels + 1):
        bands.append((convolve1d(low, dilate(H1, j), axis=-1, mode="wrap"),
                      convolve1d(low, dilate(H2, j), axis=-1, mode="wrap")))
        low = convolve1d(low, dilate(H0, j), axis=-1, mode="wrap")
    return bands, low


# -- GHM multiwavelet (Geronimo, Hardin & Massopust), orthonormal as written -----------------
R2 = np.sqrt(2)
GHM_C = np.array([[[3 / (5 * R2), 4 / 5], [-1 / 20, -3 / (10 * R2)]],
                  [[3 / (5 * R2), 0], [9 / 20, 1 / R2]],
                  [[0, 0], [9 / 20, -3 / (10 * R2)]],
                  [[0, 0], [-1 / 20, 0]]])
GHM_D = np.array([[[-1 / 20, -3 / (10 * R2)], [1 / (10 * R2), 3 / 10]],
                  [[9 / 20, -1 / R2], [-9 / (10 * R2), 0]],
                  [[9 / 20, -3 / (10 * R2)], [9 / (10 * R2), -3 / 10]],
                  [[-1 / 20, 0], [-1 / (10 * R2), 0]]])


def ghm_step(v):
    """v: (..., N, 2) vector signal, N even. Periodic decimated step -> (s, d), each (..., N/2, 2)."""
    n = v.shape[-2]
    s = np.zeros(v.shape[:-2] + (n // 2, 2))
    d = np.zeros_like(s)
    for k in range(4):
        vk = np.take(v, (2 * np.arange(n // 2) + k) % n, axis=-2)
        s += vk @ GHM_C[k].T
        d += vk @ GHM_D[k].T
    return s, d


def ghm_inverse_step(s, d):
    n = s.shape[-2] * 2
    v = np.zeros(s.shape[:-2] + (n, 2))
    for k in range(4):
        idx = (2 * np.arange(n // 2) + k) % n
        np.add.at(v, (..., idx, slice(None)), s @ GHM_C[k] + d @ GHM_D[k])
    return v


def extend(x, n=3072):
    """Symmetric extension of the rows of x to n samples (3000 -> 3072 for 6 GHM levels)."""
    pad = n - x.shape[-1]
    return np.concatenate([x, x[..., ::-1][..., :pad]], axis=-1)


def ghm(x, levels=LEVELS):
    v = extend(np.asarray(x, float)).reshape(x.shape[:-1] + (-1, 2))
    details = []
    for _ in range(levels):
        v, d = ghm_step(v)
        details.append(d)
    return details, v


# -- features ----------------------------------------------------------------------------------
def log_kurtosis(c):
    c = c - c.mean(-1, keepdims=True)
    m2 = np.mean(c ** 2, -1)
    m4 = np.mean(c ** 4, -1)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(m2 > 0, m4 / np.where(m2 > 0, m2, 1) ** 2, 3.0)
    return np.log(k)


OCTAVES = [(50 / 2 ** j, 50 / 2 ** (j - 1)) for j in range(1, LEVELS + 1)]


def bandpass_sos():
    out = []
    for lo, hi in OCTAVES:
        if hi >= s1.RATE / 2:
            out.append(butter(4, lo, "highpass", fs=s1.RATE, output="sos"))
        else:
            out.append(butter(4, [lo, hi], "bandpass", fs=s1.RATE, output="sos"))
    return out


SOS = bandpass_sos()


def epoch_features(e):
    """e: (n, 3000) epochs in µV -> dict of band/shape blocks."""
    bands, _ = framelet(e)
    fr_e = np.column_stack([np.log1p(np.mean(c ** 2, -1)) for pair in bands for c in pair])
    fr_k = np.column_stack([log_kurtosis(c1) for c1, _ in bands])
    details, _ = ghm(e)
    mw_e = np.column_stack([np.log1p(np.mean(d[..., i] ** 2, -1)) for d in details for i in (0, 1)])
    bp_k = np.column_stack([log_kurtosis(sosfiltfilt(sos, e, axis=-1)) for sos in SOS])
    return {"fr_e": fr_e, "fr_k": fr_k, "mw_e": mw_e, "bp_k": bp_k}


def night_blocks(data, run, subject):
    out = []
    for night, psg, hyp in s1.night_files(data, subject):
        x, _, idx, y = s1.epochs_of_night(psg, hyp)
        z = np.load(Path(run) / "features" / f"SC4{subject:02d}{night}.npz")
        if not np.array_equal(z["epoch"], idx) or not np.array_equal(z["y"], y):
            raise SystemExit(f"SC4{subject:02d}{night}: epochs differ from S1's stored features")
        e = np.stack([x[i * s1.EPOCH:(i + 1) * s1.EPOCH] for i in idx])
        f = epoch_features(e)
        stats = z["morlet"][:, 12:]
        out.append({"subject": subject, "y": y,
                    "F": z["fft"], "M": z["morlet"],
                    "FR-E": np.hstack([f["fr_e"], stats]), "MW-E": np.hstack([f["mw_e"], stats]),
                    "FR-S": np.hstack([f["fr_e"], stats, f["fr_k"]]),
                    "B-S": np.hstack([z["fft"], f["bp_k"]])})
    return out


# -- CV ----------------------------------------------------------------------------------------
def cv(nights, order):
    folds = {s: i % s1.FOLDS for i, s in enumerate(order)}
    y = np.concatenate([n["y"] for n in nights])
    g = np.concatenate([np.full(len(n["y"]), n["subject"]) for n in nights])
    fold = np.array([folds[int(s)] for s in g])
    res = {}
    for arm in ARMS:
        x = np.vstack([n[arm] for n in nights])
        if not np.all(np.isfinite(x)):
            raise SystemExit(f"{arm}: non-finite features")
        x = s1.standardizer(x)(x)
        per_subject, per_fold = {}, []
        for f in range(s1.FOLDS):
            tr, te = fold != f, fold == f
            p = s1.vote(y[tr][s1.knn_neighbours(x[tr], x[te], K)])
            ks = s1.per_subject_kappa(y[te], p, g[te])
            per_subject.update(ks)
            per_fold.append(float(np.mean(list(ks.values()))))
        res[arm] = {"dims": int(x.shape[1]), "mean_kappa": float(np.mean(list(per_subject.values()))),
                    "per_fold": per_fold, "per_subject": per_subject}
        print(arm, x.shape[1], round(res[arm]["mean_kappa"], 4), [round(v, 3) for v in per_fold], flush=True)
    return res


def compare(res, a, b, rng):
    subjects = sorted(res[a]["per_subject"])
    d = np.array([res[a]["per_subject"][s] - res[b]["per_subject"][s] for s in subjects])
    boots = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(RESAMPLES)]
    folds_ahead = sum(x > y for x, y in zip(res[a]["per_fold"], res[b]["per_fold"]))
    return {"candidate": a, "comparator": b, "delta": float(d.mean()),
            "ci95_descriptive": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "folds_ahead": int(folds_ahead), "subjects_ahead": int(np.sum(d > 0))}


def main():
    data, run, out = (Path(a) for a in sys.argv[1:4])
    split = s1.load_split()
    nights = []
    for s in split["development"]:
        nights += night_blocks(data, run, s)
        print("subject", s, flush=True)
    res = cv(nights, split["development"])
    rng = np.random.default_rng(s1.SEED)
    comps = {c: compare(res, c, "F", rng) for c in ARMS if c != "F"}
    comps["FR-S vs B-S"] = compare(res, "FR-S", "B-S", rng)
    advance = {}
    for c in ("FR-E", "MW-E", "FR-S"):
        ok = comps[c]["delta"] >= 0.02 and comps[c]["folds_ahead"] >= 4
        if c == "FR-S":
            ok = ok and comps["FR-S vs B-S"]["delta"] >= 0.01
        advance[c] = bool(ok)
    result = {"screen": "F1", "k": K, "development_subjects": len(split["development"]),
              "epochs": int(sum(len(n["y"]) for n in nights)), "arms": res, "comparisons": comps,
              "advance": advance}
    out.mkdir(parents=True, exist_ok=True)
    (out / "screen.json").write_text(json.dumps(result, indent=1))
    for name, c in comps.items():
        print(f"{name if ' vs ' in name else name + ' vs F'}: {c['delta']:+.4f} "
              f"[{c['ci95_descriptive'][0]:+.4f}, {c['ci95_descriptive'][1]:+.4f}] folds ahead {c['folds_ahead']}/5")
    print("advance:", advance)


if __name__ == "__main__":
    main()
