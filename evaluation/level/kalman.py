"""Reference local-level Kalman filter and the smoothing baselines for morlet_level_track.

Written from the model, not from the SQL: level x_k = x_{k-1} + w_k, Var(w_k) = q * hours since the
last valid observation; observation y_k = x_k + v_k, Var(v_k) = r. The first valid observation sets
x = y, Var(x) = r (the diffuse-prior limit). Invalid observations (None/NaN) are reported, not used.
"""
from __future__ import annotations

import math

import numpy as np

MODEL = "local-level-v1"


def track(y, hours, q, r):
    """y: observations (NaN = invalid), hours: time of each observation in hours (non-decreasing).
    Returns a dict of arrays: predicted_level, predicted_level_var, innovation, innovation_var,
    level, level_var, status."""
    if not (q >= 0 and math.isfinite(q)) or not (r > 0 and math.isfinite(r)):
        raise ValueError("q must be finite and >= 0, r finite and > 0")
    n = len(y)
    out = {k: np.full(n, np.nan) for k in ("predicted_level", "predicted_level_var", "innovation",
                                           "innovation_var", "level", "level_var")}
    out["status"] = []
    m = p = last = None
    for k in range(n):
        if m is not None:
            pp = p + q * (hours[k] - last)
            out["predicted_level"][k], out["predicted_level_var"][k] = m, pp
            out["innovation_var"][k] = pp + r
            if np.isfinite(y[k]):
                out["innovation"][k] = y[k] - m
        if not np.isfinite(y[k]):
            out["status"].append("invalid_value")
            continue
        if m is None:
            m, p, status = y[k], r, "initialized"
        else:
            gain = pp / (pp + r)
            m, p, status = m + gain * (y[k] - m), pp * r / (pp + r), "ok"
        last = hours[k]
        out["level"][k], out["level_var"][k] = m, p
        out["status"].append(status)
    return out


def batch_posterior(y, hours, q, r, prior_var=1e6):
    """Filtered means and variances by direct Gaussian conditioning of the joint distribution
    (no recursion): an independent check of track() for valid observations. prior_var stands in
    for the diffuse prior on the first level; its approximation error is about r / prior_var (1e12
    makes the solve ill-conditioned)."""
    y, t = np.asarray(y, float), np.asarray(hours, float)
    n = len(y)
    # Cov(x_i, x_j) = prior_var + q * (min(t_i, t_j) - t_0) for a random walk started at t_0.
    tm = np.minimum.outer(t, t) - t[0]
    cx = prior_var + q * tm
    means, variances = np.empty(n), np.empty(n)
    for k in range(n):
        s = cx[:k + 1, :k + 1] + r * np.eye(k + 1)
        c = cx[k, :k + 1]
        w = np.linalg.solve(s, c)
        means[k] = w @ y[:k + 1]  # prior mean 0 with a huge variance
        variances[k] = cx[k, k] - w @ c
    return means, variances


def log_likelihood(y, hours, q, r):
    """Gaussian log-likelihood of the innovations after the first valid observation."""
    o = track(y, hours, q, r)
    ok = np.isfinite(o["innovation"])
    v, s = o["innovation"][ok], o["innovation_var"][ok]
    return float(-0.5 * np.sum(np.log(2 * np.pi * s) + v * v / s))


def calibrate(y, hours, log_q=np.linspace(-8, 2, 41), log_r=np.linspace(-8, 2, 41)):
    """Maximum-likelihood (q, r) on a fixed log10 grid: reproducible, no optimiser state."""
    best = None
    for a in log_q:
        for b in log_r:
            ll = log_likelihood(y, hours, 10.0 ** a, 10.0 ** b)
            if best is None or ll > best[0]:
                best = (ll, 10.0 ** a, 10.0 ** b)
    return {"q": best[1], "r": best[2], "log_likelihood": best[0]}


def ema(y, alpha):
    """Exponential moving average; its residual is y minus the previous average."""
    out, res, m = np.full(len(y), np.nan), np.full(len(y), np.nan), None
    for k, v in enumerate(y):
        if not np.isfinite(v):
            continue
        if m is None:
            m = v
        else:
            res[k] = v - m
            m = m + alpha * (v - m)
        out[k] = m
    return out, res


def rolling_median(y, window):
    """Median of the last `window` valid observations; residual against the previous median."""
    out, res, buf = np.full(len(y), np.nan), np.full(len(y), np.nan), []
    for k, v in enumerate(y):
        if not np.isfinite(v):
            continue
        if buf:
            res[k] = v - np.median(buf)
        buf = (buf + [v])[-window:]
        out[k] = np.median(buf)
    return out, res
