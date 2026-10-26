"""Waveform and diagnostic fidelity metrics (PROTOCOL.md). Decoded vs original, same samples."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt, welch

# Named detectors, fixed before any benchmark result (PROTOCOL.md):
#   D-KURT: kurtosis of the band-passed signal (4th-order Butterworth, zero-phase).
#   D-ENV:  envelope-spectrum peak ratio = max / median of |FFT(envelope)| over the fault range.
BANDS = {"paderborn": (2000.0, 20000.0, 10.0, 500.0), "ims": (2000.0, 9000.0, 10.0, 1000.0)}


def kurtosis(x):
    x = np.asarray(x, float) - np.mean(x)
    s2 = np.mean(x ** 2)
    return float(np.mean(x ** 4) / s2 ** 2) if s2 > 0 else 0.0


def crest(x):
    x = np.asarray(x, float) - np.mean(x)
    r = np.sqrt(np.mean(x ** 2))
    return float(np.max(np.abs(x)) / r) if r > 0 else 0.0


def bandpass(x, rate, lo, hi):
    sos = butter(4, [lo, min(hi, 0.45 * rate)], btype="bandpass", fs=rate, output="sos")
    return sosfiltfilt(sos, np.asarray(x, float))


def envelope_spectrum(x, rate, lo, hi, flo, fhi):
    env = np.abs(hilbert(bandpass(x, rate, lo, hi)))
    env -= env.mean()
    spec = np.abs(np.fft.rfft(env)) / len(env)
    f = np.fft.rfftfreq(len(env), 1 / rate)
    sel = (f >= flo) & (f <= fhi)
    return f[sel], spec[sel]


def detectors(x, rate, dataset):
    lo, hi, flo, fhi = BANDS[dataset]
    _, es = envelope_spectrum(x, rate, lo, hi, flo, fhi)
    med = np.median(es)
    return {"d_kurt": kurtosis(bandpass(x, rate, lo, hi)), "d_env": float(es.max() / med) if med > 0 else 0.0}


def log_spectral_distance(a, b, rate, nperseg=4096):
    """RMS difference of 10*log10 Welch PSDs over bins where the original is above 1e-12 x its max."""
    _, pa = welch(a, rate, nperseg=min(nperseg, len(a)))
    _, pb = welch(b, rate, nperseg=min(nperseg, len(b)))
    keep = pa > pa.max() * 1e-12
    return float(np.sqrt(np.mean((10 * np.log10(pb[keep] + 1e-300) - 10 * np.log10(pa[keep])) ** 2)))


def envelope_lsd(a, b, rate, dataset):
    lo, hi, flo, fhi = BANDS[dataset]
    _, ea = envelope_spectrum(a, rate, lo, hi, flo, fhi)
    _, eb = envelope_spectrum(b, rate, lo, hi, flo, fhi)
    floor = ea.max() * 1e-6
    return float(np.sqrt(np.mean((20 * np.log10(np.maximum(eb, floor)) - 20 * np.log10(np.maximum(ea, floor))) ** 2)))


def compare(orig, dec, rate, dataset):
    orig = np.asarray(orig, float)
    dec = np.asarray(dec, float)
    err = dec - orig
    sd = orig.std()
    out = {"max_abs_err": float(np.abs(err).max()), "nrmse": float(np.sqrt(np.mean(err ** 2)) / sd) if sd > 0 else 0.0,
           "lsd_db": log_spectral_distance(orig, dec, rate),
           "d_kurtosis_rel": (kurtosis(dec) - kurtosis(orig)) / kurtosis(orig),
           "d_crest_rel": (crest(dec) - crest(orig)) / crest(orig)}
    if dataset in BANDS:
        out["env_lsd_db"] = envelope_lsd(orig, dec, rate, dataset)
        do, dd = detectors(orig, rate, dataset), detectors(dec, rate, dataset)
        for k in do:
            out[f"{k}_orig"], out[f"{k}_dec"] = do[k], dd[k]
            out[f"{k}_rel"] = (dd[k] - do[k]) / do[k] if do[k] else 0.0
    return out
