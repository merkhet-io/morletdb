#!/usr/bin/env python3
"""W1: wavelet vs standard health indicators on run-to-failure bearings (PROTOCOL.md).

  indicators  compute every indicator per snapshot for one dataset role, save to npz
  alarms      apply the frozen alarm rule, write per-bearing alarms and stable leads
  decide      family comparisons and the frozen decision (evaluation role only)

Datasets: IMS (development only; ../../data/ims/npz), FEMTO learning set (development),
FEMTO full test set (evaluation, once). Run with the compression environment (numpy, scipy,
pywavelets) and a release build of the Rust `features` example.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pywt
from scipy import signal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE.parent)]
from retrieval import baseline_features  # noqa: E402

# Frozen alarm rule (PROTOCOL.md).
SMOOTH, CALIB, K_MAD, PERSIST, STABLE = 5, 120, 6.0, 6, 0.5
SK_WINDOWS = (16, 32, 64, 128, 256)
FAMILIES = {"impulsiveness": ("B-SK", ("W-WPK", "W-MSK")), "novelty": ("B-FFT", ("W-MDB", "W-WPN"))}
VECTOR = ("B-FFT", "W-MDB", "W-WPN")
REPORTED = ("RMS", "KURT", "ENV")


# -- datasets ----------------------------------------------------------------------
IMS_FAILING = {("set1", 2), ("set1", 3), ("set2", 0), ("set3", 2)}  # 0-based bearing columns


def ims_units(npz_dir: Path):
    """(name, rate, snapshots in g) for every IMS bearing; failing ones are flagged in the name."""
    for s in ("set1", "set2", "set3"):
        d = np.load(npz_dir / f"{s}.npz")
        x = d["x"].astype(float) * float(d["step"])
        for b in range(x.shape[2]):
            tag = "failing" if (s, b) in IMS_FAILING else "other"
            yield f"ims-{s}-b{b + 1}-{tag}", 20000.0, x[:, :, b]


FEMTO_DEV = ("Bearing1_1", "Bearing1_2", "Bearing2_1", "Bearing2_2", "Bearing3_1", "Bearing3_2")
FEMTO_EVAL = ("Bearing1_3", "Bearing1_4", "Bearing1_5", "Bearing1_6", "Bearing1_7", "Bearing2_3",
              "Bearing2_4", "Bearing2_5", "Bearing2_6", "Bearing2_7", "Bearing3_3")


def femto_units(root: Path, names, channel):
    """Snapshots from acc_*.csv: hour, minute, second, microsecond, horizontal, vertical.

    Development reads Learning_set, evaluation Full_Test_Set; the truncated Test_set has
    directories with the same names and is not used."""
    col = {"horizontal": 4, "vertical": 5}[channel]
    subset = "Learning_set" if names == FEMTO_DEV else "Full_Test_Set"
    for name in names:
        files = sorted((root / subset / name).glob("acc_*.csv"))
        if not files:
            raise FileNotFoundError(f"{subset}/{name}: no acc_*.csv files")
        snaps = []
        for f in files:
            text = f.read_text()
            sep = ";" if ";" in text.splitlines()[0] else ","
            snaps.append(np.loadtxt(f, delimiter=sep, usecols=col))
        lengths = {len(s) for s in snaps}
        if lengths != {2560}:
            raise ValueError(f"{name}: snapshot lengths {sorted(lengths)}")
        yield f"femto-{name}-{channel}", 25600.0, np.stack(snaps)


# -- indicators --------------------------------------------------------------------
def kurt(v):
    v = v - v.mean()
    m2 = np.mean(v ** 2)
    return float(np.mean(v ** 4) / m2 ** 2) if m2 > 0 else 0.0


def spectral_kurtosis(coef):
    """SK per row of complex coefficients over time: E|X|^4 / (E|X|^2)^2 - 2."""
    p = np.abs(coef) ** 2
    m2 = p.mean(-1)
    return np.where(m2 > 0, (p ** 2).mean(-1) / np.where(m2 > 0, m2, 1) ** 2 - 2, 0.0)


def b_sk(x, rate):
    best = -np.inf
    for nw in SK_WINDOWS:
        f, _, z = signal.stft(x, rate, window="hann", nperseg=nw, noverlap=nw // 2, boundary=None, padded=False)
        best = max(best, float(spectral_kurtosis(z[f > 200]).max()))
    return best


def packet_nodes(x):
    wp = pywt.WaveletPacket(x, "sym8", mode="symmetric", maxlevel=4)
    return [n.data for n in wp.get_level(4, order="natural")]


def morlet_cwt(x, rate, freqs, w0=6.0):
    """Analytic complex Morlet CWT via FFT; one row per centre frequency."""
    n = len(x)
    omega = 2 * np.pi * np.fft.fftfreq(n, 1 / rate)
    xf = np.fft.fft(x - x.mean())
    out = np.empty((len(freqs), n), complex)
    for i, f in enumerate(freqs):
        s = w0 / (2 * np.pi * f)
        psi = np.pi ** -0.25 * np.exp(-0.5 * (s * omega - w0) ** 2) * (omega > 0)
        out[i] = np.fft.ifft(xf * psi * np.sqrt(2 * np.pi * s * rate))
    return out


def envelope_ratio(x, rate):
    hi = min(12000.0, 0.45 * rate)
    sos = signal.butter(4, [2000.0, hi], "bandpass", fs=rate, output="sos")
    env = np.abs(signal.hilbert(signal.sosfiltfilt(sos, x)))
    f = np.fft.rfftfreq(len(env), 1 / rate)
    spec = np.abs(np.fft.rfft(env - env.mean()))
    band = spec[(f >= 10) & (f <= 1000)]
    med = np.median(band)
    return float(band.max() / med) if med > 0 else 0.0


def morlet_db(snaps, rate, binary: Path):
    out = []
    for lo in range(0, len(snaps), 128):
        payload = "".join(json.dumps({"samples": w.tolist(), "sample_rate_hz": rate, "normalization": "none"}) + "\n"
                          for w in snaps[lo:lo + 128])
        r = subprocess.run([str(binary)], input=payload, text=True, capture_output=True, check=True)
        rows = [json.loads(line) for line in r.stdout.splitlines()]
        if len(rows) != len(snaps[lo:lo + 128]) or {row["version"] for row in rows} != {"morlet-energy-v1"}:
            raise ValueError("unexpected extractor output")
        out += [row["morlet"] for row in rows]
    return np.array(out)


def indicators(snaps, rate, binary: Path):
    freqs = np.geomspace(200.0, min(10000.0, 0.45 * rate), 32)
    res = {k: [] for k in ("B-SK", "W-WPK", "W-MSK", "B-FFT", "W-WPN", *REPORTED)}
    for x in snaps:
        x = x - x.mean()
        base = baseline_features(x, rate, "none")
        nodes = packet_nodes(x)
        res["B-SK"].append(b_sk(x, rate))
        res["W-WPK"].append(max(kurt(c) for c in nodes))
        res["W-MSK"].append(float(spectral_kurtosis(morlet_cwt(x, rate, freqs)).max()))
        res["B-FFT"].append(np.r_[base["fft_bands"], base["statistics"]])
        res["W-WPN"].append(np.r_[np.log1p([np.mean(c ** 2) for c in nodes]), base["statistics"]])
        res["RMS"].append(float(np.sqrt(np.mean(x ** 2))))
        res["KURT"].append(kurt(x))
        res["ENV"].append(envelope_ratio(x, rate))
    res = {k: np.array(v, float) for k, v in res.items()}
    res["W-MDB"] = morlet_db(snaps, rate, binary)
    return res


# -- alarm rule --------------------------------------------------------------------
def ledoit_wolf(x):
    """Shrunk covariance (Ledoit & Wolf 2004) of rows of x."""
    n, p = x.shape
    xc = x - x.mean(0)
    s = xc.T @ xc / n
    mu = np.trace(s) / p
    d2 = np.sum((s - mu * np.eye(p)) ** 2)
    b2 = min(sum(np.sum((np.outer(r, r) - s) ** 2) for r in xc) / n ** 2, d2)
    k = b2 / d2 if d2 > 0 else 1.0
    return k * mu * np.eye(p) + (1 - k) * s


def novelty(vectors):
    cal = vectors[:CALIB]
    inv = np.linalg.pinv(ledoit_wolf(cal))
    d = vectors - cal.mean(0)
    return np.sqrt(np.einsum("ij,jk,ik->i", d, inv, d))


def causal_median(v, w=SMOOTH):
    return np.array([np.median(v[max(0, i - w + 1):i + 1]) for i in range(len(v))])


def alarm(raw):
    """Frozen rule: causal median, MAD threshold from calibration, persistence, stability, lead."""
    v = causal_median(np.asarray(raw, float))
    n = len(v)
    if n <= CALIB + PERSIST:
        return {"alarm": None, "stable": False, "lead": 0.0, "n": n}
    cal = v[:CALIB]
    med = float(np.median(cal))
    thr = med + K_MAD * 1.4826 * float(np.median(np.abs(cal - med)))
    above = v > thr
    for t in range(CALIB, n - PERSIST + 1):
        if above[t:t + PERSIST].all():
            stable = bool(above[t:].mean() >= STABLE)
            lead = (n - 1 - t) / (n - 1 - (CALIB - 1)) if stable else 0.0
            return {"alarm": t, "stable": stable, "lead": float(lead), "n": n, "threshold": thr,
                    "fraction_above_after": float(above[t:].mean())}
    return {"alarm": None, "stable": False, "lead": 0.0, "n": n, "threshold": thr}


def unit_alarms(ind):
    out = {}
    for k, v in ind.items():
        out[k] = alarm(novelty(v) if k in VECTOR else v)
    return out


# -- commands ----------------------------------------------------------------------
def cmd_indicators(a):
    a.out.mkdir(parents=True, exist_ok=True)
    if a.role == "ims":
        units = ims_units(a.ims)
    else:
        names = FEMTO_DEV if a.role == "femto-dev" else FEMTO_EVAL
        units = femto_units(a.femto, names, a.channel)
    for name, rate, snaps in units:
        path = a.out / f"{name}.npz"
        if path.exists():
            continue
        ind = indicators(snaps, rate, a.features_bin)
        np.savez(path, rate=rate, n=len(snaps), **ind)
        print(name, len(snaps), flush=True)


def cmd_alarms(a):
    rows = {}
    for path in sorted(a.out.glob("*.npz")):
        d = np.load(path)
        rows[path.stem] = unit_alarms({k: d[k] for k in d.files if k not in ("rate", "n")})
    (a.out / "alarms.json").write_text(json.dumps(rows, indent=1))
    keys = [*FAMILIES["impulsiveness"][1], "B-SK", "W-MDB", "W-WPN", "B-FFT", *REPORTED]
    print("unit".ljust(34) + "".join(k.rjust(8) for k in keys))
    for u, r in rows.items():
        cells = [("-" if r[k]["alarm"] is None else f"{r[k]['lead']:.2f}" + ("" if r[k]["stable"] else "u")) for k in keys]
        print(u.ljust(34) + "".join(c.rjust(8) for c in cells))
    print("cells: stable lead (fraction of post-calibration life); 'u' = unstable alarm (lead 0); '-' = no alarm")


def bootstrap_diff(a, b, resamples=2000, seed=7):
    a, b = np.asarray(a), np.asarray(b)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), (resamples, len(a)))
    d = (a[idx] - b[idx]).mean(1)
    return float((a - b).mean()), [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def cmd_decide(a):
    rows = json.loads((a.out / "alarms.json").read_text())
    pick = json.loads(a.candidates.read_text())  # {"impulsiveness": "W-...", "novelty": "W-..."}
    res = {"units": len(rows), "families": {}}
    for fam, (base, _) in FAMILIES.items():
        cand = pick[fam]
        lc = [r[cand]["lead"] for r in rows.values()]
        lb = [r[base]["lead"] for r in rows.values()]
        unstable = {m: sum(r[m]["alarm"] is not None and not r[m]["stable"] for r in rows.values()) for m in (cand, base)}
        delta, ci = bootstrap_diff(lc, lb)
        niche = ci[0] > 0 and delta >= 0.05 and unstable[cand] <= unstable[base] + 1
        res["families"][fam] = {"candidate": cand, "baseline": base, "delta": delta, "ci95": ci,
                                "unstable": unstable, "niche": bool(niche), "closed": ci[1] < 0.05}
    f = res["families"].values()
    res["decision"] = "niche" if any(x["niche"] for x in f) else "close" if all(x["closed"] for x in f) else "inconclusive"
    (a.out / "decision.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("indicators")
    i.add_argument("--role", choices=["ims", "femto-dev", "femto-eval"], required=True)
    i.add_argument("--femto", type=Path, help="extracted FEMTO archive root")
    i.add_argument("--ims", type=Path, default=ROOT / "data/ims/npz", help="IMS npz directory (prepare_ims.py)")
    i.add_argument("--channel", choices=["horizontal", "vertical"], default="horizontal")
    i.add_argument("--features-bin", type=Path, default=ROOT / "target/release/examples/features")
    i.add_argument("--out", type=Path, required=True)
    al = sub.add_parser("alarms")
    al.add_argument("--out", type=Path, required=True)
    d = sub.add_parser("decide")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--candidates", type=Path, required=True)
    a = p.parse_args()
    {"indicators": cmd_indicators, "alarms": cmd_alarms, "decide": cmd_decide}[a.cmd](a)


if __name__ == "__main__":
    main()
