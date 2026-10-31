#!/usr/bin/env python3
"""BL1 figure from results/bl1.json: recall vs p95 latency (timed arms) and recall vs unique candidates
(exact probes, HNSW probes, equal-budget zero-lag shortlist).

  .venv-paper/bin/python -I evaluation/bounded_lag/figure.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("pdf")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3de"
plt.rcParams.update({"font.family": "serif", "font.size": 7.5, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
                     "legend.frameon": False, "pdf.fonttype": 42})


def main():
    s = json.loads((HERE / "results/bl1.json").read_text())["summary"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(6.6, 2.3))
    for arm, label, color, mk in [("A", "exhaustive, PL/pgSQL (A)", BLUE, "o"), ("X2", "exact SQL scan, 2 workers", BLUE, "s"),
                                  ("X8", "exact SQL scan, 3 workers", BLUE, "D")]:
        a1.scatter(s[arm]["latency_ms"]["p95"], s[arm]["recall_mean"], color=color, marker=mk, s=22, label=label, zorder=3)
    for g, color in (("G7", ORANGE), ("G13", AQUA)):
        xs = [s[f"P-{g}-{k}"]["latency_ms"]["p95"] for k in (50, 100)]
        ys = [s[f"P-{g}-{k}"]["recall_mean"] for k in (50, 100)]
        a1.plot(xs, ys, color=color, marker="o", ms=4, lw=1.2, label=f"HNSW probes {g} (K 50, 100)", zorder=3)
    a1.axvline(s["A"]["latency_ms"]["p95"] / 3, color=MUTED, lw=0.8, ls=":")
    a1.axhline(0.95, color=MUTED, lw=0.8, ls=":")
    a1.set_xscale("log")
    a1.set_xlabel("p95 latency (ms), 234,762 windows")
    a1.set_ylabel("recall@10 vs exhaustive")
    a1.set_ylim(0.6, 1.01)
    a1.legend(loc="lower left", fontsize=6.3)
    for prefix, label, color, mk, ls in [("PX", "exact probes", AQUA, "o", "-"), ("P", "HNSW probes", AQUA, "o", "--"),
                                         ("EZ", "zero-lag shortlist, same budget", YELLOW, "^", "-")]:
        for g, mk2 in (("G7", "s"), ("G13", "o")):
            arms = [f"{prefix}-{g}-{k}" for k in (50, 100)]
            xs = [s[a]["unique_candidates"]["mean"] for a in arms]
            ys = [s[a]["recall_mean"] for a in arms]
            a2.plot(xs, ys, color=color if prefix != "PX" else BLUE, ls=ls, marker=mk2, ms=4, lw=1.2,
                    mfc="white" if prefix == "P" else None, label=f"{label} {g}")
    a2.axhline(0.95, color=MUTED, lw=0.8, ls=":")
    a2.set_xlabel("unique candidates re-ranked per query (mean)")
    a2.set_ylim(0.6, 1.01)
    a2.legend(loc="center right", bbox_to_anchor=(1.0, 0.62), fontsize=6.3)
    for ax in (a1, a2):
        ax.grid(color=GRID, lw=0.5)
    fig.tight_layout()
    fig.savefig(HERE / "results/bl1.pdf", bbox_inches="tight")
    fig.savefig(HERE / "results/bl1.png", dpi=200, bbox_inches="tight")


if __name__ == "__main__":
    main()
