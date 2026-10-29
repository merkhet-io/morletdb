#!/usr/bin/env python3
"""L1: Kalman level track vs EMA and rolling median on synthetic sequences (PROTOCOL.md).

  evaluate.py OUT.json     (compression environment: numpy; python -I)
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kalman  # noqa: E402

N, WARMUP, SIGMA = 200, 20, 0.1
GRID = np.arange(-8, 2.0001, 0.25)
ALPHAS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5)
WINDOWS = (5, 10, 20, 40)
DEV_SEEDS, EVAL_SEEDS = range(0, 50), range(1000, 1200)
DEV_Q = EVAL_Q = 0.001
SCENARIOS = ("S1 constant", "S2 drift", "S3 spike", "S4 step", "S5 gaps", "S6 correlated")
SPIKE_AT, STEP_AT, STEP = 100, 100, 0.3


def intervals(rng, gaps):
    return np.where(rng.random(N) < 0.15, 48.0, 1.0) if gaps else np.ones(N)


def random_walk(rng, dt, q):
    return np.cumsum(np.concatenate([[0.0], rng.standard_normal(N - 1) * np.sqrt(q * dt[1:])]))


def sequence(scenario, seed):
    rng = np.random.default_rng(seed)
    gaps = scenario == "S5 gaps" or (scenario == "dev" and seed % 2 == 1)
    dt = intervals(rng, gaps)
    hours = np.cumsum(dt)
    k = np.arange(N)
    if scenario in ("dev", "S5 gaps"):
        latent = random_walk(rng, dt, DEV_Q if scenario == "dev" else EVAL_Q)
    elif scenario == "S2 drift":
        latent = 0.3 * k / (N - 1)
    elif scenario == "S4 step":
        latent = np.where(k >= STEP_AT, STEP, 0.0)
    else:
        latent = np.zeros(N)
    if scenario == "S6 correlated":
        e = np.empty(N)
        e[0] = rng.standard_normal()
        for i in range(1, N):
            e[i] = 0.7 * e[i - 1] + np.sqrt(1 - 0.49) * rng.standard_normal()
        noise = SIGMA * e
    else:
        noise = SIGMA * rng.standard_normal(N)
    y = latent + noise
    if scenario == "S3 spike":
        y[SPIKE_AT] += 0.8
    return y, hours, latent


# -- calibration on development sequences ----------------------------------------------------
def calibrate():
    dev = [sequence("dev", s) for s in DEV_SEEDS]
    best = None
    for a in GRID:
        for b in GRID:
            ll = sum(kalman.log_likelihood(y, h, 10.0 ** a, 10.0 ** b) for y, h, _ in dev)
            if best is None or ll > best[0]:
                best = (ll, 10.0 ** a, 10.0 ** b)

    def one_step_mse(f):
        errs = []
        for y, _, _ in dev:
            _, res = f(y)
            errs.append(res[np.isfinite(res)] ** 2)
        return float(np.mean(np.concatenate(errs)))

    alpha = min(ALPHAS, key=lambda a: one_step_mse(lambda y: kalman.ema(y, a)))
    window = min(WINDOWS, key=lambda w: one_step_mse(lambda y: kalman.rolling_median(y, w)))
    return {"q": best[1], "r": best[2], "log_likelihood": best[0], "ema_alpha": alpha, "rolling_window": window}


# -- metrics ---------------------------------------------------------------------------------
def first_crossing(level, start, bar):
    hit = np.flatnonzero(level[start:] > bar)
    return int(hit[0]) if len(hit) else None


def run_one(scenario, seed, cal):
    y, hours, latent = sequence(scenario, seed)
    kf = kalman.track(y, hours, cal["q"], cal["r"])
    em, em_res = kalman.ema(y, cal["ema_alpha"])
    rm, rm_res = kalman.rolling_median(y, cal["rolling_window"])
    sl = slice(WARMUP, None)
    out = {"rmse": {m: float(np.sqrt(np.mean((lv[sl] - latent[sl]) ** 2)))
                    for m, lv in (("kalman", kf["level"]), ("ema", em), ("rolling", rm))}}
    half = 1.96 * np.sqrt(kf["level_var"][sl])
    out["level_coverage"] = float(np.mean(np.abs(kf["level"][sl] - latent[sl]) <= half))
    pred = 1.96 * np.sqrt(kf["innovation_var"][sl])
    out["prediction_coverage"] = float(np.mean(np.abs(y[sl] - kf["predicted_level"][sl]) <= pred))
    if scenario == "S4 step":
        out["step_half"] = {m: first_crossing(lv, STEP_AT, 0.5 * STEP) for m, lv in
                            (("kalman", kf["level"]), ("ema", em), ("rolling", rm))}
        out["step_90"] = {m: first_crossing(lv, STEP_AT, 0.9 * STEP) for m, lv in
                          (("kalman", kf["level"]), ("ema", em), ("rolling", rm))}
    if scenario == "S3 spike":
        z = kf["innovation"] / np.sqrt(kf["innovation_var"])
        vis = {}
        for m, res in (("kalman", z), ("ema", em_res), ("rolling", rm_res)):
            other = np.delete(res[sl], SPIKE_AT - WARMUP)
            vis[m] = float(res[SPIKE_AT] / np.nanstd(other))
        out["spike_visibility"] = vis
        out["spike_leakage"] = {m: float(np.max(np.abs(lv[SPIKE_AT:SPIKE_AT + 5] - latent[SPIKE_AT:SPIKE_AT + 5])))
                                for m, lv in (("kalman", kf["level"]), ("ema", em), ("rolling", rm))}
    return out


def summarise(rows):
    s = {"rmse": {m: float(np.mean([r["rmse"][m] for r in rows])) for m in ("kalman", "ema", "rolling")},
         "level_coverage": float(np.mean([r["level_coverage"] for r in rows])),
         "prediction_coverage": float(np.mean([r["prediction_coverage"] for r in rows]))}
    for key in ("step_half", "step_90"):
        if key in rows[0]:
            s[key] = {m: {"median": float(np.median([r[key][m] for r in rows if r[key][m] is not None])),
                          "never": int(sum(r[key][m] is None for r in rows))} for m in ("kalman", "ema", "rolling")}
    for key in ("spike_visibility", "spike_leakage"):
        if key in rows[0]:
            s[key] = {m: float(np.median([r[key][m] for r in rows])) for m in ("kalman", "ema", "rolling")}
    return s


def decide(res):
    sc = res["scenarios"]
    rmse_ok = {s: sc[s]["rmse"]["kalman"] <= 1.05 * min(sc[s]["rmse"]["ema"], sc[s]["rmse"]["rolling"])
               for s in SCENARIOS[:5]}
    cov = {s: (sc[s]["level_coverage"], sc[s]["prediction_coverage"]) for s in ("S1 constant", "S5 gaps")}
    keep = all(rmse_ok.values()) and all(0.90 <= a <= 0.98 and 0.90 <= b <= 0.98 for a, b in cov.values())
    drop = sum(not v for v in rmse_ok.values()) >= 2 or any(not (0.80 <= a <= 0.99) for a, _ in cov.values())
    gap = sc["S5 gaps"]["rmse"]["kalman"] <= 0.90 * sc["S5 gaps"]["rmse"]["ema"]
    return {"rmse_within_5pct": rmse_ok, "coverage_S1_S5": cov, "gap_advantage": bool(gap),
            "verdict": "keep" if keep else "drop" if drop else "unclear"}


def steady_state_gain(q, r, dt=1.0):
    """Kalman gain that the filter settles to for evenly spaced episodes."""
    p = r
    for _ in range(10000):
        pp = p + q * dt
        p = pp * r / (pp + r)
    return (p + q * dt) / (p + q * dt + r)


def post_hoc(cal):
    """Reported after the run (change after freeze 1), decides nothing: an EMA whose alpha is the
    filter's hourly steady-state gain, on the hourly scenarios and S5."""
    alpha = steady_state_gain(cal["q"], cal["r"])
    out = {"alpha": alpha}
    for s in SCENARIOS:
        rmse, s90 = [], []
        for seed in EVAL_SEEDS:
            y, hours, latent = sequence(s, seed)
            em, _ = kalman.ema(y, alpha)
            rmse.append(np.sqrt(np.mean((em[WARMUP:] - latent[WARMUP:]) ** 2)))
            if s == "S4 step":
                s90.append(first_crossing(em, STEP_AT, 0.9 * STEP))
        out[s] = {"rmse": float(np.mean(rmse))}
        if s90:
            out[s]["step_90_median"] = float(np.median([x for x in s90 if x is not None]))
    return out


def main():
    cal = calibrate()
    res = {"protocol": "L1", "model": kalman.MODEL, "calibration": cal, "scenarios": {}}
    for s in SCENARIOS:
        res["scenarios"][s] = summarise([run_one(s, seed, cal) for seed in EVAL_SEEDS])
    res["decision"] = decide(res)
    res["post_hoc_ema_at_kalman_gain"] = post_hoc(cal)
    text = json.dumps(res, indent=1)
    Path(sys.argv[1]).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
