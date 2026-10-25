#!/usr/bin/env python3
"""PTB-XL pilot 2 (see ../PROTOCOL_PILOT2.md): learned retriever and attention fusion.

  link      reuse pilot-1 derived data (lead2.npz, contexts.npz), recording their hashes
  encoders  4 cross-fit retriever encoders: embeddings and out-of-fold probabilities
  retrieve  exact top-k cosine on the cross-fit embeddings, patient-excluded
  train     one arm x seed; early stopping on fold 9; predictions for fold 10
  gate      validation-only phase decision
  evaluate  test fold, read once: macro AUROC, per-class AUROC, macro AUPRC, bootstrap
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptbxl_h2 as h1  # noqa: E402

K = 8
PARTS = 4
CLASSES = h1.CLASSES
# sig: delivered neighbour signal (None = no signal tokens); lab: neighbour labels;
# prob: the query's own out-of-fold retriever-encoder probabilities (ensemble control).
ARMS = {"A0": {"sig": None, "lab": False, "prob": False},
        "P": {"sig": None, "lab": False, "prob": True},
        "L1": {"sig": None, "lab": True, "prob": False},
        "L3": {"sig": "raw", "lab": True, "prob": False},
        "L3n": {"sig": "raw", "lab": False, "prob": False}}
for _r in "RWC":
    ARMS[f"{_r}64"] = {"sig": f"{_r}64", "lab": True, "prob": False}
    ARMS[f"{_r}64n"] = {"sig": f"{_r}64", "lab": False, "prob": False}
PHASE1 = ["A0", "P", "L1", "L3", "L3n"]
PHASE2 = ["R64n", "W64n", "C64n", "R64", "W64", "C64"]
COMPARISONS = [("L3", "A0"), ("L3", "P"), ("L3n", "A0"), ("C64n", "R64n"), ("C64n", "W64n"),
               ("L1", "A0"), ("L1", "P"), ("L3", "L1"), ("P", "A0"), ("C64", "R64"), ("C64", "W64"),
               ("C64n", "A0"), ("kNN", "A0")]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def link(out: Path, source: Path):
    derived = out / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    report = {"source": str(source)}
    for name in ("lead2.npz", "contexts.npz"):
        target = derived / name
        if not target.exists():
            target.symlink_to((source / "derived" / name).resolve())
        report[name] = sha256(target)
    report["provenance"] = h1.provenance()
    (out / "link.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def part_of(fold, patient):
    """Cross-fit part: training folds pair up (1-2, 3-4, 5-6, 7-8); held-out records by patient."""
    return np.where(fold <= 8, (fold - 1) // 2, patient % PARTS)


# -- model ---------------------------------------------------------------------------
def build_model(cfg: dict):
    import torch
    from torch import nn

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder = h1.build_model("A0").encoder  # the pilot-1 CNN, fresh weights
            self.retrieval = cfg["sig"] is not None or cfg["lab"]
            width = 64
            if self.retrieval:
                token = (64 if cfg["sig"] else 0) + (5 if cfg["lab"] else 0) + 1  # + similarity
                self.query = nn.Linear(64, 32)
                self.key = nn.Linear(token, 32)
                self.value = nn.Sequential(nn.Linear(token, 64), nn.ReLU())
                width += 64
            if cfg["prob"]:
                width += 5
            self.head = nn.Sequential(nn.Linear(width, 64), nn.ReLU(), nn.Linear(64, 5))

        def embed(self, q):
            return self.encoder(q)

        def forward(self, q, ctx, labels, sims, probs):
            e = self.encoder(q)
            parts = [e]
            if self.retrieval:
                tokens = []
                if cfg["sig"]:
                    b, k, n = ctx.shape
                    tokens.append(self.encoder(ctx.reshape(b * k, n)).reshape(b, k, -1))
                if cfg["lab"]:
                    tokens.append(labels)
                tokens.append(sims.unsqueeze(-1))
                t = torch.cat(tokens, -1)
                scores = (self.key(t) @ self.query(e).unsqueeze(-1)).squeeze(-1) / 32 ** 0.5
                weights = torch.softmax(scores, 1).unsqueeze(-1)
                parts.append((weights * self.value(t)).sum(1))
            if cfg["prob"]:
                parts.append(probs)
            return self.head(torch.cat(parts, 1))

    return Model()


def fit(model, make_batch, y, train_idx, val_idx, epochs, patience, device):
    """Adam 1e-3, batch 128, early stopping on validation macro AUROC (as in pilot 1)."""
    import torch
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = torch.nn.BCEWithLogitsLoss()
    yt = torch.tensor(y)

    def predict(idx):
        model.eval()
        out = []
        with torch.no_grad():
            for lo in range(0, len(idx), 128):
                out.append(torch.sigmoid(model(*make_batch(idx[lo:lo + 128]))).cpu().numpy())
        return np.concatenate(out)

    best, best_state, wait, history = -1.0, None, 0, []
    for epoch in range(epochs):
        model.train()
        order = np.random.permutation(train_idx)
        for lo in range(0, len(order), 128):
            b = order[lo:lo + 128]
            opt.zero_grad()
            loss = loss_fn(model(*make_batch(b)), yt[b].to(device))
            loss.backward()
            opt.step()
        val = h1.macro_auroc(y[val_idx], predict(val_idx))
        history.append({"epoch": epoch + 1, "val_macro_auroc": val})
        if val > best:
            best, wait = val, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            wait += 1
            if wait >= patience:
                break
    model.load_state_dict(best_state)
    return best, history, predict


def device_and_inputs(out: Path):
    import torch
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    x, y, patient, fold, _ = h1.load(out)
    xz = torch.tensor(h1.zscore(x).astype(np.float32))
    return device, xz, y, patient, fold


# -- retriever -----------------------------------------------------------------------
def encoders(out: Path, epochs: int, patience: int, smoke: bool):
    import torch
    device, xz, y, patient, fold = device_and_inputs(out)
    part = part_of(fold, patient)
    val = np.where(fold == 9)[0]
    emb = np.zeros((PARTS, len(y), 64), dtype=np.float32)
    probs = np.zeros((PARTS, len(y), 5), dtype=np.float32)
    rows = []
    for j in range(PARTS):
        torch.manual_seed(1000 + j)
        np.random.seed(1000 + j)
        train_idx = np.where((fold <= 8) & (part != j))[0]
        val_idx = val
        if smoke:
            train_idx, val_idx = train_idx[:512], val_idx[:512]
        model = build_model(ARMS["A0"]).to(device)

        def make_batch(b):
            return xz[b].to(device), None, None, None, None
        started = time.perf_counter()
        best, history, predict = fit(model, make_batch, y, train_idx, val_idx, epochs, patience, device)
        everything = np.arange(len(y))  # forward passes only; no held-out labels are read
        probs[j] = predict(everything)
        model.eval()
        with torch.no_grad():
            e = np.concatenate([model.embed(xz[everything[lo:lo + 512]].to(device)).cpu().numpy()
                                for lo in range(0, len(y), 512)])
        emb[j] = e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-12)
        rows.append({"part": j, "train_records": int(len(train_idx)), "best_val_macro_auroc": best,
                     "epochs_run": len(history), "train_s": time.perf_counter() - started})
    assigned = probs[part, np.arange(len(y))]
    tr = np.where(fold <= 8)[0]
    report = {"encoders": rows, "smoke": smoke,
              "oof_train_macro_auroc": h1.macro_auroc(y[tr], assigned[tr]),
              "assigned_val_macro_auroc": h1.macro_auroc(y[val], assigned[val]),
              "provenance": h1.provenance()}
    np.savez(out / "derived" / "encoders.npz", emb=emb, probs=probs, part=part)
    (out / "encoders.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def retrieve(out: Path):
    _, y, patient, fold, _ = h1.load(out)
    enc = np.load(out / "derived" / "encoders.npz")
    emb, part = enc["emb"], enc["part"]
    gallery = np.where(fold <= 8)[0]
    neighbours = np.zeros((len(y), K), dtype=np.int64)
    sims_out = np.zeros((len(y), K), dtype=np.float32)
    started = time.perf_counter()
    for j in range(PARTS):
        queries = np.where(part == j)[0]
        g = emb[j][gallery].astype(np.float64)
        for lo in range(0, len(queries), 2048):
            q = queries[lo:lo + 2048]
            sims = emb[j][q].astype(np.float64) @ g.T
            sims[patient[q][:, None] == patient[gallery][None, :]] = -np.inf
            top = np.argpartition(-sims, K, axis=1)[:, :K]
            order = np.argsort(-np.take_along_axis(sims, top, 1), axis=1, kind="stable")
            top = np.take_along_axis(top, order, 1)
            neighbours[q] = gallery[top]
            sims_out[q] = np.take_along_axis(sims, top, 1)
    elapsed = time.perf_counter() - started
    assert not (patient[neighbours] == patient[:, None]).any()
    assert (fold[neighbours] <= 8).all()
    assert np.isfinite(sims_out).all() and (np.abs(sims_out) <= 1 + 1e-6).all()
    np.savez(out / "derived" / "neighbours.npz", retrieved=neighbours, sims=sims_out)
    val = np.where(fold == 9)[0]
    report = {"k": K, "gallery": int(len(gallery)), "queries": int(len(y)), "exhaustive_retrieval_s": elapsed,
              "retriever": "cosine on cross-fit A0-encoder embeddings (64-d), exact",
              "same_patient_neighbours": 0,
              "kNN_label_val_macro_auroc": h1.macro_auroc(y[val], y[neighbours[val]].mean(1)),
              "provenance": h1.provenance()}
    (out / "retrieve.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


# -- training ------------------------------------------------------------------------
def train(out: Path, arm: str, seed: int, epochs: int, patience: int, smoke: bool):
    import torch
    torch.manual_seed(seed)
    np.random.seed(seed)
    cfg = ARMS[arm]
    device, xz, y, patient, fold = device_and_inputs(out)
    nb = np.load(out / "derived" / "neighbours.npz")
    neighbours = torch.tensor(nb["retrieved"])
    sims = torch.tensor(nb["sims"])
    enc = np.load(out / "derived" / "encoders.npz")
    own = torch.tensor(enc["probs"][enc["part"], np.arange(len(y))])
    yt = torch.tensor(y)
    ctx_bank = position = None
    if cfg["sig"]:
        ctxz = np.load(out / "derived" / "contexts.npz")
        ctx_bank = torch.tensor(ctxz[cfg["sig"]])
        pos = np.full(len(y), -1)
        pos[ctxz["gallery"]] = np.arange(len(ctxz["gallery"]))
        position = torch.tensor(pos)

    def make_batch(b):
        b = torch.tensor(b)
        nbr = neighbours[b]
        ctx = ctx_bank[position[nbr]].to(device) if cfg["sig"] else None
        return xz[b].to(device), ctx, yt[nbr].to(device), sims[b].to(device), own[b].to(device)

    split = {s: np.where(m)[0] for s, m in (("train", fold <= 8), ("val", fold == 9), ("test", fold == 10))}
    if smoke:
        split = {k: v[:512] for k, v in split.items()}
    model = build_model(cfg).to(device)
    params = sum(p.numel() for p in model.parameters())
    started = time.perf_counter()
    best, history, predict = fit(model, make_batch, y, split["train"], split["val"], epochs, patience, device)
    train_s = time.perf_counter() - started
    run = out / "runs" / f"{arm}_seed{seed}"
    run.mkdir(parents=True, exist_ok=True)
    if not smoke:  # Smoke runs never touch the test fold.
        np.save(run / "test_probs.npy", predict(split["test"]).astype(np.float32))
    result = {"arm": arm, "config": cfg, "seed": seed, "parameters": params, "best_val_macro_auroc": best,
              "epochs_run": len(history), "history": history, "train_s": train_s, "device": str(device),
              "smoke": smoke, "provenance": h1.provenance()}
    (run / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: result[k] for k in ("arm", "seed", "parameters", "best_val_macro_auroc",
                                             "epochs_run", "train_s")}))


def gate(out: Path):
    """Validation only. Phase 2 runs if mean val macro AUROC of L3n exceeds A0."""
    rows = {}
    for arm in ARMS:
        vals = [json.loads(p.read_text())["best_val_macro_auroc"]
                for p in sorted((out / "runs").glob(f"{arm}_seed*/result.json"))
                if not json.loads(p.read_text())["smoke"]]
        if vals:
            rows[arm] = {"seeds": len(vals), "val_macro_auroc_mean": statistics.fmean(vals),
                         "val_macro_auroc_seeds": vals}
    diff = (rows["L3n"]["val_macro_auroc_mean"] - rows["A0"]["val_macro_auroc_mean"]
            if "L3n" in rows and "A0" in rows else None)
    report = {"note": "validation fold 9 only; test fold 10 not evaluated", "arms": rows,
              "gate_L3n_minus_A0": diff, "phase2": bool(diff is not None and diff > 0)}
    (out / "gate.json").write_text(json.dumps(report, indent=1))
    for arm, r in rows.items():
        print(f"{arm:5s} seeds={r['seeds']} val {r['val_macro_auroc_mean']:.4f} "
              f"{[round(v, 4) for v in r['val_macro_auroc_seeds']]}")
    print("gate L3n - A0 (validation):", diff, "-> phase 2" if report["phase2"] else "-> stop")


# -- evaluation ----------------------------------------------------------------------
def evaluate(out: Path, resamples: int):
    from sklearn.metrics import average_precision_score, roc_auc_score
    _, y, patient, fold, _ = h1.load(out)
    test = np.where(fold == 10)[0]
    yt, pt = y[test], patient[test]
    probs, seeds = {}, {}
    for arm in ARMS:
        runs = sorted((out / "runs").glob(f"{arm}_seed*/test_probs.npy"))
        if runs:
            stack = np.stack([np.load(r) for r in runs])
            probs[arm] = stack.mean(0)
            seeds[arm] = [h1.macro_auroc(yt, s) for s in stack]
    nb = np.load(out / "derived" / "neighbours.npz")["retrieved"]
    probs["kNN"] = y[nb[test]].mean(1)  # untrained: mean retrieved neighbour labels
    seeds["kNN"] = []
    rng = np.random.default_rng(7)
    patients = np.unique(pt)
    members = {p: np.where(pt == p)[0] for p in patients}
    draws = [np.concatenate([members[p] for p in rng.choice(patients, len(patients))])
             for _ in range(resamples)]
    draws = [d for d in draws if not (yt[d].sum(0) == 0).any() and not (yt[d].min(0) == 1).any()]

    def boot(a, b):
        diffs = np.array([h1.macro_auroc(yt[d], probs[a][d]) - h1.macro_auroc(yt[d], probs[b][d])
                          for d in draws])
        return {"diff": h1.macro_auroc(yt, probs[a]) - h1.macro_auroc(yt, probs[b]),
                "ci95": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
                "resamples_used": int(len(diffs))}
    table = {arm: {"macro_auroc": h1.macro_auroc(yt, p), "seed_macro_auroc": seeds[arm],
                   "per_class_auroc": {c: float(roc_auc_score(yt[:, i], p[:, i])) for i, c in enumerate(CLASSES)},
                   "macro_auprc": float(np.mean([average_precision_score(yt[:, i], p[:, i]) for i in range(5)]))}
             for arm, p in probs.items()}
    report = {"test_records": int(len(test)), "test_patients": int(len(patients)),
              "bootstrap_resamples": resamples, "arms": table,
              "comparisons": {f"{a} vs {b}": boot(a, b) for a, b in COMPARISONS if a in probs and b in probs},
              "provenance": h1.provenance()}
    (out / "evaluation.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({a: round(v["macro_auroc"], 4) for a, v in table.items()}))
    for name, c in report["comparisons"].items():
        print(f"{name:14s} diff {c['diff']:+.4f}  ci95 [{c['ci95'][0]:+.4f}, {c['ci95'][1]:+.4f}]")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["link", "encoders", "retrieve", "train", "gate", "evaluate"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", type=Path, help="pilot-1 output directory (link stage)")
    parser.add_argument("--arm", choices=list(ARMS))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--smoke", action="store_true", help="512 records per split, for checks only")
    parser.add_argument("--resamples", type=int, default=1000)
    a = parser.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.stage == "link":
        link(a.out, a.source)
    elif a.stage == "encoders":
        encoders(a.out, a.epochs, a.patience, a.smoke)
    elif a.stage == "retrieve":
        retrieve(a.out)
    elif a.stage == "train":
        train(a.out, a.arm, a.seed, a.epochs, a.patience, a.smoke)
    elif a.stage == "gate":
        gate(a.out)
    else:
        evaluate(a.out, a.resamples)


if __name__ == "__main__":
    main()
