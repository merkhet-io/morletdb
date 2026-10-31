#!/usr/bin/env python3
"""Stage B figure from results/stageb/*.json: worker backlog over time (W runs) and query latency during paced
ingestion (S-paced).

  .venv-paper/bin/python -I evaluation/bounded_lag/figure_stageb.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("pdf")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent / "results/stageb"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
MUTED, GRID = "#6b6a64", "#e4e3de"
plt.rcParams.update({"font.family": "serif", "font.size": 7.5, "axes.edgecolor": MUTED, "xtick.color": MUTED,
                     "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False})


def main():
    r = {t: json.loads((HERE / f"{t}.json").read_text()) for t in ("W-max", "W-paced", "S-paced")}
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(6.6, 2.2))
    for tag, color in (("W-max", ORANGE), ("W-paced", AQUA)):
        s = r[tag]["backlog"]["series"]
        t0 = s[0]["t"]
        a1.plot([b["t"] - t0 for b in s], [b["backlog"] for b in s], color=color, lw=1.2, label=tag)
    k = r["W-max"]["worker_killed_at_s"]
    a1.axvline(k, color=MUTED, ls=":", lw=0.8)
    a1.annotate("worker killed\nand restarted", (k, a1.get_ylim()[1] * 0.8), fontsize=6.3, color=MUTED, xytext=(4, 0),
                textcoords="offset points")
    a1.set_xlabel("seconds since replay start")
    a1.set_ylabel("completed windows not yet searchable")
    a1.legend(loc="upper right")
    q = r["S-paced"]["queries"]["series"]
    t0 = q[0]["t"]
    a2.scatter([x["visible"] for x in q], [x["x_ms"] for x in q], s=3, color=BLUE, label="exact parallel scan", alpha=0.6)
    a2.scatter([x["visible"] for x in q], [x["p_ms"] for x in q], s=3, color=ORANGE, label="13 probes × 50 + re-rank", alpha=0.6)
    a2.set_xlabel("searchable windows at query time (S-paced)")
    a2.set_ylabel("query latency (ms)")
    a2.legend(loc="upper left", markerscale=3)
    for ax in (a1, a2):
        ax.grid(color=GRID, lw=0.5)
    fig.tight_layout()
    fig.savefig(HERE / "stageb.pdf", bbox_inches="tight")
    fig.savefig(HERE / "stageb.png", dpi=200, bbox_inches="tight")


if __name__ == "__main__":
    main()
