#!/usr/bin/env python3
"""F2 H3 (development): encoder throughput and memory on the device, one core.

Input: a .npy of float64 vibration samples. Windows of 65,536 samples (about 1 s at 64 kHz). Three modes:
  fixed   one setting (c, q) per window;
  bisect  rate control by bisection on c for a byte budget (about 8 encodes per window);
  grid    the full development grid (14 c x 4 q = 56 encodes), the harness's rate control.
Reports samples per second and the real-time factor at 64 kHz, and the peak resident memory of the process.

  python bench_pi.py samples.npy [budget]
"""
import json
import platform
import resource
import sys
import time

import numpy as np

import shrink as S

CS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0)
QA = (0.25, 0.5, 1.0, 2.0)
WIN = 65536


def rel_err(x, r):
    return float(np.linalg.norm(x - r) / (np.linalg.norm(x - x.mean()) or 1e-12))


def bisect(x, budget, q=0.5, steps=8):
    """Rate control by bisection on c: the transform is computed once, each try only thresholds, quantises and
    codes; the waveform is reconstructed once, for the chosen setting (on a device it is not needed at all)."""
    coeffs = S.pywt.wavedec(x, "sym8", level=6)
    lo, hi, best = np.log(0.001), np.log(10.0), None
    for _ in range(steps):
        c = float(np.exp((lo + hi) / 2))
        b, _ = S.encode_abs(x, c, q, coeffs=coeffs, reconstruct=False)
        if len(b) / len(x) <= budget:
            best, hi = c, np.log(c)
        else:
            lo = np.log(c)
    return None if best is None else S.encode_abs(x, best, q, coeffs=coeffs)


def grid(x, budget):
    best = None
    for c in CS:
        for q in QA:
            b, r = S.encode_abs(x, c, q)
            if len(b) / len(x) <= budget:
                e = rel_err(x, r)
                if best is None or e < best[0]:
                    best = (e, b, r)
    return best


def main(path, budget=0.1):
    x = np.load(path).astype(float)
    wins = [x[i:i + WIN] for i in range(0, len(x) - WIN + 1, WIN)]
    n = len(wins) * WIN
    out = {"machine": platform.machine(), "python": platform.python_version(), "numpy": np.__version__,
           "windows": len(wins), "samples": n, "budget": budget}
    try:
        out["model"] = open("/proc/device-tree/model").read().strip("\x00")
    except OSError:
        out["model"] = platform.platform()
    for mode in ("fixed", "bisect", "grid"):
        t = time.perf_counter()
        sizes, recs = [], []
        for w in wins:
            if mode == "fixed":
                b, r = S.encode_abs(w, 1.0, 0.5)
                sizes.append(len(b)); recs.append(r)
            elif mode == "bisect":
                r = bisect(w, budget)
                sizes.append(len(r[0]) if r else None); recs.append(r[1] if r else None)
            else:
                r = grid(w, budget)
                sizes.append(len(r[1]) if r else None); recs.append(r[2] if r else None)
        dt = time.perf_counter() - t
        ok = [s for s in sizes if s is not None]
        errs = [rel_err(w, r) for w, r in zip(wins, recs) if r is not None]
        out[mode] = {"seconds": round(dt, 3), "samples_per_s": round(n / dt), "realtime_x_64khz": round(n / dt / 64000, 2),
                     "bytes_per_sample": round(sum(ok) / (len(ok) * WIN), 4) if ok else None, "missed": len(sizes) - len(ok),
                     "median_rel_err": round(float(np.median(errs)), 4) if errs else None}
    out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if platform.system() == "Linux" else 1024 * 1024), 1)
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.1)
