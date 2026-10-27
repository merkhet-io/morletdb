#!/usr/bin/env python3
"""C4: labels of the nearest earlier occurrences vs the well's last label, on 3W (PROTOCOL.md).

Usage: c4.py DATASET_DIR OUT   (compression environment: numpy, pyarrow; run with python -I)
DATASET_DIR is the extracted 3W `dataset` folder (subfolders 0..9). Writes OUT/c4.json.
Synthetic checks: test_c4.py.
"""
import json
import re
import sys
import warnings
from collections import Counter
from pathlib import Path

import numpy as np

WINDOW = 3600
SHARE = 0.8
K = 5
MIN_SHARED = 3
VALUES = ("std", "crest", "kurtosis", "p75")
CHANNELS = ("ABER-CKGL", "ABER-CKP", "P-ANULAR", "P-JUS-BS", "P-JUS-CKGL", "P-JUS-CKP", "P-MON-CKGL", "P-MON-CKP",
            "P-MON-SDV-P", "P-PDG", "PT-P", "P-TPT", "QBS", "QGL", "T-JUS-CKP", "T-MON-CKP", "T-PDG", "T-TPT")
NAME = re.compile(r"^(WELL-\d{5})_(\d{14})\.parquet$")
DAY = 86400


def seq_sum(a):
    """Left-to-right float64 sum, as Rust's Iterator::sum in morlet_core (numpy's sum is pairwise)."""
    return np.cumsum(a)[-1]


def channel_values(x):
    """simple-v1 std, crest, kurtosis, p75 (morlet_core::values) of one window; None if the product rejects it.
    Sums are sequential so that near-constant windows give the product's exact values."""
    if not np.isfinite(x).all() or np.abs(x).max() > 1e9:
        return None
    n = len(x)
    m = seq_sum(x) / n
    d = x - m
    m2 = seq_sum(d * d) / n
    m4 = seq_sum((d * d) * (d * d)) / n
    rms = np.sqrt(seq_sum(x * x) / n)
    peak = np.max(np.abs(x))
    return [np.sqrt(m2), peak / rms if rms > 0 else 0.0, m4 / (m2 * m2) if m2 > 0 else 0.0,
            float(np.percentile(x, 75))]


def majority(codes):
    """The value held by at least SHARE of the observations (NaN counts as a value), else 'mixed'."""
    keys = ["nan" if np.isnan(c) else int(c) for c in codes]
    value, n = Counter(keys).most_common(1)[0]
    return value if n >= SHARE * len(keys) else "mixed"


def window_label(cls):
    mapped = np.where(cls >= 100, cls - 100, cls)
    v = majority(mapped)
    return v if isinstance(v, int) else None


def window_context(state):
    v = majority(state)
    return "unknown" if v == "nan" else str(v)


