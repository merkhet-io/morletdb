#!/usr/bin/env python3
"""J3 step 1: J1's decision states (system1_j1/results/states.json) as compact text for a 512-token model.

  compact.py K OUT_JSON      K = deviations per channel (3, 2 or 1; PROTOCOL.md, "Compact state")

Deterministic and outcome-blind: per channel the summary deviation and its rank, then the K values with the largest
|relative deviation| (ties in J1's value order). Not sent, as in J1: case, run, time in use, VB, labels.
"""
import json
import sys
from pathlib import Path

J1 = Path(__file__).resolve().parent.parent / "system1_j1/results/states.json"
VALUES = ("duration_s", "mean", "std", "rms", "p75", "peak_abs", "crest", "kurtosis")
NAMES = {"duration_s": "duration", "mean": "mean", "std": "std", "rms": "rms", "p75": "75th percentile",
         "peak_abs": "peak", "crest": "crest factor", "kurtosis": "kurtosis"}


def pct(x):
    return f"{100 * x:+.0f}%"


def compact(state, k):
    s = state["setting"]
    chans = state["channels"]
    n0 = next(iter(chans.values()))["history_runs"]
    lines = [f"One cut on a milling machine ({s['material']}, depth of cut {s['depth_of_cut_mm']:g} mm, feed "
             f"{s['feed_mm_per_rev']:g} mm/rev), compared with {n0} earlier cuts of the same setting made with a sharp "
             f"insert. Deviation is (value - earlier median) / |earlier median|; rank is the value's position among "
             f"the earlier cuts (0 lowest, 1 highest)."]
    for c in chans.values():
        vals = [(v, c["values"][v]) for v in VALUES if c["values"].get(v) and c["values"][v].get("relative_deviation") is not None]
        top = sorted(vals, key=lambda t: -abs(t[1]["relative_deviation"]))[:k]
        extra = f" ({c['history_runs']} earlier cuts)" if c["history_runs"] != n0 else ""
        parts = ", ".join(f"{NAMES[v]} {pct(d['relative_deviation'])} (rank {d['history_quantile']:.2f})" for v, d in top)
        lines.append(f"- {c['sensor']}{extra}: overall deviation {c['summary_score']:.2f} (rank "
                     f"{c['summary_quantile']:.2f}); largest: {parts}")
    return "\n".join(lines)


def main():
    k, out = int(sys.argv[1]), Path(sys.argv[2])
    assert k in (1, 2, 3)
    rows = json.loads(J1.read_text())["rows"]
    out.write_text(json.dumps({"k": k, "rows": [{key: r[key] for key in ("case", "run", "vb", "time_in_use_min")}
                                                 | {"text": compact(r["state"], k)} for r in rows]}, indent=1) + "\n")
    print(f"wrote {out}: {len(rows)} states, k={k}, max {max(len(compact(r['state'], k)) for r in rows)} chars")


if __name__ == "__main__":
    main()
