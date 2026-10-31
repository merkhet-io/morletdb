#!/usr/bin/env python3
"""CV1 gate (PROTOCOL.md): does current add vibration band-energy information beyond measured
operating conditions? Ridge models A-D, shuffled-current control, leave one bearing out.

  .venv-seismic/bin/python -I evaluation/current_vibration/gate.py \
      evaluation/current_vibration/results/features.npz evaluation/current_vibration/results
"""
import json
import sys
from pathlib import Path

import numpy as np

ALPHAS = (1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0)
INNER_FOLDS = 5
SEEDS = range(5)
RESAMPLES, SEED = 2000, 7
HEALTHY = {f"K00{i}" for i in range(1, 7)}
ARTIFICIAL = {"KA01", "KA03", "KA05", "KA06", "KA07", "KA09", "KI01", "KI03", "KI05", "KI07", "KI08"}
REAL = {"KA04", "KA15", "KA16", "KA22", "KA30", "KI04", "KI14", "KI16", "KI17", "KI18", "KI21", "KB23", "KB24", "KB27"}


def design(z, which, C=None):
    B, Cur = z["B"], (z["C"] if C is None else C)
    return {"A": np.zeros((len(B), 0)), "B": B, "C": Cur, "D": np.hstack([B, Cur])}[which]


def fit(X, Y, alpha):
    """Ridge on standardized X (scaling from these rows only); returns a predictor."""
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Xs = (X - mu) / sd
    ym = Y.mean(0)
    if X.shape[1] == 0:
        return lambda Xn: np.tile(ym, (len(Xn), 1))
    W = np.linalg.solve(Xs.T @ Xs + alpha * np.eye(X.shape[1]), Xs.T @ (Y - ym))
    return lambda Xn: ((Xn - mu) / sd) @ W + ym


def unit_error(Y, P):
    """Primary per-unit error: RMSE per band, averaged over bands."""
    return float(np.sqrt(((Y - P) ** 2).mean(0)).mean())


def tune(X, Y, groups, alphas):
    if X.shape[1] == 0:
        return 0.0
    names = np.unique(groups)
    folds = np.array_split(np.random.default_rng(SEED).permutation(names), INNER_FOLDS)
    best = None
    for a in alphas:
        errs = []
        for f in folds:
            te = np.isin(groups, f)
            pred = fit(X[~te], Y[~te], a)(X[te])
            errs += [unit_error(Y[te][groups[te] == g], pred[groups[te] == g]) for g in f]
        s = np.mean(errs)
        if best is None or s < best[0]:
            best = (s, a)
    return best[1]


def shuffle_current(z, train, rng):
    """Swap whole recordings' current blocks among training recordings of the same nominal setting."""
    C = z["C"].copy()
    rec = np.array([f"{b}|{s}|{m}" for b, s, m in zip(z["bearing"], z["setting"], z["measurement"])])
    for s in np.unique(z["setting"][train]):
        recs = np.unique(rec[train & (z["setting"] == s)])
        perm = rng.permutation(len(recs))
        for src, dst in zip(recs, recs[perm]):
            si, di = np.where(rec == src)[0], np.where(rec == dst)[0]
            n = min(len(si), len(di))                  # 7 or 8 windows; pair window by window
            C[si[:n]] = z["C"][di[:n]]
    return C


def decompose(Y, P, rec):
    """Between-recording RMSE (recording means) and within-recording RMSE / correlation."""
    ids = np.unique(rec)
    my = np.array([Y[rec == r].mean(0) for r in ids])
    mp = np.array([P[rec == r].mean(0) for r in ids])
    dy = np.vstack([Y[rec == r] - Y[rec == r].mean(0) for r in ids])
    dp = np.vstack([P[rec == r] - P[rec == r].mean(0) for r in ids])
    corr = [float(np.corrcoef(dy[:, j], dp[:, j])[0, 1]) if dp[:, j].std() > 0 else 0.0 for j in range(Y.shape[1])]
    return {"between_rmse": float(np.sqrt(((my - mp) ** 2).mean(0)).mean()),
            "within_rmse": float(np.sqrt(((dy - dp) ** 2).mean(0)).mean()),
            "within_target_sd": float(dy.std(0).mean()), "within_corr_per_band": corr}


