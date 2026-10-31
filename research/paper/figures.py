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
    fig.savefig(OUT / "storage.pdf", bbox_inches="tight")


def scaling():
    path = ROOT / "evaluation/retrieval_scaling/results/bench.json"
    if not path.exists():
        return
    b = json.loads(path.read_text())
    load = json.loads((ROOT / "evaluation/retrieval_scaling/results/load.json").read_text())
    tiers = sorted(b["tiers"], key=int)
    n = [load["windows_per_tier"][t] for t in tiers]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(3.33, 1.7))
    arms = [("A", "exact scan", BLUE, "o"), ("H-1000", "HNSW 1000 + re-rank", ORANGE, "D"),
            ("H-100", "HNSW 100 + re-rank", AQUA, "s")]
    for arm, label, color, marker in arms:
        a1.plot(n, [b["tiers"][t]["latency_ms"][arm]["p50"] for t in tiers], color=color, marker=marker, ms=3.5,
                lw=1.2, label=label)
    a1.set_xscale("log")
    a1.set_yscale("log")
    a1.set_xlabel("windows in gallery")
    a1.set_ylabel("median latency (ms)")
    a1.grid(color=GRID, lw=0.5)
    for arm, label, color, marker in [("H-1000", "HNSW 1000 + re-rank", ORANGE, "D"), ("H-100", "HNSW 100 + re-rank", AQUA, "s"),
                                      ("Z", "zero-lag HNSW", YELLOW, "^")]:
        a2.plot(n, [b["tiers"][t]["recall_at_10"][arm] for t in tiers], color=color, marker=marker, ms=3.5, lw=1.2,
                label=label)
    a2.set_xscale("log")
    a2.set_ylim(0, 1.02)
    a2.set_xlabel("windows in gallery")
    a2.set_ylabel("recall@10 vs exact")
    a2.grid(color=GRID, lw=0.5)
    h1, l1 = a1.get_legend_handles_labels()
    h2, l2 = a2.get_legend_handles_labels()
    fig.legend(h1 + h2[-1:], l1 + l2[-1:], loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=2,
               handletextpad=0.3, columnspacing=1.0)
    fig.tight_layout(w_pad=1.2)
    fig.savefig(OUT / "scaling.pdf", bbox_inches="tight")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    storage()
    scaling()
