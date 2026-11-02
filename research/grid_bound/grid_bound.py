"""B1: the query-computable bound on what the 13-lag probe grid can lose, against the loss observed on
development data (morlet_r2, E1's 100 development queries, their exact top ten). No held-out data is read.

For a stored window g and lags l (all 61) and lambda (the 13 grid lags), c(l) = <s_l, g> / n with s_l the
zero-padded shifted query. Cauchy-Schwarz: |c(l) - c(lambda)| <= ||s_l - s_lambda|| * ||g|| / n.
  item bound   b(l*)  = min over grid lambda of ||s_l* - s_lambda|| * G / n   (l* = the item's best lag)
  query bound  D(q)   = max over l of b(l)                                     (needs no gallery)
  observed     loss   = c(l*) - max over grid lambda of c(lambda)
G is the largest stored-envelope norm in the gallery (sqrt(n) up to rounding, since decode re-z-scores).

  python -I research/grid_bound/grid_bound.py <db> <out.json>
"""
import json
import sys
from pathlib import Path

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation" / "strong_exact"))
import e3  # noqa: E402

P, e1 = e3.P, e3.e1
G61, G13 = np.array(P.GRIDS["G61"]), np.array(P.GRIDS["G13"])


def gap_norms(q):
    """||s_l - s_lambda|| for every lag l and its best grid lag lambda (smallest norm among grid lags)."""
    S = P.probe_matrix(q, G61)
    col = {int(l): i for i, l in enumerate(G61)}
    out = {}
    for l in G61:
        out[int(l)] = min(float(np.linalg.norm(S[:, col[int(l)]] - S[:, col[int(g)]])) for g in G13
                          if abs(int(g) - int(l)) <= 2)
    return out


def main(db, out_path):
    e3.DB = db
    queries = [(q["recording_id"], int(q["start"]), q["type"]) for q in json.loads(e3.QUERIES.read_text())["queries"]]
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as conn:
        gal, _ = e3.materialize(conn)
        e3.GAL = gal
        src_attr = {}
        for (r, rate, ch, unit) in conn.execute(f"SELECT recording_id, sample_rate_hz, channel, unit FROM {e3.COLL}"):
            key = (rate, ch, unit)
            src_attr[r] = gal["attrs"].index(key) if key in gal["attrs"] else -1
        gal["src_attr"] = src_attr
        n = e3.N_ENV
        G = float(gal["gnorm"].max())
        index = {(gal["names"][r], int(s)): i for i, (r, s) in enumerate(zip(gal["rid"], gal["start"]))}
        per = []
        for rid, s, typ in queries:
            q = e1.query_env(conn, rid, s)
            top, _ = e3.search64(q, (rid, s), {})
            gaps = gap_norms(q)
            S = P.probe_matrix(q, G61)
            for r, st, d in top:
                g = gal["G64"][index[(r, st)]]
                c = g @ S / n                                   # c(l) for all 61 lags
                lstar = int(G61[int(np.argmax(c))])
                cgrid = float(max(c[i] for i, l in enumerate(G61) if l in set(G13.tolist())))
                per.append({"query": f"{rid}:{s}", "type": typ, "lstar": lstar, "c_best": float(c.max()),
                            "c_grid": cgrid, "loss": float(c.max()) - cgrid,
                            "item_bound": gaps[lstar] * G / n, "query_bound": max(gaps.values()) * G / n,
                            "q_norm": float(np.linalg.norm(q))})
    loss = np.array([p["loss"] for p in per])
    ib = np.array([p["item_bound"] for p in per])
    qb = np.array(sorted({p["query"]: p["query_bound"] for p in per}.values()))
    kept = np.array([p["c_grid"] / p["c_best"] for p in per])
    pct = lambda a: {k: float(np.percentile(a, v)) for k, v in (("min", 0), ("p5", 5), ("p50", 50), ("p95", 95), ("max", 100))}
    on_grid = loss[ib == 0]
    off = ib > 0
    summary = {
        "database": db, "queries": len(queries), "items": len(per), "n": n, "G_max_norm": G, "sqrt_n": n ** 0.5,
        "observed_loss": pct(loss), "kept_fraction": pct(kept),
        "item_bound": pct(ib[off]), "query_bound": pct(qb),
        "items_on_grid": int((ib == 0).sum()), "max_loss_on_grid": float(on_grid.max()) if len(on_grid) else None,
        "bound_holds_every_item": bool(np.all(loss <= ib + 1e-12)),
        "item_bound_over_loss_off_grid": pct(ib[off] / np.maximum(loss[off], 1e-15)),
        "items_with_zero_loss_off_grid": int(np.sum(loss[off] <= 1e-15)),
    }
    Path(out_path).write_text(json.dumps({"summary": summary, "items": per}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
