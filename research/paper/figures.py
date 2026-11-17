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


def speed():
    """E7, one session, eight workers: p95 latency by gallery size for the four arms (all 200 timings each)."""
    e = json.loads((ROOT / "evaluation/screened_exact/results/summary.json").read_text())
    sizes = ["N1", "N2", "N3"]
    arms = [("X-FULL", "exact, every window scored", ORANGE, "D", "-"),
            ("X-PG", "exact, screened (identical results)", BLUE, "s", "-"),
            ("P-PG", "13 shifted probes, re-ranked", AQUA, "o", "-"),
            ("X-MAT32R", "in-memory exact scan, outside the database", YELLOW, "^", "--")]
    fig, ax = plt.subplots(figsize=(3.33, 1.9))
    x = [e[n]["w8"]["windows"] / 1e6 for n in sizes]
    for arm, label, color, marker, ls in arms:
        y = [e[n]["w8"]["request_ms"][arm]["all"]["p95"] for n in sizes]
        ax.plot(x, y, color=color, marker=marker, ms=3.5, lw=1.2, ls=ls, label=label,
                mfc="white" if ls == "--" else color)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(x, [f"{v:.2f}" for v in x])
    ax.minorticks_off()
    ax.set_yticks([30, 100, 300, 1000, 2000], ["30", "100", "300", "1000", "2000"])
    ax.set_xlabel("windows in gallery (millions)")
    ax.set_ylabel("p95 latency (ms)")
    ax.grid(color=GRID, lw=0.5)
    ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.28), ncol=1, fontsize=6.3, handlelength=1.6)
    fig.savefig(OUT / "speed.pdf", bbox_inches="tight", metadata={"CreationDate": None})


def industrial():
    """IU1 (Bosch, scoring runs against the good history) and IU2 (tool wear, end of life): AUROC with 95% CIs."""
    d = json.loads((ROOT / "evaluation/industrial_iu1/results/drift_summary.json").read_text())["1_all_runs"]["all"]
    w = json.loads((ROOT / "evaluation/industrial_iu2/results/summary.json").read_text())["conf"]["W1"]
    shape, level = BLUE, ORANGE
    panels = [("CNC milling: bad vs good runs\n(1,183 runs, 36 bad)",
               [("bounded lag (BL)", d["BL"]["auroc"], shape), ("zero lag (ZL)", d["ZL"]["auroc"], shape),
                ("RMS", d["RMS"]["auroc"], level), ("run length", d["DUR"]["auroc"], level)]),
              ("Tool wear: last tenth of life\n(7 tools, 429 cycles)",
               [("bounded lag (BL)", w["BL"], shape), ("zero lag (ZL)", w["ZL"], shape),
                ("RMS per window", w["RMSW"], level), ("vibration level$^*$", w["VIB"], level),
                ("spindle current$^*$", w["CUR"], level), ("cycle count", w["TIU"], level)])]
    fig, axes = plt.subplots(2, 1, figsize=(3.33, 2.3), sharex=True, gridspec_kw={"height_ratios": [4, 6]})
    for ax, (title, rows) in zip(axes, panels):
        rows = rows[::-1]
        for y, (label, (p, lo, hi), color) in enumerate(rows):
            ax.plot([lo, hi], [y, y], color=color, lw=1.0, solid_capstyle="round")
            ax.scatter([p], [y], s=16, color=color, marker="o" if color == shape else "s", zorder=3,
                       edgecolors="white", linewidths=0.6)
        ax.axvline(0.5, color=MUTED, lw=0.6, ls=":")
        ax.set_yticks(range(len(rows)), [r[0] for r in rows])
        ax.tick_params(axis="y", length=0)
        ax.set_title(title.replace("\n", " "), fontsize=6.8, color=INK, loc="left")
        ax.set_xlim(0.4, 1.005)
        ax.set_ylim(-0.6, len(rows) - 0.4)
        ax.grid(axis="x", color=GRID, lw=0.5)
    axes[-1].set_xlabel("AUROC with 95% interval (0.5 = chance)")
    from matplotlib.lines import Line2D
    handles = [Line2D([], [], color=shape, marker="o", lw=1.0, ms=3.5, label="shape similarity (envelopes)"),
               Line2D([], [], color=level, marker="s", lw=1.0, ms=3.5, label="level or duration")]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.0), ncol=2, fontsize=6.3)
    fig.tight_layout(h_pad=0.4, rect=(0, 0.035, 1, 1))
    fig.savefig(OUT / "industrial.pdf", bbox_inches="tight", metadata={"CreationDate": None})


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    storage()
    scaling()
    probes()
    speed()
    industrial()
