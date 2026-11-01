"""Why a 13-lag grid (spacing 5) suffices and a 7-lag grid (spacing 10) loses hits: a post-hoc analysis on
development data only (R2's database; BL1 per-query results). No held-out data is read.

1. Envelope smoothness: biased autocorrelation a(d) = (1/n) sum_t g[t+d] g[t] of stored, re-z-scored float16
   envelopes for a random sample of windows. A top-ten item whose best lag lies d lags from the nearest grid
   point keeps roughly a(d) of its matched correlation in that probe.
2. Where best lags fall: distance of each exhaustive top-ten item's best lag to the nearest grid point,
   from BL1's recorded best lags (lstar), for both grids.

Usage: python -I grid_spacing.py <dbname> <bl1.json> <out.json>
"""
import json
import sys
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "evaluation" / "bounded_lag"))
import probes as P  # noqa: E402

SAMPLE, SEED, MAXD = 3000, 20261101, 10


def autocorr(g, d):
    return float(np.dot(g[d:], g[: len(g) - d]) / len(g))


def main(db, bl1_path, out_path):
    with psycopg.connect(dbname=db, host="localhost", port=28817) as conn:
        conn.execute("SELECT setseed(%s)", (0.2026,))
        rows = conn.execute(
            "SELECT envelope FROM public.r2_envelopes WHERE window_size = 4096 ORDER BY random() LIMIT %s",
            (SAMPLE,)).fetchall()
    G = [P.decode(e).astype(np.float64) for (e,) in rows]
    G = [(g - g.mean()) / g.std() for g in G]
    A = np.array([[autocorr(g, d) for d in range(MAXD + 1)] for g in G])
    smooth = {d: {"mean": float(A[:, d].mean()), "p5": float(np.percentile(A[:, d], 5)),
                  "p50": float(np.median(A[:, d]))} for d in range(MAXD + 1)}

    bl1 = json.load(open(bl1_path))
    lstars = [l for rec in bl1["per_query"].values() for l in rec.get("lstar", [])]
    dist = {}
    for name in ("G13", "G7"):
        grid = np.array(P.GRIDS[name])
        dd = [int(np.min(np.abs(grid - l))) for l in lstars]
        dist[name] = {str(k): dd.count(k) for k in sorted(set(dd))}
    out = {"sample": SAMPLE, "autocorrelation": smooth, "items": len(lstars), "distance_to_grid": dist}
    Path(out_path).write_text(json.dumps(out, indent=1))
    for d in (1, 2, 3, 5, 10):
        print(d, {k: round(v, 3) for k, v in smooth[d].items()})
    print(dist)


if __name__ == "__main__":
    main(*sys.argv[1:4])
