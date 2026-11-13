"""Figures for IU1 (Bosch CNC) from results/mcp_demo.json. Usage: python figures.py de|en"""
import json, sys, statistics as st, collections
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

LANG = sys.argv[1] if len(sys.argv) > 1 else "en"
R = Path("/spike/results"); OUT = Path("/spike/figures") / LANG; OUT.mkdir(parents=True, exist_ok=True)
D = json.loads((R / "mcp_demo.json").read_text())
GOOD, BAD, M2, M3, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#0b0b0b", "#6b6a65", "#e1e0d9"
T = {
    "en": dict(bad="Bad run (M02, OP10, Feb 2019)", good="Good run (M02, OP10, Feb 2019)", t="Time (s)", counts="Acceleration Y (counts)",
               f1="Acceleration over the whole run, min to max per 0.2 s", f2="Power per frequency band in the most unusual 2 s window",
               pw="Power (counts²)", f3="Distance to good history per run (M02 and M03)", share="Share of runs (%)",
               dist="Distance to nearest good-history window (0 = same shape)", gl="Good runs", bl="Bad runs",
               f4="Median distance to good history per half-year", med="Median distance", m2="Machine M02", m3="Machine M03",
               nb="bad runs", of="of"),
    "de": dict(bad="Schlechter Lauf (M02, OP10, Feb 2019)", good="Guter Lauf (M02, OP10, Feb 2019)", t="Zeit (s)", counts="Beschleunigung Y (Counts)",
               f1="Beschleunigung über den ganzen Lauf, min bis max je 0,2 s", f2="Leistung je Frequenzband im auffälligsten 2-Sekunden-Fenster",
               pw="Leistung (Counts²)", f3="Abstand zur Guthistorie je Lauf (M02 und M03)", share="Anteil der Läufe (%)",
               dist="Abstand zum nächsten Fenster der Guthistorie (0 = gleiche Form)", gl="Gute Läufe", bl="Schlechte Läufe",
               f4="Mittlerer Abstand zur Guthistorie je Halbjahr", med="Median des Abstands", m2="Maschine M02", m3="Maschine M03",
               nb="schlechte Läufe", of="von"),
}[LANG]
plt.rcParams.update({"font.size": 11, "axes.edgecolor": "#c3c2b7", "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 12, "axes.titlecolor": INK,
                     "axes.titlelocation": "left", "figure.dpi": 200, "savefig.bbox": "tight", "font.family": "DejaVu Sans"})


def num(v, d=0):
    s = f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if LANG == "de" else s


calls = D["calls"]
env = [c["result"] for c in calls if c["tool"] == "read_interval" and "min_max" in c["result"]]
desc = [c["result"] for c in calls if c["tool"] == "describe_interval"]

