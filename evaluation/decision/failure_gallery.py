#!/usr/bin/env python3
"""Failure gallery (PLAN.md): for M and FS, the 3 worst held-out query bearings on T2.

For each, the query window with its rank-1 (misleading) match and its best-ranked relevant
match, as raw waveforms and magnitude spectra. Reads evaluation-heldout.json and windows.npz.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path[:0] = [str(Path(__file__).resolve().parent)]
import bearings as b  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--figures", type=Path, required=True)
    a = p.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    a.figures.mkdir(parents=True, exist_ok=True)
    report = json.loads((a.out / "evaluation-heldout.json").read_text())
    w = np.load(a.out / "windows.npz")
    meta = {k: w[k] for k in w.files if k not in ("native", "decim")}
    native = w["native"]
    feats = dict(np.load(a.out / "features.npz"))
    key = {(str(meta["bearing"][i]), str(meta["setting"][i]), int(meta["measurement"][i]), int(meta["slot"][i])): i
           for i in range(len(native))}
    gi = np.where(meta["role"] == "heldout")[0]
    index = []
    for method in ("M", "FS"):
        task = report["results"][f"{b.FROZEN}/raw/{method}"]["tasks"]["T2/location"]
        per = task["p10"]["per_bearing"]
        worst = sorted(per, key=lambda k: (per[k], k))[:3]
        x = feats[f"{b.FROZEN}/{method}"]
        for bearing in worst:
            match = min((m for m in task["matches"] if m["query"][0] == bearing), key=lambda m: (m["p10"], m["query"]))
            qi = key[tuple(match["query"])]
            top1 = key[tuple(match["top"][0][:4])]
            eligible = b.task_mask("T2", meta, np.array([qi]), gi)[0]
            relevant = eligible & (meta["location"][gi] == meta["location"][qi])
            d = b.distances(x[[qi]], x[gi])[0]
            order = np.lexsort((np.arange(len(gi)), d))
            ranks = np.where(relevant[order])[0]
            useful = gi[order[ranks[0]]] if len(ranks) else None
            rows = [("query", qi, None), ("rank 1 (misleading)", top1, 1)]
            if useful is not None:
                rows.append((f"best relevant (rank {ranks[0] + 1})", useful, ranks[0] + 1))
            fig, axes = plt.subplots(len(rows), 2, figsize=(10, 2.2 * len(rows)), constrained_layout=True)
            for (label, i, _), (ax_t, ax_f) in zip(rows, axes):
                s = native[i]
                t = np.arange(len(s)) / b.RATE * 1e3
                ax_t.plot(t, s, lw=0.6, color="#3b5b92")
                ax_t.set_title(f"{label}: {meta['bearing'][i]} ({meta['location'][i]}, {meta['origin'][i]}) "
                               f"{meta['setting'][i]} #{meta['measurement'][i]}", fontsize=8)
                ax_t.set_xlabel("ms (amplitude in undocumented source units)", fontsize=7)
                spec = np.abs(np.fft.rfft((s - s.mean()) / (s.std() or 1)))
                fr = np.fft.rfftfreq(len(s), 1 / b.RATE) / 1e3
                ax_f.semilogy(fr[1:], spec[1:], lw=0.5, color="#3b5b92")  # DC omitted
                ax_f.set_xlabel("kHz", fontsize=7)
                ax_f.set_title("magnitude spectrum (z-scored window)", fontsize=8)
                for ax in (ax_t, ax_f):
                    ax.tick_params(labelsize=6)
            name = f"{method}-{bearing}.png"
            fig.suptitle(f"{method}, T2 (other settings), query bearing P@10 = {per[bearing]:.2f}", fontsize=9)
            fig.savefig(a.figures / name, dpi=110)
            plt.close(fig)
            index.append({"method": method, "bearing": bearing, "bearing_p10": per[bearing], "figure": name,
                          "query": match["query"], "rank1": match["top"][0],
                          "best_relevant_rank": int(ranks[0] + 1) if len(ranks) else None})
    (a.figures / "index.json").write_text(json.dumps(index, indent=1))
    print(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