def ratio(per, a, b, rng):
    qs = sorted(per)
    A = np.array([per[q][a] for q in qs])
    B = np.array([per[q][b] for q in qs])
    boots = [A[i].mean() / B[i].mean() for i in (rng.integers(0, len(qs), len(qs)) for _ in range(RESAMPLES))]
    return {"ratio": float(A.mean() / B.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "units_better": int((A < B).sum()), "units": len(qs)}


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    z = dict(np.load(src))
    bearings = sorted(np.unique(z["bearing"]))
    rec = np.array([f"{b}|{s}|{m}" for b, s, m in zip(z["bearing"], z["setting"], z["measurement"])])
    Y = z["Y"]
    per, detail = {}, {}
    for q in bearings:
        test = z["bearing"] == q
        train = ~test
        assert not (set(z["bearing"][train]) & {q})
        preds, alphas = {}, {}
        for m in "ABCD":
            X = design(z, m)
            a = tune(X[train], Y[train], z["bearing"][train], ALPHAS)
            alphas[m] = a
            preds[m] = fit(X[train], Y[train], a)(X[test])
        sh = []
        for s in SEEDS:
            Xs = design(z, "D", shuffle_current(z, train, np.random.default_rng(s)))
            a = tune(Xs[train], Y[train], z["bearing"][train], ALPHAS)
            sh.append(fit(Xs[train], Y[train], a)(design(z, "D")[test]))
        preds["D_shuffled"] = np.mean(sh, 0)
        per[q] = {m: unit_error(Y[test], p) for m, p in preds.items()}
        detail[q] = {"windows": int(test.sum()), "alpha": alphas,
                     "per_band_rmse": {m: np.sqrt(((Y[test] - p) ** 2).mean(0)).tolist() for m, p in preds.items()},
                     "decomposition": {m: decompose(Y[test], preds[m], rec[test]) for m in ("B", "D")},
                     "per_setting": {s: {m: unit_error(Y[test & (z["setting"] == s)], preds[m][z["setting"][test] == s])
                                         for m in ("B", "D")} for s in np.unique(z["setting"][test])}}
        print(q, {m: round(v, 4) for m, v in per[q].items()}, flush=True)

    rng = np.random.default_rng(SEED)
    means = {m: float(np.mean([per[q][m] for q in bearings])) for m in ("A", "B", "C", "D", "D_shuffled")}
    comps = {"D_vs_B": ratio(per, "D", "B", rng), "D_vs_D_shuffled": ratio(per, "D", "D_shuffled", rng),
             "C_vs_B": ratio(per, "C", "B", rng), "B_vs_A": ratio(per, "B", "A", rng)}
    d = comps["D_vs_B"]
    c1 = d["ratio"] <= 0.90 and d["ci95"][1] < 1.0
    c2 = comps["D_vs_D_shuffled"]["ci95"][1] < 1.0
    c3 = d["units_better"] >= int(np.ceil(2 * len(bearings) / 3))
    verdict = "CONTINUE" if c1 and c2 and c3 else ("REJECT" if d["ci95"][0] > 0.95 else "PAUSE")
    classes = {"healthy": HEALTHY, "artificial": ARTIFICIAL, "real": REAL}
    by_class = {k: {m: float(np.mean([per[q][m] for q in bearings if q in v])) for m in ("B", "D", "D_shuffled")} for k, v in classes.items()}
    dec = {m: {k: float(np.mean([detail[q]["decomposition"][m][k] for q in bearings]))
               for k in ("between_rmse", "within_rmse", "within_target_sd")} for m in ("B", "D")}
    for m in ("B", "D"):
        dec[m]["within_corr_mean_over_bands"] = float(np.mean([np.mean(detail[q]["decomposition"][m]["within_corr_per_band"]) for q in bearings]))
    res = {"bearings": len(bearings), "windows": int(len(Y)), "mean_unit_error": means, "comparisons": comps,
           "conditions": {"D_le_0.90_B_and_CI_below_1": c1, "D_beats_shuffled": c2, "D_better_on_2_3_of_bearings": c3},
           "verdict": verdict, "by_class": by_class, "decomposition_mean": dec,
           "resid_corr_current1_vibration": {"median": float(np.median(z["resid_corr"])),
                                             "p10_p90": [float(np.percentile(z["resid_corr"], 10)), float(np.percentile(z["resid_corr"], 90))]},
           "per_bearing": per, "detail": detail}
    json.dump(res, open(out / "gate.json", "w"), indent=1)
    print(json.dumps({k: res[k] for k in ("mean_unit_error", "comparisons", "conditions", "verdict", "by_class", "decomposition_mean",
                                          "resid_corr_current1_vibration")}, indent=1))


if __name__ == "__main__":
    main()