# 1 envelope, small multiples with one shared y scale
fig, axs = plt.subplots(2, 1, figsize=(8, 4.6), sharey=True)
for ax, e, col, lab in zip(axs, env, (BAD, GOOD), (T["bad"], T["good"])):
    mm = np.array(e["min_max"]); x = np.arange(len(mm)) * e["bucket_samples"] / e["sample_rate_hz"]
    ax.fill_between(x, mm[:, 0], mm[:, 1], color=col, linewidth=0)
    ax.set_ylim(-1500, 1500); ax.set_yticks([-1000, 0, 1000]); ax.grid(axis="y", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
    ax.set_xlim(0, 50); ax.text(0.995, 0.93, lab, transform=ax.transAxes, ha="right", va="top", color=INK, fontsize=10.5)
    ax.set_ylabel(T["counts"], fontsize=9.5)
axs[0].set_title(T["f1"]); axs[1].set_xlabel(T["t"])
fig.savefig(OUT / "fig1_run_envelope.png"); plt.close(fig)

# 2 band power
bands = list(desc[0]["band_power"].keys())
fig, ax = plt.subplots(figsize=(8, 3.6)); w = 0.36; x = np.arange(len(bands))
b1 = ax.bar(x - w / 2 - 0.02, [desc[0]["band_power"][b] for b in bands], w, color=BAD, label=T["bad"].split(" (")[0])
b2 = ax.bar(x + w / 2 + 0.02, [desc[1]["band_power"][b] for b in bands], w, color=GOOD, label=T["good"].split(" (")[0])
for bars in (b1, b2):
    for r in bars:
        if r.get_height() > 3000:
            ax.text(r.get_x() + r.get_width() / 2, r.get_height() + 500, num(r.get_height()), ha="center", va="bottom", fontsize=9, color=INK)
ax.set_xticks(x); ax.set_xticklabels([b.replace("-", "–").replace("62.5", "62").replace(".", ",") if LANG == "de" else b.replace("-", "–") for b in bands])
ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: num(v))); ax.grid(axis="y", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
ax.set_ylabel(T["pw"]); ax.set_title(T["f2"]); ax.legend(frameon=False, loc="upper left", fontsize=10); ax.set_ylim(0, 31000)
fig.savefig(OUT / "fig2_band_power.png"); plt.close(fig)

# 3 score histogram
sc = D["scores"]; edges = np.linspace(0, 0.24, 17)
fig, ax = plt.subplots(figsize=(8, 3.6)); w = (edges[1] - edges[0]) * 0.42
for lab, col, off, name in (("good", GOOD, -w / 2 - 0.0004, T["gl"]), ("bad", BAD, w / 2 + 0.0004, T["bl"])):
    v = np.clip([s["score"] for s in sc if s["label"] == lab], 0, 0.2399)
    h = np.histogram(v, edges)[0] / len(v) * 100
    ax.bar(edges[:-1] + (edges[1] - edges[0]) / 2 + off, h, w, color=col, label=f"{name} ({num(len(v))})")
ax.set_xlim(0, 0.24); ax.set_xticks(np.arange(0, 0.241, 0.03)); ax.set_xticklabels([num(v, 2) if v < 0.23 else "≥ " + num(0.225, 3) for v in np.arange(0, 0.241, 0.03)])
ax.grid(axis="y", color=GRID, linewidth=0.8); ax.set_axisbelow(True); ax.set_ylabel(T["share"]); ax.set_xlabel(T["dist"]); ax.set_title(T["f3"])
ax.legend(frameon=False, loc="upper right", fontsize=10)
fig.savefig(OUT / "fig3_score_distribution.png"); plt.close(fig)

# 4 trend per half-year
P = ["Feb_2019", "Aug_2019", "Feb_2020", "Aug_2020", "Feb_2021", "Aug_2021"]
g = collections.defaultdict(list)
for s in sc:
    g[(s["m"], s["period"])].append(s)
fig, ax = plt.subplots(figsize=(8, 3.8))
med = {(m, i): st.median(x["score"] for x in g[(m, p)]) for m in ("M02", "M03") for i, p in enumerate(P) if g.get((m, p))}
for m, col, ls, mk, name in (("M02", M2, "-", "o", T["m2"]), ("M03", M3, (0, (5, 3)), "s", T["m3"])):
    xs = [i for i in range(len(P)) if (m, i) in med]
    ys = [med[(m, i)] for i in xs]
    ax.plot(xs, ys, color=col, linestyle=ls, linewidth=2, marker=mk, markersize=7, markeredgecolor="white", markeredgewidth=1.5, label=name)
    other = "M03" if m == "M02" else "M02"
    for i, y in zip(xs, ys):
        v = g[(m, P[i])]; nb = sum(x["label"] == "bad" for x in v)
        above = y >= med.get((other, i), -1)
        ax.annotate(f"{nb} {T['of']} {len(v)}", (i, y), textcoords="offset points", xytext=((30 if (above and i == xs[0] and i > 0) else 0), 12 if above else -18), ha="center", fontsize=8.5, color=MUTED)
ax.set_xticks(range(len(P))); ax.set_xticklabels([p.replace("_", " ") for p in P]); ax.set_ylim(0, 0.095); ax.set_xlim(-0.35, 5.35)
ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: num(v, 2))); ax.grid(axis="y", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
ax.set_ylabel(T["med"]); ax.set_title(T["f4"]); ax.legend(frameon=False, loc="upper right", fontsize=10, title=f"({T['nb']} {T['of']} n)", title_fontsize=8.5)
fig.savefig(OUT / "fig4_trend_per_half_year.png"); plt.close(fig)
print("written", sorted(p.name for p in OUT.iterdir()))

# 5 AUROC by history and score (addendum A1), dot and 95% interval
ds = R / "drift_summary.json"
if ds.exists():
    S = json.loads(ds.read_text())
    L = {"en": dict(t="Bad against good runs: AUROC by history and score (95% interval)", x="AUROC (0.5 = chance)",
                    rows=["All half-years (frozen T2)", "Same half-year only", "Pairs inside machine and half-year", "Without M02 Feb 2019", "Earlier half-years only (13 bad runs)"],
                    arms=["Bounded lag", "Zero lag", "RMS", "Run length"]),
         "de": dict(t="Schlechte gegen gute Läufe: AUROC je Historie und Kennwert (95-%-Intervall)", x="AUROC (0,5 = Zufall)",
                    rows=["Alle Halbjahre (eingefrorenes T2)", "Nur dasselbe Halbjahr", "Paare innerhalb Maschine und Halbjahr", "Ohne M02 Feb 2019", "Nur frühere Halbjahre (13 schlechte Läufe)"],
                    arms=["Mit Lag", "Ohne Lag", "RMS", "Laufdauer"])}[LANG]
    src = [S["1_all_runs"]["all"], S["1_all_runs"]["same"], S["3_pooled_within"]["same"], S["2_without_M02_Feb2019"]["history_all"], S["1_all_runs"]["past"]]
    cols = [GOOD, "#8a8983", M2, BAD]; mks = ["o", "s", "^", "D"]
    fig, ax = plt.subplots(figsize=(8, 4.4))
    for i, e in enumerate(src):
        for j, a in enumerate(("BL", "ZL", "RMS", "DUR")):
            v = e[a]["auroc"]; y = i + (j - 1.5) * 0.17
            ax.plot([v[1], v[2]], [y, y], color=cols[j], linewidth=2, solid_capstyle="round")
            ax.plot(v[0], y, mks[j], color=cols[j], markersize=7, markeredgecolor="white", markeredgewidth=1.2, label=L["arms"][j] if i == 0 else None)
    ax.axvline(0.5, color="#c3c2b7", linewidth=1); ax.set_yticks(range(len(src))); ax.set_yticklabels(L["rows"], color=INK)
    ax.invert_yaxis(); ax.set_xlim(0.3, 1.02); ax.grid(axis="x", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: num(v, 1))); ax.set_xlabel(L["x"]); ax.set_title(L["t"])
    ax.legend(frameon=False, loc="upper left", fontsize=9.5, ncol=1)
    fig.savefig(OUT / "fig5_auroc_by_history.png"); plt.close(fig)
    print("fig5 written")
