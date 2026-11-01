#!/usr/bin/env python3
"""Figures for the manuscript, from committed result files only.

  .venv-paper/bin/python -I research/paper/figures.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("pdf")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "fig"
# validated categorical slots 1-4 (dataviz reference palette, light); marker shape is the secondary encoding
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3de"

plt.rcParams.update({"font.family": "serif", "font.size": 7.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
                     "legend.frameon": False, "pdf.fonttype": 42})


def storage():
    r = json.loads((ROOT / "evaluation/storage_baselines/results/b1.json").read_text())["inputs"]
    names = {"cx": "CX seismic counts", "ims": "IMS 3-decimal", "pad-c": "Paderborn counts",
             "pad-p": "Paderborn physical", "sleep": "Sleep-EDF EEG", "ecg": "PTB-XL ECG"}
    series = [("ALP", "ALP (DuckDB), floats", BLUE, "s"), ("Z", "zstd-19, floats", ORANGE, "D"),
              ("M", "MorletDB, floats + quantum", AQUA, "o"), ("FLAC", "FLAC, integer counts", YELLOW, "^")]
    fig, ax = plt.subplots(figsize=(3.33, 2.0))
    keys = list(names)[::-1]
    for y, k in enumerate(keys):
        vals = [r[k][s] for s, *_ in series]
        ax.plot([min(vals), max(vals)], [y, y], color=GRID, lw=1.0, zorder=1)
    for s, label, color, marker in series:
        ax.scatter([r[k][s] for k in keys], range(len(keys)), s=18, color=color, marker=marker, label=label, zorder=3,
                   edgecolors="white", linewidths=0.6)
    ax.set_yticks(range(len(keys)), [names[k] for k in keys])
    ax.set_xlabel("bits per sample (lossless)")
    ax.set_xlim(0, 66)
    ax.grid(axis="x", color=GRID, lw=0.5)
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.42, -0.25), ncol=2, handletextpad=0.2, columnspacing=1.0)
    fig.savefig(OUT / "storage.pdf", bbox_inches="tight", metadata={"CreationDate": None})


def scaling():
    """R2 latency of the exhaustive scan; R2-D shortlists: exact zero-lag (E, solid) vs HNSW (H*, dashed)."""
    rs = ROOT / "evaluation/retrieval_scaling"
    b = json.loads((rs / "results/bench.json").read_text())
    load = json.loads((rs / "results/load.json").read_text())
    d = json.loads((rs / "diagnostic/results/diag.json").read_text())
    h = json.loads((rs / "diagnostic/results/hstar.json").read_text())
    tiers = ["0", "1", "2", "3"]
    n = {t: load["windows_per_tier"][t] for t in tiers}

    def series(src, key, arm):
        pts = [(n[t], src["tiers"][t][key][arm]) for t in tiers if arm in src["tiers"][t][key]]
        return [p[0] for p in pts], [p[1] for p in pts]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(3.33, 1.75))
    a1.plot([n[t] for t in tiers], [b["tiers"][t]["latency_ms"]["A"]["p50"] for t in tiers], color=BLUE, marker="o",
            ms=3.5, lw=1.2, label="exhaustive lag-tolerant")
    for src, arm, color, ls, mk, label in [(d, "E-1000", ORANGE, "-", "D", "exact zero-lag top 1000"),
                                           (h, "H*-1000", ORANGE, "--", "D", "HNSW top 1000"),
                                           (d, "E-100", AQUA, "-", "s", "exact zero-lag top 100"),
                                           (h, "H*-100", AQUA, "--", "s", "HNSW top 100")]:
        x, y = series(src, "latency_ms", arm)
        a1.plot(x, [v["p50"] for v in y], color=color, ls=ls, marker=mk, ms=3.5, lw=1.2,
                mfc="white" if ls == "--" else color, label=label + " + re-rank")
        x, y = series(src, "recall_at_10", arm)
        a2.plot(x, y, color=color, ls=ls, marker=mk, ms=3.5, lw=1.2, mfc="white" if ls == "--" else color)
    x, y = series(d, "recall_at_10", "ZE")
    a2.plot(x, y, color=YELLOW, marker="^", ms=3.5, lw=1.2, label="exact zero-lag top 10")
    for ax in (a1, a2):
        ax.set_xscale("log")
        ax.set_xlabel("windows in gallery")
        ax.grid(color=GRID, lw=0.5)
    a1.set_yscale("log")
    a1.set_ylabel("median latency (ms)")
    a2.set_ylim(0, 1.02)
    a2.set_ylabel("recall@10 vs exhaustive")
    h1, l1 = a1.get_legend_handles_labels()
    h2, l2 = a2.get_legend_handles_labels()
    fig.legend(h1 + h2, l1 + l2, loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=2, fontsize=6.3,
               handletextpad=0.3, columnspacing=0.8)
    fig.tight_layout(w_pad=1.2)
    fig.savefig(OUT / "scaling.pdf", bbox_inches="tight", metadata={"CreationDate": None})


def probes():
    """E1, held-out CX 2021 (consolidated build): recall@10 by the best lag of each exact top-ten item."""
    r = json.loads((ROOT / "evaluation/consolidated/results/e1_heldout.json").read_text())["summary"]
    bins = ["0-5", "6-15", "16-30"]
    arms = [("EZ", "zero-lag shortlist, same size", YELLOW), ("PR", "13 shifted probes (HNSW, product)", AQUA),
            ("PX-13", "13 shifted probes (exact)", BLUE)]
    fig, ax = plt.subplots(figsize=(3.33, 1.7))
    w = 0.26
    for j, (arm, label, color) in enumerate(arms):
        ys = [r[arm]["recall"]["by_lag_band"][b]["recall"] for b in bins]
        ax.bar([i + (j - 1) * w for i in range(len(bins))], ys, width=w - 0.03, color=color, label=label)
        for i, y in enumerate(ys):
            if y < 0.05:
                ax.text(i + (j - 1) * w, 0.02, "0", ha="center", va="bottom", fontsize=6.5, color=MUTED)
    n = [r["EX-2"]["recall"]["by_lag_band"][b]["items"] for b in bins]
    ax.set_xticks(range(len(bins)), [f"|lag| {b}\n({k} items)" for b, k in zip(bins, n)])
    ax.set_ylabel("recall@10 vs exact")
    ax.set_ylim(0, 1.05)
    ax.grid(axis="y", color=GRID, lw=0.5)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=2, fontsize=6.3, handlelength=1.2, columnspacing=0.8)
    fig.savefig(OUT / "probes.pdf", bbox_inches="tight", metadata={"CreationDate": None})


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    storage()
    scaling()
    probes()
