"""Orthonormal periodized discrete wavelet transform (research module).

Independent NumPy implementation; tests compare it with PyWavelets
(`mode="periodization"`) and analytical fixtures. It is NOT the frozen
Morlet energy extractor: Morlet energies and localized cells are pooled,
non-reconstructive summaries, whereas this transform is exactly invertible.

Conventions
-----------
- Analysis: a[i] = sum_j h[j] x[(2i + L/2 - j) mod N], d[i] likewise with g,
  matching PyWavelets' periodization layout (L = filter length).
- The analysis operator is orthonormal on R^N, so synthesis is its adjoint and
  energy is preserved exactly (Parseval) up to floating-point error.
- Signals whose length is not a multiple of 2**levels are extended to the next
  multiple by symmetric (half-sample) reflection. The extension length is part of
  the representation spec; reconstruction crops it again. Coefficients near the
  boundary therefore describe reflected data, never zeros.
"""
from __future__ import annotations

import numpy as np

# Daubechies (1992) 8-tap orthonormal scaling filter "db4" (decomposition low-pass).
DB4_LO = np.array([
    -0.010597401785069, 0.0328830116668852, 0.0308413818355608, -0.1870348117190931,
    -0.0279837694168599, 0.6308807679298589, 0.7148465705529157, 0.2303778133088965,
])
HAAR_LO = np.array([1.0, 1.0]) / np.sqrt(2.0)

FILTERS = {"db4": DB4_LO, "haar": HAAR_LO}


def _filters(family: str) -> tuple[np.ndarray, np.ndarray]:
    if family not in FILTERS:
        raise ValueError(f"unsupported wavelet family {family!r}; use one of {sorted(FILTERS)}")
    lo = FILTERS[family]
    # Quadrature mirror: g[j] = (-1)**(j+1) * h[L-1-j]  (PyWavelets' dec_hi sign convention).
    hi = np.array([(-1) ** (j + 1) * lo[len(lo) - 1 - j] for j in range(len(lo))])
    return lo, hi


def _indices(n: int, taps: int) -> np.ndarray:
    i = np.arange(n // 2)[:, None]
    j = np.arange(taps)[None, :]
    return (2 * i + taps // 2 - j) % n


def analysis_step(x: np.ndarray, family: str) -> tuple[np.ndarray, np.ndarray]:
    if x.ndim != 1 or len(x) % 2:
        raise ValueError("analysis step needs a 1-D signal of even length")
    lo, hi = _filters(family)
    idx = _indices(len(x), len(lo))
    window = x[idx]
    return window @ lo, window @ hi


def synthesis_step(a: np.ndarray, d: np.ndarray, family: str) -> np.ndarray:
    if a.shape != d.shape:
        raise ValueError("approximation and detail must have equal length")
    lo, hi = _filters(family)
    n = 2 * len(a)
    idx = _indices(n, len(lo))
    x = np.zeros(n)
    np.add.at(x, idx, a[:, None] * lo[None, :] + d[:, None] * hi[None, :])
    return x


def padded_length(n: int, levels: int) -> int:
    block = 2 ** levels
    return -(-n // block) * block


def extend(x: np.ndarray, levels: int) -> np.ndarray:
    """Symmetric half-sample reflection up to a multiple of 2**levels."""
    target = padded_length(len(x), levels)
    extra = target - len(x)
    if extra == 0:
        return np.asarray(x, dtype=float).copy()
    if extra > len(x):
        raise ValueError("signal too short for the requested number of levels")
    return np.concatenate([x, x[::-1][:extra]]).astype(float)


def wavedec(x: np.ndarray, family: str, levels: int) -> list[np.ndarray]:
    """Return [a_L, d_L, d_{L-1}, ..., d_1] (coarsest first), like pywt.wavedec."""
    if levels < 1:
        raise ValueError("levels must be >= 1")
    signal = np.asarray(x, dtype=float)
    if len(signal) % (2 ** levels):
        raise ValueError("length must be a multiple of 2**levels; call extend() first")
    details = []
    a = signal
    for _ in range(levels):
        a, d = analysis_step(a, family)
        details.append(d)
    return [a] + details[::-1]


def waverec(coeffs: list[np.ndarray], family: str) -> np.ndarray:
    a = np.asarray(coeffs[0], dtype=float)
    for d in coeffs[1:]:
        a = synthesis_step(a, np.asarray(d, dtype=float), family)
    return a
