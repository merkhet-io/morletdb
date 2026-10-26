#!/usr/bin/env python3
"""Aggregate stage-1 results into summary.json, tables for RESULTS.md and figures."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import bench  # noqa: E402

DATASETS = ("paderborn", "ims", "cx")
LOSSY = ("quant-zstd", "quant-flac", "dwt-q", "harmonic", "tsa", "sz3", "zfp", "sperr")
# Fixed codec -> categorical slot (reference palette order; colour follows the codec, never rank).
PALETTE = {"quant-zstd": "#2a78d6", "quant-flac": "#eb6834", "dwt-q": "#1baf7a", "harmonic": "#eda100",
           "tsa": "#e87ba4", "sz3": "#008300", "zfp": "#6250d6", "sperr": "#e34948"}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def boot_ratio(num: dict, den: dict, resamples=2000, seed=7):
    """Ratio of summed bytes (den/num = reduction factor), bootstrap over units."""
    keys = sorted(set(num) & set(den))
    n = np.array([num[k] for k in keys], float)
    d = np.array([den[k] for k in keys], float)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(resamples):
        i = rng.integers(0, len(keys), len(keys))
        boots.append(d[i].sum() / n[i].sum())
    return {"factor": float(d.sum() / n.sum()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "units": len(keys)}


def verdict(c):
    lo = c["ci95"][0]
    return "material (>=2x)" if lo >= 2 else ("some gain" if lo > 1 else "no gain")


def per_unit_bytes(rows, codec, r=None, param="any", header=True):
    out = {}
    for row in rows:
        if row["codec"] != codec or "bytes" not in row:
            continue
        if r is not None and abs(row["r"] - r) > 1e-12:
            continue
        if param != "any" and row["param"] != param:
            continue
        out[row["unit"]] = out.get(row["unit"], 0) + row["bytes"] + (bench.HEADER_BYTES if header else 0)
    return out


def summarize(out: Path):
    op = json.loads((out / "operating.json").read_text())
    summary = {"datasets": {}}
    for ds in DATASETS:
        if not (out / f"rd-{ds}-heldout.jsonl").exists():
            continue
        rows = bench.load_rows(out, ds, "heldout")
        pq = json.loads((out / f"parquet-{ds}-heldout.json").read_text())
        best_pq = op[ds]["parquet"]
        sigma = json.loads((out / f"sigma-{ds}.json").read_text())["sigma_counts"]
        parquet = {u: v[best_pq] for u, v in pq.items()}
        samples = {u: v["samples"] for u, v in pq.items()}
        raw = {}
        for row in rows:
            if row["codec"] == "int-zstd":
                raw[row["unit"]] = raw.get(row["unit"], 0) + row["raw_bytes"]
        n_samples = sum(samples.values())
        d = {"sigma_counts": sigma, "samples": n_samples, "units": sorted(parquet), "parquet_config": best_pq,
             "bits_per_sample": {"native_raw": 8 * sum(raw.values()) / n_samples,
                                 "optimized_parquet": 8 * sum(parquet.values()) / n_samples,
                                 "float64_parquet_naive": 8 * sum(v["float64-naive/BSS-zstd3"] for v in pq.values()) / n_samples},
             "lossless": {}, "lossy": {}, "rd": {}}
        for codec in bench.LOSSLESS:
            b = per_unit_bytes(rows, codec)
            if len(b) != len(parquet):
                d["lossless"][codec] = "unsupported for some units"
                continue
            c = boot_ratio(b, parquet)
            d["lossless"][codec] = {"bits_per_sample": 8 * sum(b.values()) / n_samples, "vs_parquet": c,
                                    "vs_native_raw": sum(raw.values()) / sum(b.values()), "verdict": verdict(c)}
        for key, o in op[ds]["operating"].items():
            name, p = key.split("|", 1)
            if name in ("dwt-q", "harmonic") and str(op[ds]["params"].get(name)) != p.replace(".0", "") and \
                    str(op[ds]["params"].get(name)) != p:
                continue
            if o["r"] is None:
                d["lossy"][name] = {"operating_point": None}
                continue
            b = per_unit_bytes(rows, name, r=o["r"])
            viol = sum(1 for row in rows if row["codec"] == name and abs((row.get("r") or 0) - o["r"]) < 1e-12
                       and row.get("bound_ok") is False)
            c = boot_ratio(b, parquet)
            d["lossy"][name] = {"r": o["r"], "eps_counts": o["eps"], "bits_per_sample": 8 * sum(b.values()) / n_samples,
                                "vs_parquet": c, "verdict": verdict(c), "bound_violations": viol}
            if ds == "cx":
                sel = [row for row in rows if row["codec"] == name and abs((row.get("r") or 0) - o["r"]) < 1e-12 and "bytes" in row]
                to, td = sum(r_["trig_orig"] for r_ in sel), sum(r_["trig_dec"] for r_ in sel)
                d["lossy"][name]["heldout_trigger_recall"] = sum(r_["trig_matched_orig"] for r_ in sel) / to if to else None
                d["lossy"][name]["heldout_trigger_precision"] = sum(r_["trig_matched_dec"] for r_ in sel) / td if td else None
        # Rate-distortion curves (held-out): bits/sample and the 95th percentile of indicator change per r.
        for name in LOSSY:
            pts = []
            for r in bench.R_GRID:
                sel = [row for row in rows if row["codec"] == name and row.get("r") is not None
                       and abs(row["r"] - r) < 1e-12 and "bytes" in row]
                if not sel:
                    continue
                pt = {"r": r, "bits_per_sample": 8 * sum(s["bytes"] + bench.HEADER_BYTES for s in sel) / sum(s["n"] for s in sel)}
                if ds != "cx":
                    pt["p95_abs_dkurt_rel"] = float(np.percentile(np.abs([s["d_kurt_rel"] for s in sel]), 95))
                    pt["p95_abs_denv_rel"] = float(np.percentile(np.abs([s["d_env_rel"] for s in sel]), 95))
                    pt["median_env_lsd_db"] = float(np.median([s["env_lsd_db"] for s in sel]))
                else:
                    to = sum(s["trig_orig"] for s in sel)
                    pt["trigger_recall"] = sum(s["trig_matched_orig"] for s in sel) / to if to else None
                pts.append(pt)
            if pts:
                d["rd"][name] = pts
        # H4 at the common eps (quant-flac's operating point).
        qf = op[ds]["operating"].get("quant-flac|None", {})
        if qf.get("r") is not None:
            r = qf["r"]
            simple = {u: min(per_unit_bytes(rows, "quant-zstd", r).get(u, np.inf), per_unit_bytes(rows, "quant-flac", r).get(u, np.inf))
                      for u in parquet}
            sci = {u: min(per_unit_bytes(rows, c, r).get(u, np.inf) for c in ("sz3", "zfp", "sperr")) for u in parquet}
            h4 = {"r": r, "eps_counts": qf["eps"]}
            for name in ("dwt-q", "harmonic", "tsa"):
                b = per_unit_bytes(rows, name, r)
                if len(b) != len(parquet):
                    continue
                vs_simple = boot_ratio(simple, b)  # factor < 1 means the decomposition codec is smaller
                vs_sci = boot_ratio(sci, b)
                h4[name] = {"bytes_over_best_simple": vs_simple, "bytes_over_best_scientific": vs_sci,
                            "wins": vs_simple["ci95"][1] < 1.0 and vs_sci["ci95"][1] < 1.0}
            d["h4"] = h4
        summary["datasets"][ds] = d
    for name in ("h2-paderborn", "h2-ims", "h3", "h6", "costs"):
        if (out / f"{name}.json").exists():
            summary[name] = json.loads((out / f"{name}.json").read_text())
    summary["operating"] = op
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    return summary


def figures(summary, out: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 8, "axes.edgecolor": INK2, "axes.labelcolor": INK2, "xtick.color": INK2,
                         "ytick.color": INK2, "text.color": INK, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE})
    dss = [d for d in DATASETS if d in summary["datasets"]]
    for kind in ("rate", "diag"):
        fig, axes = plt.subplots(1, len(dss), figsize=(4.2 * len(dss), 3.4), constrained_layout=True)
        axes = np.atleast_1d(axes)
        for ax, ds in zip(axes, dss):
            d = summary["datasets"][ds]
            for name, pts in d["rd"].items():
                if kind == "rate":
                    xs = [p["r"] for p in pts]
                    ys = [p["bits_per_sample"] for p in pts]
                    ax.plot(xs, ys, "-o", color=PALETTE[name], lw=1.5, ms=4, label=name)
                else:
                    key = "trigger_recall" if ds == "cx" else "p95_abs_dkurt_rel"
                    keep = [p for p in pts if ds == "cx" or p[key] > 0]  # exact (zero-change) points are off a log axis
                    xs = [p["bits_per_sample"] for p in keep]
                    ys = [p[key] for p in keep]
                    ax.plot(xs, ys, "-o", color=PALETTE[name], lw=1.5, ms=4, label=name)
            if kind == "rate":
                ax.axhline(d["bits_per_sample"]["optimized_parquet"], color=INK2, lw=1, ls="--")
                ax.text(ax.get_xlim()[0] if False else min(bench.R_GRID), d["bits_per_sample"]["optimized_parquet"],
                        " lossless Parquet", va="bottom", color=INK2, fontsize=7)
                ax.set_xscale("log", base=2)
                ax.set_xlabel("error bound ε / σ (log2)")
                ax.set_ylabel("bits per sample (incl. 64 B block headers)")
            else:
                if ds == "cx":
                    ax.axhline(0.98, color=INK2, lw=1, ls="--")
                    ax.set_ylabel("STA/LTA trigger recall vs original")
                else:
                    ax.axhline(0.05, color=INK2, lw=1, ls="--")
                    ax.set_yscale("log")
                    ax.set_ylabel("95th pct |relative change| of D-KURT")
                ax.axvline(d["bits_per_sample"]["optimized_parquet"], color=INK2, lw=1, ls=":")
                ax.set_xlabel("bits per sample")
            ax.set_title(f"{ds} (held-out)", fontsize=9, color=INK)
            ax.grid(True, color=GRID, lw=0.6)
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
        axes[0].legend(frameon=False, fontsize=7, ncol=2)
        fig.savefig(out / f"fig-{kind}.png", dpi=140)
        plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    s = summarize(a.out)
    figures(s, a.out)
    for ds, d in s["datasets"].items():
        print(ds, "bits/sample", {k: round(v, 2) for k, v in d["bits_per_sample"].items()})
        for c, v in d["lossless"].items():
            print("  lossless", c, v if isinstance(v, str) else (round(v["bits_per_sample"], 2), round(v["vs_parquet"]["factor"], 3), [round(x, 3) for x in v["vs_parquet"]["ci95"]], v["verdict"]))
        for c, v in d["lossy"].items():
            print("  lossy", c, v if v.get("operating_point", 1) is None else (v["r"], round(v["eps_counts"], 2), round(v["bits_per_sample"], 2),
                  round(v["vs_parquet"]["factor"], 2), [round(x, 2) for x in v["vs_parquet"]["ci95"]], v["verdict"], "viol", v["bound_violations"],
                  v.get("heldout_trigger_recall"), v.get("heldout_trigger_precision")))
        print("  h4", json.dumps(d.get("h4"), default=float)[:600])


if __name__ == "__main__":
    main()