def instance_windows(well, instance, t0, cls, state, columns):
    """1-hour windows of one instance: dicts with well, instance, start, label, context, vals (C x 4, NaN = absent)."""
    out = []
    for w in range(len(cls) // WINDOW):
        s = slice(w * WINDOW, (w + 1) * WINDOW)
        vals = np.full((len(CHANNELS), len(VALUES)), np.nan)
        for c, name in enumerate(CHANNELS):
            if name in columns:
                v = channel_values(np.asarray(columns[name][s], float))
                if v is not None:
                    vals[c] = v
        out.append({"well": well, "instance": instance, "start": t0[w], "label": window_label(cls[s]),
                    "context": window_context(state[s]), "vals": vals})
    return out


def load(dataset_dir: Path):
    import pyarrow.parquet as pq
    windows, rejected, irregular = [], [], 0
    for p in sorted(dataset_dir.glob("[0-9]/*.parquet")):
        m = NAME.match(p.name)
        if not m:
            continue  # simulated and hand-drawn instances
        t = pq.read_table(p)
        names = t.column_names
        ts_name = "timestamp" if "timestamp" in names else next((n for n in names if n.startswith("__index")), None)
        if ts_name is None or "class" not in names or "state" not in names:
            rejected.append({"file": p.name, "reason": f"columns {names}"})
            continue
        ts = t.column(ts_name).cast("timestamp[s]").to_numpy().astype("datetime64[s]").astype(np.int64)
        cls = t.column("class").to_numpy(zero_copy_only=False).astype(float)
        state = t.column("state").to_numpy(zero_copy_only=False).astype(float)
        cols = {n: t.column(n).to_numpy(zero_copy_only=False).astype(float) for n in CHANNELS if n in names}
        n = len(ts) // WINDOW
        if n == 0:
            rejected.append({"file": p.name, "reason": f"{len(ts)} observations, shorter than one window"})
            continue
        irregular += int(np.sum(np.diff(ts[: n * WINDOW]) != 1))
        instance = f"{p.parent.name}/{p.stem}"
        windows += instance_windows(m.group(1), instance, ts[: n * WINDOW: WINDOW], cls, state, cols)
    return windows, rejected, irregular


def prior_label(labels):
    c = Counter(labels)
    return min(c, key=lambda k: (-c[k], k))


def neighbours(q, V, cand, order_key):
    """Indices of up to K comparable candidates nearest to q, with distances and squared contributions.
    Medians over all candidates (the context's references), as morlet_history_neighbors."""
    if len(cand) == 0:
        return [], [], None, 0
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN channels: absent
        med = np.nanmedian(V[cand], axis=0)
        usable = np.isfinite(med) & (med != 0)
        rel = np.where(usable, (V[q] - V[cand]) / np.where(usable, np.abs(med), 1.0), np.nan)
        sq = rel ** 2
        dist = np.sqrt(np.nanmean(sq.reshape(len(cand), -1), axis=1))
    shared = (np.isfinite(V[q][:, 0]) & np.isfinite(V[cand][:, :, 0])).sum(axis=1)
    ok = (shared >= MIN_SHARED) & np.isfinite(dist)
    idx = np.flatnonzero(ok)
    idx = idx[np.lexsort((order_key[cand[idx]], dist[idx]))][:K]
    return cand[idx], dist[idx], sq[idx], int(ok.sum())


def vote(labels, dists):
    c = Counter(labels)
    top = max(c.values())
    tied = [lab for lab in c if c[lab] == top]
    return min(tied, key=lambda lab: (sum(d for la, d in zip(labels, dists) if la == lab), lab))


def predict(windows):
    W = len(windows)
    V = np.stack([w["vals"] for w in windows])
    start = np.array([w["start"] for w in windows])
    inst = np.array([w["instance"] for w in windows])
    well = np.array([w["well"] for w in windows])
    ctx = np.array([w["context"] for w in windows])
    lab = np.array([-1 if w["label"] is None else w["label"] for w in windows])
    order_key = np.lexsort((start, inst))  # product ties: recording, then start
    rank = np.empty(W, int)
    rank[order_key] = np.arange(W)
    labelled = lab >= 0
    rows, excluded = [], Counter()
    contrib = np.zeros((len(CHANNELS), len(VALUES)))
    for q in range(W):
        if not labelled[q]:
            excluded["no label"] += 1
            continue
        if ctx[q] in ("mixed", "unknown"):
            excluded[f"context {ctx[q]}"] += 1
            continue
        refs = labelled & (inst != inst[q]) & (start < start[q])
        same = np.flatnonzero(refs & (ctx == ctx[q]))
        nb, nd, sq, comparable = neighbours(q, V, same, rank)
        if comparable < K:
            excluded["fewer than 5 comparable references"] += 1
            continue
        ref_idx = np.flatnonzero(refs)
        prior = prior_label(lab[ref_idx].tolist())
        own = ref_idx[well[ref_idx] == well[q]]
        last = int(lab[own[np.lexsort((rank[own], start[own]))[-1]]]) if len(own) else prior
        n_pred = vote(lab[nb].tolist(), nd.tolist())
        s = np.nan_to_num(sq).sum(axis=0)
        contrib += s / s.sum() if s.sum() > 0 else 0
        own_same = same[well[same] == well[q]]
        nb_own, nd_own, _, _ = neighbours(q, V, own_same, rank)
        nb_cf, nd_cf, _, _ = neighbours(q, V, ref_idx, rank)
        rows.append({
            "q": q, "well": well[q], "instance": inst[q], "start": int(start[q]), "context": ctx[q],
            "label": int(lab[q]), "N": n_pred, "B": last, "prior": prior,
            "N_own_well": vote(lab[nb_own].tolist(), nd_own.tolist()) if len(nb_own) else None,
            "N_context_free": vote(lab[nb_cf].tolist(), nd_cf.tolist()) if len(nb_cf) else None,
            "neighbours": [{"window": int(i), "well": well[i], "label": int(lab[i]), "distance": float(d),
                            "days_before": float((start[q] - start[i]) / DAY)} for i, d in zip(nb, nd)],
        })
    return rows, excluded, contrib


def groups(rows, arm):
    """(class, instance) -> well, fraction correct."""
    g = {}
    for r in rows:
        key = (r["label"], r["instance"])
        g.setdefault(key, {"well": r["well"], "n": 0, "ok": 0})
        g[key]["n"] += 1
        g[key]["ok"] += r[arm] == r["label"]
    return {k: (v["well"], v["ok"] / v["n"]) for k, v in g.items()}


def eligible_classes(rows):
    by = {}
    for r in rows:
        by.setdefault(r["label"], set()).add((r["instance"], r["well"]))
    return sorted(c for c, s in by.items() if len({i for i, _ in s}) >= 3 and len({w for _, w in s}) >= 2)


def balanced_accuracy(g, classes, wells=None):
    """Mean over classes of the mean over (class, instance) groups; wells (with multiplicity) restricts groups."""
    weight = Counter(wells) if wells is not None else None
    per = []
    for c in classes:
        acc = [(a, 1 if weight is None else weight[w]) for (cl, _), (w, a) in g.items() if cl == c]
        acc = [(a, n) for a, n in acc if n > 0]
        if not acc:
            return None
        per.append(sum(a * n for a, n in acc) / sum(n for _, n in acc))
    return float(np.mean(per))


def analyse(rows, resamples=2000, seed=7):
    classes = eligible_classes(rows)
    rows = [r for r in rows if r["label"] in classes]
    gN, gB = groups(rows, "N"), groups(rows, "B")
    ba_n, ba_b = balanced_accuracy(gN, classes), balanced_accuracy(gB, classes)
    wells = sorted({r["well"] for r in rows})
    rng = np.random.default_rng(seed)
    deltas, dropped = [], 0
    for _ in range(resamples):
        pick = [wells[i] for i in rng.integers(0, len(wells), len(wells))]
        n, b = balanced_accuracy(gN, classes, pick), balanced_accuracy(gB, classes, pick)
        if n is None or b is None:
            dropped += 1
            continue
        deltas.append(n - b)
    delta = ba_n - ba_b
    ci = [float(np.percentile(deltas, 2.5)), float(np.percentile(deltas, 97.5))]
    decision = "supported" if delta >= 0.05 and ci[0] > 0 else "not supported" if ci[1] < 0.05 else "inconclusive"
    reported = {}
    for arm in ("prior", "N_own_well", "N_context_free"):
        filled = [dict(r, **{arm: r[arm] if r[arm] is not None else r["prior"]}) for r in rows]
        reported[arm] = {"balanced_accuracy": balanced_accuracy(groups(filled, arm), classes),
                         "fallback_to_prior": sum(r[arm] is None for r in rows)}
    labels = sorted({r["label"] for r in rows} | {r[a] for r in rows for a in ("N", "B")})
    confusion = {arm: {str(t): {str(p): sum(1 for r in rows if r["label"] == t and r[arm] == p) for p in labels}
                       for t in classes} for arm in ("N", "B")}
    per_class = {str(c): {arm: balanced_accuracy(g, [c]) for arm, g in (("N", gN), ("B", gB))} for c in classes}
    per_context = {}
    for cx in sorted({r["context"] for r in rows}):
        sub = [r for r in rows if r["context"] == cx]
        cl = sorted({r["label"] for r in sub})
        per_context[cx] = {"windows": len(sub), "classes": cl,
                           **{arm: balanced_accuracy(groups(sub, arm), cl) for arm in ("N", "B")}}
    nbs = [(r, n) for r in rows for n in r["neighbours"]]
    return {
        "eligible_classes": classes, "queries": len(rows), "query_instances": len({r["instance"] for r in rows}),
        "wells": len(wells), "ba_N": ba_n, "ba_B": ba_b, "delta": delta, "ci95": ci,
        "bootstrap": {"valid": len(deltas), "dropped_missing_class": dropped}, "decision": decision,
        "reported": {
            **reported, "confusion": confusion, "per_class": per_class, "per_context": per_context,
            "neighbours_own_well_share": float(np.mean([n["well"] == r["well"] for r, n in nbs])),
            "neighbours_within_30_days_share": float(np.mean([n["days_before"] <= 30 for _, n in nbs])),
            "neighbours_own_well_within_30_days_share":
                float(np.mean([n["well"] == r["well"] and n["days_before"] <= 30 for r, n in nbs])),
        },
    }


def main():
    dataset_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    windows, rejected, irregular = load(dataset_dir)
    rows, excluded, contrib = predict(windows)
    res = analyse(rows)
    share = contrib / contrib.sum() if contrib.sum() > 0 else contrib
    res["reported"]["distance_share_by_value"] = dict(zip(VALUES, share.sum(axis=0).tolist()))
    res["reported"]["distance_share_by_channel"] = dict(zip(CHANNELS, share.sum(axis=1).tolist()))
    res.update({"windows": len(windows), "labelled_windows": sum(w["label"] is not None for w in windows),
                "contexts": dict(Counter(w["context"] for w in windows)), "excluded_queries": dict(excluded),
                "rejected_files": rejected, "irregular_steps_within_windows": irregular,
                "instances": len({w["instance"] for w in windows})})
    plain = lambda o: o.item() if hasattr(o, "item") else str(o)  # noqa: E731  numpy scalars
    (out / "c4.json").write_text(json.dumps({**res, "rows": rows}, indent=1, default=plain))
    print(json.dumps(res, indent=1, default=plain))


if __name__ == "__main__":
    main()
