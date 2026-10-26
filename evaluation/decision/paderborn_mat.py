"""Read Paderborn KAt .mat files (MATLAB 5; structure inspected on development bearing K002).

Each file holds one struct named after the file. `Y` lists channels (Name, Raster, Data,
empty Unit); `X` holds one time axis per raster. The vibration channel is `vibration_1`
on raster `HostService`. The file name is checked against the measured speed, torque and
radial force (raster `Mech_4kHz`) instead of being trusted.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

NAME = re.compile(r"^(N\d\d_M\d\d_F\d\d)_(K[A-Z]?\d\d\d?|K[A-Z]\d\d)_(\d+)\.mat$")
# Data-quality rule (decided on development bearing K002 before any held-out file was read):
# some files hold extra samples within the same 4 s span (effective rates up to ~73 kHz),
# so they are not uniformly sampled at 64 kHz. Files outside 0.01% are excluded.
RATE_TOLERANCE_HZ = 6.4
NOMINAL = {"N15_M07_F10": (1500, 0.7, 1000), "N09_M07_F10": (900, 0.7, 1000),
           "N15_M01_F10": (1500, 0.1, 1000), "N15_M07_F04": (1500, 0.7, 400)}


def read(path: Path) -> dict:
    import scipy.io as sio
    m = sio.loadmat(path, squeeze_me=False)
    keys = [k for k in m if not k.startswith("__")]
    if len(keys) != 1:
        raise ValueError(f"{path.name}: expected one struct, found {keys}")
    s = m[keys[0]][0, 0]
    channels = {}
    for e in s["Y"][0]:
        channels[str(e["Name"].ravel()[0])] = (str(e["Raster"].ravel()[0]), e["Data"].ravel().astype(np.float64),
                                               str(e["Unit"].ravel()[0]) if e["Unit"].size else "")
    axes = {str(e["Raster"].ravel()[0]): e["Data"].ravel().astype(np.float64) for e in s["X"][0]}
    return {"channels": channels, "axes": axes}


def load_file(path: Path):
    """Returns (setting, bearing, measurement, vibration samples, checks)."""
    match = NAME.match(path.name)
    if not match:
        raise ValueError(f"unexpected file name {path.name}")
    setting, bearing, measurement = match.group(1), match.group(2), int(match.group(3))
    d = read(path)
    raster, vib, unit = d["channels"]["vibration_1"]
    t = d["axes"][raster]
    # Stored time stamps are quantized (per-step jitter of about 3%); the span is exact.
    rate = (len(t) - 1) / (t[-1] - t[0])
    speed = float(np.mean(d["channels"]["speed"][1]))
    torque = float(np.mean(d["channels"]["torque"][1]))
    force = float(np.mean(d["channels"]["force"][1]))
    checks = {"vibration_rate_hz": float(rate), "rate_ok": bool(abs(rate - 64000) <= RATE_TOLERANCE_HZ), "vibration_unit": unit, "samples": int(len(vib)),
              "speed_mean": speed, "torque_mean": torque, "force_mean": force}
    return setting, bearing, measurement, vib, checks


def load_recordings(root: Path, checks_out: list | None = None, bearings=None):
    """Yields (bearing, setting, measurement, samples) for every accepted .mat file under root."""
    for path in sorted(root.glob("K*/*.mat")):
        if bearings is not None and path.parent.name not in bearings:
            continue
        setting, bearing, measurement, vib, checks = load_file(path)
        if checks_out is not None:
            checks_out.append({"file": path.name, **checks})
        if checks["rate_ok"]:
            yield bearing, setting, measurement, vib
