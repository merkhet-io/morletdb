"""Empirical-Bayes wavelet shrinkage with a spike-and-slab prior, and a label-free surprise score (F2, development).

Model per detail level j of an orthogonal DWT, in units of the background noise sigma (z = d / sigma):
    z = theta + e,   e ~ N(0, 1),   theta ~ (1 - p_j) delta_0 + p_j g(theta; s_j)
with slab g one of
    gauss:   N(0, s^2)
    laplace: (1 / 2s) exp(-|theta| / s)
    pareto:  alpha / (2s) (1 + |theta| / s)^-(alpha + 1)     (symmetric Lomax / double Pareto, heavy tail)
p_j and s_j are fitted by marginal maximum likelihood on the recording (on a device: a rolling window), then applied
to every episode; sigma comes from the finest level of a quiet reference or of the recording itself (median absolute
deviation / 0.6745, robust to sparse transients). For each coefficient the posterior probability of signal pi = p m1(z) / (p m1(z) + (1 - p) phi(z)) and the
posterior mean are computed numerically on a grid.

Approximation: keep coefficients with pi > tau, store the posterior mean quantised with step q (in sigma); the
coarsest approximation band is always kept. Size is an entropy estimate (significance map + quantised values).
Surprise of an episode: posterior-weighted excess energy per sample, sum_k pi_k (z_k^2 - 1)_+ / n.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pywt

GRID = np.linspace(-400, 400, 160001)          # theta grid in sigma units (step 0.005)
Z = np.linspace(-400, 400, 16001)              # z grid for tabulated marginals (step 0.05)
P_GRID = np.logspace(-4, -0.02, 30)
S_GRID = np.logspace(-0.5, 2.3, 24)
PHI = lambda x: np.exp(-0.5 * x * x) / np.sqrt(2 * np.pi)  # noqa: E731


def slab_pdf(kind: str, theta: np.ndarray, s: float, alpha: float = 1.5) -> np.ndarray:
    a = np.abs(theta)
    if kind == "gauss":
        return np.exp(-0.5 * (theta / s) ** 2) / (s * np.sqrt(2 * np.pi))
    if kind == "laplace":
        return np.exp(-a / s) / (2 * s)
    if kind == "pareto":
        return alpha / (2 * s) * (1 + a / s) ** (-(alpha + 1))
    raise ValueError(kind)


_TABLES: dict = {}


def tables(kind: str, s: float, alpha: float):
    """m1(z) and the numerator of the posterior mean, tabulated on Z by convolution with the noise density."""
    key = (kind, round(s, 6), alpha)
    if key not in _TABLES:
        step = GRID[1] - GRID[0]
        g = slab_pdf(kind, GRID, s, alpha)
        g = g / (g.sum() * step)                                  # renormalise the truncated tail
        kern_x = np.arange(-8, 8 + step / 2, step)
        k = PHI(kern_x)
        m1 = np.convolve(g, k, mode="same") * step
        tm = np.convolve(g * GRID, k, mode="same") * step
        _TABLES[key] = (np.interp(Z, GRID, m1), np.interp(Z, GRID, tm))
    return _TABLES[key]


def marginal(kind, s, alpha, z):
    m1, tm = tables(kind, s, alpha)
    zc = np.clip(z, Z[0], Z[-1])
    return np.maximum(np.interp(zc, Z, m1), 1e-300), np.interp(zc, Z, tm)


@dataclass
class LevelPrior:
    p: float
    s: float


@dataclass
class Prior:
    kind: str
    alpha: float
    sigma: float
    wavelet: str
    levels: int
    per_level: list          # LevelPrior for detail levels, coarsest first (pywt order after the approximation)


def fit_level(z: np.ndarray, kind: str, alpha: float) -> LevelPrior:
    best, arg = -np.inf, None
    phi = PHI(z)
    for s in S_GRID:
        m1, _ = marginal(kind, s, alpha, z)
        ll = np.log((1 - P_GRID[:, None]) * phi[None, :] + P_GRID[:, None] * m1[None, :]).sum(axis=1)
        i = int(np.argmax(ll))
        if ll[i] > best:
            best, arg = ll[i], LevelPrior(float(P_GRID[i]), float(s))
    return arg


def noise_sigma(x: np.ndarray, wavelet: str) -> float:
    d1 = pywt.wavedec(x, wavelet, level=1)[-1]
    return float(np.median(np.abs(d1)) / 0.6745) or 1e-12


def fit(data: np.ndarray, kind: str, wavelet: str = "sym8", levels: int = 6, alpha: float = 1.5,
        sample: int = 200_000, background: np.ndarray | None = None) -> Prior:
    """sigma from `background` (quiet reference; default: data itself, by the robust MAD); p_j and s_j from `data`
    (the recording, or on a device a rolling window of it)."""
    sigma = noise_sigma(data if background is None else background, wavelet)
    coeffs = pywt.wavedec(data, wavelet, level=levels)
    rng = np.random.default_rng(0)
    per = []
    for d in coeffs[1:]:
        z = d / sigma
        if len(z) > sample:
            z = rng.choice(z, sample, replace=False)
        per.append(fit_level(z, kind, alpha))
    return Prior(kind, alpha, sigma, wavelet, levels, per)


def posterior(prior: Prior, z: np.ndarray, lp: LevelPrior):
    m1, tm = marginal(prior.kind, lp.s, prior.alpha, z)
    m0 = PHI(z)
    num = lp.p * m1
    den = num + (1 - lp.p) * m0
    pi = num / den
    mean = lp.p * tm / den
    return pi, mean


def entropy_bits(ints: np.ndarray) -> float:
    if len(ints) == 0:
        return 0.0
    _, c = np.unique(ints, return_counts=True)
    p = c / c.sum()
    return float(-(c * np.log2(p)).sum())


def map_bits(mask: np.ndarray) -> float:
    n, k = len(mask), int(mask.sum())
    if k in (0, n):
        return 0.0
    p = k / n
    return float(-n * (p * np.log2(p) + (1 - p) * np.log2(1 - p)))


def approximate(x: np.ndarray, prior: Prior, tau: float = 0.5, q: float = 0.5, rule: str = "bayes",
                lam: float | None = None):
    """Returns (reconstruction, bytes per sample, surprise). rule: bayes (pi > tau, posterior mean) or hard
    (|z| > lam, value kept)."""
    sigma = prior.sigma
    coeffs = pywt.wavedec(x, prior.wavelet, level=prior.levels)
    step = q * sigma
    bits = 0.0
    out = [np.round(coeffs[0] / step) * step]
    bits += entropy_bits(np.round(coeffs[0] / step).astype(np.int64))
    excess, n = 0.0, 0
    for d, lp in zip(coeffs[1:], prior.per_level):
        z = d / sigma
        pi, mean = posterior(prior, z, lp)
        excess += float((pi * np.maximum(z * z - 1, 0)).sum())
        n += len(z)
        if rule == "bayes":
            keep = pi > tau
            val = mean * sigma
        elif rule == "select":                      # posterior selects, the observed value is kept (no shrinkage)
            keep = pi > tau
            val = d
        else:
            keep = np.abs(z) > lam
            val = d
        qv = np.round(val[keep] / step).astype(np.int64)
        bits += map_bits(keep) + entropy_bits(qv)
        r = np.zeros_like(d)
        r[keep] = qv * step
        out.append(r)
    rec = pywt.waverec(out, prior.wavelet)[: len(x)]
    return rec, bits / 8 / len(x), excess / max(n, 1)


def surprise(x: np.ndarray, prior: Prior) -> float:
    coeffs = pywt.wavedec(x, prior.wavelet, level=prior.levels)
    excess, n = 0.0, 0
    for d, lp in zip(coeffs[1:], prior.per_level):
        z = d / prior.sigma
        pi, _ = posterior(prior, z, lp)
        excess += float((pi * np.maximum(z * z - 1, 0)).sum())
        n += len(z)
    return excess / max(n, 1)


# ---------------------------------------------------------------- edge approximation with a real coder (F2 focus)
import zlib  # noqa: E402


def _varint(vals: np.ndarray) -> bytes:
    """LEB128 of non-negative int64 values."""
    out = bytearray()
    for v in vals.tolist():
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                out.append(b | 0x80)
            else:
                out.append(b)
                break
    return bytes(out)


def _zigzag(v: np.ndarray) -> np.ndarray:
    return (v << 1) ^ (v >> 63)


def encode(x: np.ndarray, wavelet: str = "sym8", levels: int = 6, lam: float = 3.0, q: float = 1.0,
           per_level_sigma: bool = True, select=None) -> tuple[bytes, np.ndarray]:
    """Wavelet approximation as actual bytes: per detail level, coefficients with |d| > lam * sigma_j are kept,
    quantised with step q * sigma_j (sigma_j: robust MAD of that level if per_level_sigma, else of the finest);
    the coarsest band is quantised with step q * sigma_1. Stream: header, then per band the gaps between kept
    positions and the zigzag values as varints, compressed with zlib level 9. `select(level_index, d, sigma)` may
    replace the threshold rule (posterior selection). Returns (bytes, reconstruction)."""
    coeffs = pywt.wavedec(x, wavelet, level=levels)
    s1 = float(np.median(np.abs(coeffs[-1])) / 0.6745) or 1e-12
    header = [len(x), levels]
    parts, rec = [], []
    a = coeffs[0]
    qa = np.round(a / (q * s1)).astype(np.int64)
    parts.append(_varint(_zigzag(qa)))
    rec.append(qa * q * s1)
    steps = [q * s1]
    for j, d in enumerate(coeffs[1:]):
        sj = (float(np.median(np.abs(d)) / 0.6745) or s1) if per_level_sigma else s1
        keep = select(j, d, sj) if select is not None else np.abs(d) > lam * sj
        idx = np.flatnonzero(keep)
        qv = np.round(d[idx] / (q * sj)).astype(np.int64)
        gaps = np.diff(np.concatenate([[-1], idx])) - 1
        parts.append(_varint(np.array([len(idx)], np.int64)) + _varint(gaps.astype(np.int64)) + _varint(_zigzag(qv)))
        r = np.zeros_like(d)
        r[idx] = qv * q * sj
        rec.append(r)
        steps.append(q * sj)
    blob = np.array(header, np.int64).tobytes() + np.array(steps, np.float32).tobytes() + b"".join(parts)
    comp = zlib.compress(blob, 9)
    out = pywt.waverec(rec, wavelet)[: len(x)]
    return comp, out


def encode_abs(x: np.ndarray, c: float, q: float = 0.5, wavelet: str = "sym8", levels: int = 6,
               select=None) -> tuple[bytes, np.ndarray]:
    """Transform coder: one absolute threshold T = c * std(x) for all detail coefficients (the largest coefficients
    are kept wherever they are, which minimises L2 error for their number in an orthogonal basis), quantisation
    step q * T for every band including the coarsest. Header: length, levels as varints and the step as float32;
    then per band the count, gaps and zigzag values as varints; zlib level 9."""
    coeffs = pywt.wavedec(x, wavelet, level=levels)
    T = c * (float(np.std(x)) or 1e-12)
    step = q * T
    parts = [_varint(np.array([len(x), levels], np.int64)), np.float32(step).tobytes()]
    qa = np.round(coeffs[0] / step).astype(np.int64)
    parts.append(_varint(_zigzag(qa)))
    rec = [qa * step]
    for j, d in enumerate(coeffs[1:]):
        keep = select(j, d) if select is not None else np.abs(d) > T
        idx = np.flatnonzero(keep)
        qv = np.round(d[idx] / step).astype(np.int64)
        gaps = np.diff(np.concatenate([[-1], idx])) - 1
        parts.append(_varint(np.array([len(idx)], np.int64)) + _varint(gaps.astype(np.int64)) + _varint(_zigzag(qv)))
        r = np.zeros_like(d)
        r[idx] = qv * step
        rec.append(r)
    comp = zlib.compress(b"".join(parts), 9)
    return comp, pywt.waverec(rec, wavelet)[: len(x)]
