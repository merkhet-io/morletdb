"""IU2 figures from results/summary.json and results/cycle_scores.json. Usage: python figures.py de|en"""
import json, sys
from pathlib import Path
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
LANG = sys.argv[1] if len(sys.argv) > 1 else "en"
R = Path("/w/iu2/results"); OUT = Path("/w/iu2/figures") / LANG; OUT.mkdir(parents=True, exist_ok=True)
S = json.loads((R / "summary.json").read_text()); C = json.loads((R / "cycle_scores.json").read_text())
BLUE, GREY, INK, MUTED, GRID, ORANGE = "#2a78d6", "#8a8983", "#0b0b0b", "#6b6a65", "#e1e0d9", "#eb6834"
T = {"en": dict(t1="End of tool life across seven tools: AUROC per score (95% interval)", x="AUROC (0.5 = chance)",
                arms=["Vibration level", "Spindle current", "RMS per window", "Time in use", "Envelope shape, with lag", "Envelope shape, zero lag", "Cycle length"],
                t2="Tool 9, 149 cycles to failure", life="Tool life used (%)",
                y1="Envelope distance to first layer", y2="Vibration level, log ratio to first layer", l1="Envelope shape, with lag", l2="Vibration level", last="last tenth"),
     "de": dict(t1="Ende der Standzeit über sieben Werkzeuge: AUROC je Kennwert (95-%-Intervall)", x="AUROC (0,5 = Zufall)",
                arms=["Schwingungsstärke", "Spindelstrom", "RMS je Fenster", "Betriebszeit", "Hüllkurvenform, mit Lag", "Hüllkurvenform, ohne Lag", "Zyklusdauer"],
                t2="Werkzeug 9, 149 Zyklen bis zum Bruch", life="Verbrauchte Standzeit (%)",
                y1="Hüllkurvenabstand zur ersten Lage", y2="Schwingungsstärke, Log-Verhältnis zur ersten Lage", l1="Hüllkurvenform, mit Lag", l2="Schwingungsstärke", last="letztes Zehntel")}[LANG]
num = lambda v, d=1: (f"{v:.{d}f}".replace(".", ",") if LANG == "de" else f"{v:.{d}f}")
plt.rcParams.update({"font.size": 11, "axes.edgecolor": "#c3c2b7", "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 12, "axes.titlecolor": INK, "axes.titlelocation": "left",
                     "figure.dpi": 200, "savefig.bbox": "tight", "font.family": "DejaVu Sans"})
w = S["conf"]["W1"]; order = ["VIB", "CUR", "RMSW", "TIU", "BL", "ZL", "LEN"]
fig, ax = plt.subplots(figsize=(8, 3.8))
for i, a in enumerate(order):
    v = w[a]; col = BLUE if a == "BL" else GREY
    ax.plot([v[1], v[2]], [i, i], color=col, linewidth=2.2, solid_capstyle="round")
    ax.plot(v[0], i, "o", color=col, markersize=8, markeredgecolor="white", markeredgewidth=1.3)
    ax.text(1.005, i, num(v[0], 2), va="center", ha="left", fontsize=10, color=INK)
ax.axvline(0.5, color="#c3c2b7", linewidth=1); ax.set_yticks(range(len(order))); ax.set_yticklabels(T["arms"], color=INK); ax.invert_yaxis()
ax.set_xlim(0.4, 1.0); ax.grid(axis="x", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: num(v))); ax.set_xlabel(T["x"]); ax.set_title(T["t1"])
fig.savefig(OUT / "iu2_fig1_auroc.png"); plt.close(fig)
d = sorted((c for c in C if c["tool"] == 9), key=lambda c: c["tiu"]); x = [100 * (1 - c["ctfn"]) for c in d]
fig, axs = plt.subplots(2, 1, figsize=(8, 4.8), sharex=True)
for ax, key, col, lab, yl in ((axs[0], "BL", BLUE, T["l1"], T["y1"]), (axs[1], "VIB", GREY, T["l2"], T["y2"])):
    ax.axvspan(90, 100, color="#f3efe6", linewidth=0); ax.plot(x, [c[key] for c in d], color=col, linewidth=1.6)
    ax.grid(axis="y", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
    ax.text(0.01, 0.95, yl, transform=ax.transAxes, color=INK, fontsize=10.5, va="top")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: num(v, 2)))
axs[0].text(95, axs[0].get_ylim()[1], T["last"], ha="center", va="bottom", fontsize=9, color=MUTED)
axs[0].set_title(T["t2"], pad=14); axs[1].set_xlabel(T["life"]); axs[1].set_xlim(0, 100)
fig.savefig(OUT / "iu2_fig2_tool9_trend.png"); plt.close(fig)
print("written", sorted(p.name for p in OUT.iterdir()))
