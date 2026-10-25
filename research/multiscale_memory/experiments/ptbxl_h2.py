#!/usr/bin/env python3
"""PTB-XL pilot for H1/H2 (see ../PROTOCOL.md). Stages write to --out with provenance.

  verify    check every used data file against PhysioNet SHA256SUMS.txt
  prepare   superclass labels and lead II (100 Hz) -> derived/lead2.npz
  retrieve  exhaustive cosine top-k on log-FFT features, patient-excluded
  contexts  budgeted neighbour signals per arm (msmem) + bytes/time accounting
  train     one arm x seed; early stopping on fold 9; predictions for folds 9 and 10
  evaluate  seed-averaged test macro AUROC + paired patient-level bootstrap
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from msmem.representation import CoefficientSet, RepresentationSpec  # noqa: E402

CLASSES = ["NORM", "MI", "STTC", "CD", "HYP"]
K = 8
BUDGETS = (32, 64, 128)
SPEC = RepresentationSpec(levels=6, channel="II", units="mV", sample_rate_hz=100.0,
                          window_samples=1000, normalization="zscore-per-window")
COARSE = {16: {"a6"}, 32: {"a6", "d6"}, 64: {"a6", "d6", "d5"}, 128: {"a6", "d6", "d5", "d4"}}
ARMS = (["A0", "A1", "A2", "A3"] + [f"R{b}" for b in BUDGETS] + [f"W{b}" for b in BUDGETS]
        + [f"C{b}" for b in BUDGETS])


def provenance() -> dict:
    def run(*cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
        return r.stdout.strip() if r.returncode == 0 else None
    import scipy
    import torch
    return {"commit": run("git", "rev-parse", "HEAD"),
            "dirty": bool(run("git", "status", "--porcelain", "--untracked-files=no")),
            "python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "numpy": np.__version__, "scipy": scipy.__version__,
            "torch": torch.__version__, "spec_id": SPEC.id}


def zscore(x):
    m = x.mean(axis=-1, keepdims=True)
    s = x.std(axis=-1, keepdims=True)
    return (x - m) / np.where(s > 0, s, 1.0)


# -- data --------------------------------------------------------------------------
def verify(data: Path, out: Path):
    sums = {}
    for line in (data / "SHA256SUMS.txt").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        sums[name.strip()] = digest
    import pandas as pd
    db = pd.read_csv(data / "ptbxl_database.csv")
    names = ["ptbxl_database.csv", "scp_statements.csv", "LICENSE.txt"]
    for stem in db["filename_lr"]:
        names += [f"{stem}.dat", f"{stem}.hea"]
    bad = []
    for name in names:
        if hashlib.sha256((data / name).read_bytes()).hexdigest() != sums.get(name):
            bad.append(name)
    report = {"checked": len(names), "mismatched_or_missing": bad[:20], "n_bad": len(bad)}
    (out / "verify.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report))
    if bad:
        raise SystemExit("checksum verification failed")


def prepare(data: Path, out: Path):
    import pandas as pd
    import wfdb
    db = pd.read_csv(data / "ptbxl_database.csv", index_col="ecg_id")
    stm = pd.read_csv(data / "scp_statements.csv", index_col=0)
    diag = stm[stm.diagnostic == 1]["diagnostic_class"].to_dict()
    rows, labels, excluded = [], [], {"no_superclass": 0, "bad_signal": 0}
    signals = []
    for ecg_id, row in db.iterrows():
        codes = ast.literal_eval(row.scp_codes)
        classes = {diag[c] for c in codes if c in diag}
        if not classes:
            excluded["no_superclass"] += 1
            continue
        record = wfdb.rdrecord(str(data / row.filename_lr))
        lead = record.sig_name.index("II")
        x = record.p_signal[:, lead]
        if len(x) != 1000 or not np.isfinite(x).all():
            excluded["bad_signal"] += 1
            continue
        signals.append(x.astype(np.float32))
        labels.append([c in classes for c in CLASSES])
        rows.append((ecg_id, int(row.patient_id), int(row.strat_fold)))
    ids, patients, folds = map(np.array, zip(*rows))
    derived = out / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(derived / "lead2.npz", x=np.stack(signals), y=np.array(labels, dtype=np.float32),
                        ecg_id=ids, patient=patients, fold=folds)
    summary = {"records": len(ids), "patients": int(len(set(patients))), "excluded": excluded,
               "per_fold": {int(f): int((folds == f).sum()) for f in sorted(set(folds))},
               "label_prevalence": dict(zip(CLASSES, np.array(labels).mean(0).round(4).tolist()))}
    (out / "prepare.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


def load(out: Path):
    d = np.load(out / "derived" / "lead2.npz")
    return d["x"].astype(np.float64), d["y"], d["patient"], d["fold"], d["ecg_id"]


# -- retrieval ---------------------------------------------------------------------
def spectral_features(x):
    """log-magnitude spectrum 0.5-40 Hz of the z-scored window, L2-normalized (no fitting)."""
    spec = np.abs(np.fft.rfft(zscore(x), axis=-1))
    freqs = np.fft.rfftfreq(x.shape[-1], d=1 / 100.0)
    band = (freqs >= 0.5) & (freqs <= 40.0)
    f = np.log1p(spec[:, band])
    f -= f.mean(axis=1, keepdims=True)
    return f / np.linalg.norm(f, axis=1, keepdims=True)


def retrieve(out: Path):
    x, _, patient, fold, _ = load(out)
    gallery = np.where(fold <= 8)[0]
    feats = spectral_features(x).astype(np.float32)
    g = feats[gallery]
    neighbours = np.zeros((len(x), K), dtype=np.int64)
    rng = np.random.default_rng(20261025)
    random_nb = np.zeros((len(x), K), dtype=np.int64)
    started = time.perf_counter()
    for lo in range(0, len(x), 2048):
        q = np.arange(lo, min(len(x), lo + 2048))
        sims = feats[q] @ g.T
        sims[patient[q][:, None] == patient[gallery][None, :]] = -np.inf  # no self/same patient
        top = np.argpartition(-sims, K, axis=1)[:, :K]
        order = np.argsort(-np.take_along_axis(sims, top, 1), axis=1, kind="stable")
        neighbours[q] = gallery[np.take_along_axis(top, order, 1)]
    elapsed = time.perf_counter() - started
    for i in range(len(x)):
        pool = gallery[patient[gallery] != patient[i]]
        random_nb[i] = rng.choice(pool, K, replace=False)
    assert not (patient[neighbours] == patient[:, None]).any()
    assert (fold[neighbours] <= 8).all()
    np.savez(out / "derived" / "neighbours.npz", retrieved=neighbours, random=random_nb)
    report = {"k": K, "gallery": int(len(gallery)), "queries": int(len(x)),
              "exhaustive_retrieval_s": elapsed, "retriever": "cosine log|FFT| 0.5-40 Hz, zscored",
              "same_patient_neighbours": 0}
    (out / "retrieve.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report))


# -- budgeted contexts -------------------------------------------------------------
def contexts(out: Path):
    """Neighbour signals as delivered to the model (1000-sample, z-scored domain)."""
    from scipy.signal import resample
    x, _, _, fold, ecg_id = load(out)
    gallery = np.where(fold <= 8)[0]
    z = zscore(x[gallery])
    arms = {"raw": z.astype(np.float32)}
    cost = {"raw": {"scalars": 1000, "bytes": 8000}}
    started = time.perf_counter()
    reps = [CoefficientSet.from_samples(x[i], SPEC, f"ptbxl:{ecg_id[i]}", 0) for i in gallery]
    build_s = time.perf_counter() - started
    blob_bytes = len(reps[0].to_bytes())
    for b in BUDGETS:
        arms[f"R{b}"] = resample(resample(z, b, axis=1), 1000, axis=1).astype(np.float32)
        cost[f"R{b}"] = {"scalars": b, "bytes": 8 * b}
        arms[f"W{b}"] = np.stack([r.reconstruct_from(COARSE[b], []) for r in reps]).astype(np.float32)
        cost[f"W{b}"] = {"scalars": b, "bytes": 8 * b}
        half = b // 2
        coarse = COARSE[half]
        candidates = [n for n, _ in SPEC.shapes() if n not in coarse]
        arms[f"C{b}"] = np.stack([r.reconstruct_from(coarse, r.refinement(half, candidates))
                                  for r in reps]).astype(np.float32)
        cost[f"C{b}"] = {"scalars": b, "bytes": 8 * b + 4 * half}  # uint32 index per refinement
    errors = {name: float(np.mean(np.sum((v - z) ** 2, 1) / np.sum(z ** 2, 1)))
              for name, v in arms.items() if name != "raw"}
    np.savez(out / "derived" / "contexts.npz", gallery=gallery, **arms)
    report = {"spec": SPEC.id, "representation_build_s": build_s, "gallery": int(len(gallery)),
              "stored_blob_bytes_per_window": blob_bytes, "raw_float64_bytes_per_window": 8000,
              "delivered_cost_per_neighbour": cost,
              "mean_relative_reconstruction_error": errors}
    (out / "contexts.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


# -- model ---------------------------------------------------------------------------
def build_model(arm: str):
    import torch
    from torch import nn

    class Encoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Conv1d(1, 32, 7, stride=2, padding=3), nn.BatchNorm1d(32), nn.ReLU(),
                nn.Conv1d(32, 64, 5, stride=2, padding=2), nn.BatchNorm1d(64), nn.ReLU(),
                nn.Conv1d(64, 64, 5, stride=2, padding=2), nn.BatchNorm1d(64), nn.ReLU(),
                nn.AdaptiveAvgPool1d(1), nn.Flatten())

        def forward(self, x):
            return self.net(x.unsqueeze(1))

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = Encoder()
            self.signal_ctx = arm not in ("A0", "A1")
            self.label_ctx = arm != "A0"
            width = 64 + (64 if self.signal_ctx else 0) + (5 if self.label_ctx else 0)
            self.head = nn.Sequential(nn.Linear(width, 64), nn.ReLU(), nn.Linear(64, 5))

        def forward(self, q, ctx, labels):
            parts = [self.encoder(q)]
            if self.signal_ctx:
                b, k, n = ctx.shape
                parts.append(self.encoder(ctx.reshape(b * k, n)).reshape(b, k, -1).mean(1))
            if self.label_ctx:
                parts.append(labels.mean(1))
            return self.head(torch.cat(parts, 1))

    return Model()


def macro_auroc(y, p):
    from sklearn.metrics import roc_auc_score
    return float(np.mean([roc_auc_score(y[:, c], p[:, c]) for c in range(y.shape[1])]))


def train(out: Path, arm: str, seed: int, epochs: int, patience: int, smoke: bool):
    import torch
    torch.manual_seed(seed)
    np.random.seed(seed)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    x, y, _, fold, _ = load(out)
    nb = np.load(out / "derived" / "neighbours.npz")
    ctxz = np.load(out / "derived" / "contexts.npz")
    gallery = ctxz["gallery"]
    position = np.full(len(x), -1)
    position[gallery] = np.arange(len(gallery))
    neighbours = nb["random"] if arm == "A2" else nb["retrieved"]
    ctx_key = {"A2": "raw", "A3": "raw"}.get(arm, arm if arm[0] in "RWC" else "raw")
    ctx_bank = torch.tensor(ctxz[ctx_key])
    xz = torch.tensor(zscore(x).astype(np.float32))
    yt = torch.tensor(y)
    nbpos = torch.tensor(position[neighbours])
    split = {s: np.where(m)[0] for s, m in (("train", fold <= 8), ("val", fold == 9), ("test", fold == 10))}
    if smoke:
        split = {k: v[:512] for k, v in split.items()}
    model = build_model(arm).to(device)
    params = sum(p.numel() for p in model.parameters())
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = torch.nn.BCEWithLogitsLoss()

    def batches(idx, shuffle):
        order = np.random.permutation(idx) if shuffle else idx
        for lo in range(0, len(order), 128):
            b = torch.tensor(order[lo:lo + 128])
            yield (xz[b].to(device), ctx_bank[nbpos[b]].to(device),
                   yt[gallery[nbpos[b]]].to(device), yt[b].to(device), b)

    def predict(idx):
        model.eval()
        probs = []
        with torch.no_grad():
            for q, c, lab, _, _ in batches(idx, False):
                probs.append(torch.sigmoid(model(q, c, lab)).cpu().numpy())
        return np.concatenate(probs)

    best, best_state, wait, history = -1.0, None, 0, []
    started = time.perf_counter()
    for epoch in range(epochs):
        model.train()
        for q, c, lab, target, _ in batches(split["train"], True):
            opt.zero_grad()
            loss = loss_fn(model(q, c, lab), target)
            loss.backward()
            opt.step()
        val = macro_auroc(y[split["val"]], predict(split["val"]))
        history.append({"epoch": epoch + 1, "val_macro_auroc": val})
        if val > best:
            best, wait = val, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            wait += 1
            if wait >= patience:
                break
    train_s = time.perf_counter() - started
    model.load_state_dict(best_state)
    run = out / "runs" / f"{arm}_seed{seed}"
    run.mkdir(parents=True, exist_ok=True)
    infer_s = None
    if not smoke:  # Smoke runs never touch the test fold.
        started = time.perf_counter()
        test_probs = predict(split["test"])
        infer_s = time.perf_counter() - started
        np.save(run / "test_probs.npy", test_probs.astype(np.float32))
    result = {"arm": arm, "seed": seed, "parameters": params, "best_val_macro_auroc": best,
              "epochs_run": len(history), "history": history, "train_s": train_s,
              "test_inference_s": infer_s, "device": str(device), "smoke": smoke,
              "context": ctx_key, "neighbours": "random" if arm == "A2" else "retrieved",
              "provenance": provenance()}
    (run / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("arm", "seed", "parameters", "best_val_macro_auroc",
                                             "epochs_run", "train_s")}))


# -- evaluation ----------------------------------------------------------------------
def evaluate(out: Path, resamples: int):
    x, y, patient, fold, _ = load(out)
    test = np.where(fold == 10)[0]
    yt, pt = y[test], patient[test]
    probs, seeds = {}, {}
    for arm in ARMS:
        runs = sorted((out / "runs").glob(f"{arm}_seed*/test_probs.npy"))
        if runs:
            stack = np.stack([np.load(r) for r in runs])
            probs[arm] = stack.mean(0)
            seeds[arm] = [macro_auroc(yt, s) for s in stack]
    rng = np.random.default_rng(7)
    patients = np.unique(pt)
    members = {p: np.where(pt == p)[0] for p in patients}
    draws = [np.concatenate([members[p] for p in rng.choice(patients, len(patients))])
             for _ in range(resamples)]

    def boot(a, b):
        diffs = []
        for idx in draws:
            if yt[idx].min(0).max() == 1 or (yt[idx].sum(0) == 0).any():
                continue
            diffs.append(macro_auroc(yt[idx], probs[a][idx]) - macro_auroc(yt[idx], probs[b][idx]))
        diffs = np.array(diffs)
        return {"diff": macro_auroc(yt, probs[a]) - macro_auroc(yt, probs[b]),
                "ci95": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
                "resamples_used": int(len(diffs))}
    comparisons = [("C64", "R64"), ("C64", "W64"), ("A3", "A0"), ("A3", "A2"), ("A3", "A1")]
    comparisons += [(f"C{b}", f"R{b}") for b in (32, 128)] + [(f"C{b}", f"W{b}") for b in (32, 128)]
    comparisons += [(f"{r}{b}", "A1") for r in "RWC" for b in BUDGETS] + [("A2", "A1"), ("A1", "A0")]
    table = {arm: {"macro_auroc": macro_auroc(yt, probs[arm]), "seed_macro_auroc": seeds[arm]}
             for arm in probs}
    report = {"test_records": int(len(test)), "test_patients": int(len(patients)),
              "bootstrap_resamples": resamples, "arms": table,
              "comparisons": {f"{a} vs {b}": boot(a, b) for a, b in comparisons if a in probs and b in probs},
              "provenance": provenance()}
    (out / "evaluation.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({a: round(v["macro_auroc"], 4) for a, v in table.items()}))
    for name, c in report["comparisons"].items():
        print(f"{name:14s} diff {c['diff']:+.4f}  ci95 [{c['ci95'][0]:+.4f}, {c['ci95'][1]:+.4f}]")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["verify", "prepare", "retrieve", "contexts", "train", "evaluate"])
    parser.add_argument("--data", type=Path, default=HERE.parents[2] / "data" / "ptb-xl" / "1.0.3")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--smoke", action="store_true", help="512 records per split, for timing only")
    parser.add_argument("--resamples", type=int, default=1000)
    a = parser.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.stage == "verify":
        verify(a.data, a.out)
    elif a.stage == "prepare":
        prepare(a.data, a.out)
    elif a.stage == "retrieve":
        retrieve(a.out)
    elif a.stage == "contexts":
        contexts(a.out)
    elif a.stage == "train":
        train(a.out, a.arm, a.seed, a.epochs, a.patience, a.smoke)
    else:
        evaluate(a.out, a.resamples)


if __name__ == "__main__":
    main()
