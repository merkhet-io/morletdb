#!/usr/bin/env python3
"""V2 S3 (PROTOCOL.md): C2's and C3's summary scores under simple-v2, against their stored v1 results.

Usage: s3.py DATA_DIR OUT_DIR   (numpy, scipy, h5py; DATA_DIR is the repository's data/ folder)

The C2 and C3 harnesses run unchanged except their score functions: a value is skipped when its history median is
within QUANTISED_STEPS steps of zero in a quantised history (quantised_step, a mirror of morlet_core::values), as
simple-v2 does. They skip exact zero medians already (as v1 does for exact zeros); v1's 1e-9 x rms floor never
applies to their values (std, kurtosis, crest, duration), so their stored results are the v1 baseline.
Writes OUT_DIR/s3.json: per study the stored v1 AUROCs, the v2 AUROCs, the differences, how many history medians
v2 treats as zero, and the decision.
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CONTEXT = HERE.parent / "context"
QUANTISED_STEPS = 2.0          # frozen in PROTOCOL.md
STEP_TOLERANCE = 1e-6
MAX_MULTIPLE = 1e6


def quantised_step(values):
    """Mirror of morlet_core::values::quantised_step."""
    u = sorted({float(v) for v in values if np.isfinite(v)})
    if len(u) < 3:
        return None
    gaps = [b - a for a, b in zip(u, u[1:]) if b - a > 0]
    step = min(gaps) if gaps else None
    if not step or not np.isfinite(step) or step <= 0:
        return None
    for g in gaps:
        m = g / step
        if m > MAX_MULTIPLE or abs(m - round(m)) > STEP_TOLERANCE * m:
            return None
    return step


def zero_under_v2(med, column):
    step = quantised_step(column)
    return step is not None and abs(med) <= QUANTISED_STEPS * step


def load(name):
    spec = importlib.util.spec_from_file_location(name, CONTEXT / name / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(CONTEXT / name))
    spec.loader.exec_module(mod)
    return mod


def run_c2(data_dir):
    c2 = load("c2")
    counts = {"medians": 0, "zero_under_v2": 0}

    def score_v2(run, history):
        out = []
        for v in c2.VALUES:
            col = [h[v] for h in history]
            med = np.median(col)
            counts["medians"] += 1
            if med != 0 and zero_under_v2(med, col):
                counts["zero_under_v2"] += 1
                continue
            if med != 0:
                out.append(abs(run[v] - med) / abs(med))
        return max(out) if out else None

    c2.score = score_v2
    rows, _rejected = c2.load(data_dir / "bosch")
    res = c2.analyse(rows)
    stored = json.loads((CONTEXT / "c2/results/c2.json").read_text())
    keys = ("auroc_machine_operation", "auroc_context_free")
    return {"v1": {k: stored[k] for k in keys}, "v2": {k: res[k] for k in keys},
            "difference": {k: res[k] - stored[k] for k in keys}, **counts}


def run_c3(data_dir):
    c3 = load("c3")
    counts = {"medians": 0, "zero_under_v2": 0}
    original_analyse, state = c3.analyse, {}

    def analyse_v2(train, test, **kw):
        history = [r for r in train if not r["bad"]]
        state["zero"] = set()
        for v in c3.VALUES:
            col = [h[v] for h in history]
            med = float(np.median(col))
            counts["medians"] += 1
            if med != 0 and zero_under_v2(med, col):
                state["zero"].add(v)
        counts["zero_under_v2"] = len(state["zero"])
        return original_analyse(train, test, **kw)

    original_score = c3.score

    def score_v2(run, medians, names, spread=None):
        return original_score(run, medians, [v for v in names if v not in state["zero"]], spread)

    c3.analyse, c3.score = analyse_v2, score_v2
    d = np.load(data_dir / "millingvibes/clean/millingvibes.npz")
    train = [{**c3.segment_values(x), "bad": y == 0} for x, y in zip(d["x_train"], d["y_train"])]
    test = [{**c3.segment_values(x), "bad": y == 0} for x, y in zip(d["x_test"], d["y_test"])]
    res = c3.analyse(train, test)
    stored = json.loads((CONTEXT / "c3/results/c3.json").read_text())
    return {"v1": stored["auroc"], "v2": res["auroc"],
            "difference": {k: res["auroc"][k] - stored["auroc"][k] for k in stored["auroc"]}, **counts}


def main():
    data_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    c2, c3 = run_c2(data_dir), run_c3(data_dir)
    holds = (c2["difference"]["auroc_machine_operation"] >= -0.01 and c3["difference"]["A_c2_score"] >= -0.01)
    res = {"C2": c2, "C3": c3, "criterion": "each summary AUROC within 0.01 of v1 or higher "
           "(C2 auroc_machine_operation, C3 A_c2_score)", "S3": "holds" if holds else "fails"}
    plain = lambda o: o.item() if hasattr(o, "item") else str(o)  # noqa: E731
    (out / "s3.json").write_text(json.dumps(res, indent=1, default=plain))
    print(json.dumps(res, indent=1, default=plain))


if __name__ == "__main__":
    main()
