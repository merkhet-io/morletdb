#!/usr/bin/env python3
"""C1: context-conditioned vs context-free compare-to-history on NASA milling (PROTOCOL.md).

Usage: c1.py MILL_MAT OUT   (compression environment: numpy, scipy)
Runs once; writes OUT/c1.json. Synthetic checks: test_c1.py.
"""
import json
import sys
from pathlib import Path

import numpy as np
from scipy import io as sio
from scipy import stats

REFERENCE, EVALUATION = range(1, 9), range(9, 17)
TWINS = {9: 1, 12: 2, 11: 3, 10: 4, 16: 5, 15: 6, 13: 7, 14: 8}
SHARP_VB, MIN_HISTORY, WORN_VB = 0.2, 3, 0.3
VALUES = ("dc", "ac", "vt", "vs", "at", "as")


def run_values(r):
    return {"dc": float(np.percentile(r.smcDC, 75)), "ac": float(np.std(r.smcAC)),
            "vt": float(np.percentile(r.vib_table, 75)), "vs": float(np.percentile(r.vib_spindle, 75)),
            "at": float(np.percentile(r.AE_table, 75)), "as": float(np.percentile(r.AE_spindle, 75))}


def load(path):
    mill = sio.loadmat(path, squeeze_me=True, struct_as_record=False)["mill"]
    runs = []
    for r in mill:
        vb = float(r.VB) if np.size(r.VB) == 1 and np.isfinite(float(r.VB)) else None
        runs.append({"case": int(r.case), "run": int(r.run), "time": float(r.time), "vb": vb,
                     "context": (float(r.DOC), float(r.feed), int(r.material)), **run_values(r)})
    return runs


def check_twins(runs):
    ctx = {}
    for r in runs:
        ctx.setdefault(r["case"], set()).add(r["context"])
    if any(len(v) != 1 for v in ctx.values()):
        raise ValueError("context varies within a case")
    for e, ref in TWINS.items():
        if ctx[e] != ctx[ref]:
            raise ValueError(f"case {e} and its twin {ref} differ in setting")


def robust_score(run, reference):
    """Max |robust z| over values with nonzero MAD; None if no value is scorable."""
    zs = []
    for v in VALUES:
        ref = np.array([h[v] for h in reference])
        med = np.median(ref)
        mad = np.median(np.abs(ref - med))
        if mad > 0:
            zs.append(abs(run[v] - med) / (1.4826 * mad))
    return max(zs) if zs else None


def relative_score(run, reference):
    """Primary score (amendment 1): max over values of |x - median_ref| / |median_ref|."""
    out = []
    for v in VALUES:
        med = np.median([h[v] for h in reference])
        if med != 0:
            out.append(abs(run[v] - med) / abs(med))
    return max(out) if out else None


def value_z(run, reference, v):
    ref = np.array([h[v] for h in reference])
    med = np.median(ref)
    mad = np.median(np.abs(ref - med))
    return (run[v] - med) / (1.4826 * mad) if mad > 0 else None


def spearman(x, y):
    return float(stats.spearmanr(x, y).statistic) if len(x) > 2 else float("nan")


def auroc(score, worn):
    score, worn = np.asarray(score), np.asarray(worn, bool)
    if worn.all() or not worn.any():
        return None
    return float(stats.mannwhitneyu(score[worn], score[~worn]).statistic / (worn.sum() * (~worn).sum()))


def analyse(runs, resamples=2000, seed=7):
    check_twins(runs)
    history = [r for r in runs if r["case"] in REFERENCE and r["vb"] is not None and r["vb"] <= SHARP_VB]
    by_ctx = {}
    for h in history:
        by_ctx.setdefault(h["context"], []).append(h)
    excluded = sorted({r["case"] for r in runs if r["case"] in EVALUATION
                       and len(by_ctx.get(r["context"], [])) < MIN_HISTORY})
    rows = []
    for r in runs:
        if r["case"] not in EVALUATION or r["vb"] is None or r["case"] in excluded:
            continue
        cond, free = relative_score(r, by_ctx[r["context"]]), relative_score(r, history)
        if cond is None or free is None:
            raise ValueError(f"unscorable run {r['case']}/{r['run']}")
        rows.append({"case": r["case"], "run": r["run"], "vb": r["vb"], "time": r["time"],
                     "conditioned": cond, "context_free": free,
                     "z_conditioned": robust_score(r, by_ctx[r["context"]]), "z_context_free": robust_score(r, history),
                     **{f"z_cond_{v}": value_z(r, by_ctx[r["context"]], v) for v in VALUES},
                     **{f"z_free_{v}": value_z(r, history, v) for v in VALUES}})
    vb = [x["vb"] for x in rows]
    rho_c = spearman([x["conditioned"] for x in rows], vb)
    rho_f = spearman([x["context_free"] for x in rows], vb)
    cases = sorted({x["case"] for x in rows})
    rng = np.random.default_rng(seed)
    deltas = []
    for _ in range(resamples):
        pick = rng.choice(cases, len(cases))
        sub = [x for c in pick for x in rows if x["case"] == c]
        v = [x["vb"] for x in sub]
        deltas.append(spearman([x["conditioned"] for x in sub], v) - spearman([x["context_free"] for x in sub], v))
    deltas = np.array(deltas)
    deltas = deltas[np.isfinite(deltas)]
    delta = rho_c - rho_f
    ci = [float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5))]
    decision = ("supported" if delta >= 0.10 and ci[0] > 0 else
                "not supported" if ci[1] < 0.10 else "inconclusive")
    worn = [x["vb"] >= WORN_VB for x in rows]
    reported = {
        "time_in_use_rho": spearman([x["time"] for x in rows], vb),
        "robust_z_rho": {"conditioned": spearman([x["z_conditioned"] for x in rows], vb),
                         "context_free": spearman([x["z_context_free"] for x in rows], vb)},
        "per_value_rho": {f"{arm}_{v}": spearman([x[f"z_{arm}_{v}"] if x[f"z_{arm}_{v}"] is not None else np.nan
                                                  for x in rows], vb)
                          for arm in ("cond", "free") for v in VALUES},
        "auroc_vb_ge_0.3": {"conditioned": auroc([x["conditioned"] for x in rows], worn),
                            "context_free": auroc([x["context_free"] for x in rows], worn),
                            "time_in_use": auroc([x["time"] for x in rows], worn),
                            "worn_runs": int(sum(worn)), "runs": len(rows)},
        "history_runs": {"all": len(history), "per_context": {str(k): len(v) for k, v in by_ctx.items()}},
    }
    return {"cases": cases, "excluded_cases": excluded, "runs": len(rows),
            "rho_conditioned": rho_c, "rho_context_free": rho_f, "delta": delta, "ci95": ci,
            "bootstrap_valid": int(len(deltas)), "decision": decision, "reported": reported, "rows": rows}


def main():
    path, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    res = analyse(load(path))
    (out / "c1.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
