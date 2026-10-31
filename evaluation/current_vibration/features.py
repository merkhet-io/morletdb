#!/usr/bin/env python3
"""CV1 window features (PROTOCOL.md): vibration band energies (target), operating-condition
features (B) and current features (C) for every accepted Paderborn recording.

  .venv-seismic/bin/python -I evaluation/current_vibration/features.py \
      data/paderborn/zenodo-15845309/extracted evaluation/current_vibration/results/features.npz
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "decision"))
import paderborn_mat as pm  # noqa: E402

FS = 64000.0
WIN = 32000                      # 0.5 s
WINDOWS = 8
VIB_BANDS = [(10, 1000), (1000, 5000), (5000, 15000), (15000, 30000)]
CUR_BANDS = [(0, 200), (200, 1000), (1000, 5000), (5000, 15000), (15000, 30000)]
FLOOR = 1e-12
SETTINGS = list(pm.NOMINAL)


def band_log_energy(x, bands):
    x = x - x.mean()
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / FS)
    return [float(np.log10(p[(f >= lo) & (f < hi)].sum() + FLOOR)) for lo, hi in bands]


def current_features(c):
    c = c - c.mean()
    rms = np.sqrt((c ** 2).mean())
    kurt = float(((c / rms) ** 4).mean()) if rms > 0 else 0.0
    return [float(np.log10(rms + FLOOR)), float(np.abs(c).max() / rms) if rms > 0 else 0.0, kurt] + band_log_energy(c, CUR_BANDS)


def windows(n):
    """Start indices of the complete non-overlapping windows on the HostService raster: 8 for a
    full 256,000-sample recording; 53 files are 1-4 samples short and lose their 8th window."""
    return [i * WIN for i in range(WINDOWS) if (i + 1) * WIN <= n]


def mech_span(t_mech, t_host, a, b):
    """Mechanical samples whose time lies in the HostService window of samples [a, b).
    Window times come from the raster start and the nominal rate: stored stamps are quantized
    (about 3% jitter per step) and the last window may end at the final sample."""
    t0 = t_host[0]
    return (t_mech >= t0 + a / FS) & (t_mech < t0 + b / FS)


def recording(path):
    setting, bearing, measurement, _, checks = pm.load_file(path)
    if not checks["rate_ok"]:
        return {"file": path.name, "excluded": True}
    d = pm.read(path)
    ch = d["channels"]
    rv, vib, _ = ch["vibration_1"]
    r1, i1, _ = ch["phase_current_1"]
    r2, i2, _ = ch["phase_current_2"]
    assert rv == r1 == r2 == "HostService" and len(vib) == len(i1) == len(i2)
    t_host = d["axes"]["HostService"]
    t_mech = d["axes"][ch["speed"][0]]
    rows = []
    for a in windows(len(vib)):
        b = a + WIN
        m = mech_span(t_mech, t_host, a, b)
        mech = [f(ch[k][1][m]) for k in ("speed", "torque", "force") for f in (np.mean, np.std)]
        onehot = [float(setting == s) for s in SETTINGS]
        dv, di = np.diff(vib[a:b]), np.diff(i1[a:b])
        rows.append({"B": onehot + mech, "C": current_features(i1[a:b]) + current_features(i2[a:b]),
                     "Y": band_log_energy(vib[a:b], VIB_BANDS),
                     "resid_corr": float(np.corrcoef(dv, di)[0, 1]), "mech_samples": int(m.sum())})
    return {"file": path.name, "excluded": False, "bearing": bearing, "setting": setting,
            "measurement": measurement, "rows": rows}


def main():
    root, out = Path(sys.argv[1]), Path(sys.argv[2])
    paths = sorted(root.glob("K*/*.mat"))
    with ProcessPoolExecutor() as pool:
        recs = list(pool.map(recording, paths, chunksize=4))
    kept = [r for r in recs if not r["excluded"]]
    flat = [(r, w, row) for r in kept for w, row in enumerate(r["rows"])]
    np.savez_compressed(
        out,
        bearing=np.array([r["bearing"] for r, _, _ in flat]), setting=np.array([r["setting"] for r, _, _ in flat]),
        measurement=np.array([r["measurement"] for r, _, _ in flat]), window=np.array([w for _, w, _ in flat]),
        B=np.array([row["B"] for _, _, row in flat]), C=np.array([row["C"] for _, _, row in flat]),
        Y=np.array([row["Y"] for _, _, row in flat]), resid_corr=np.array([row["resid_corr"] for _, _, row in flat]),
        mech_samples=np.array([row["mech_samples"] for _, _, row in flat]),
        excluded=np.array([r["file"] for r in recs if r["excluded"]]))
    print(len(paths), "files,", len(kept), "kept,", len(recs) - len(kept), "excluded,", len(flat), "windows")


if __name__ == "__main__":
    main()
