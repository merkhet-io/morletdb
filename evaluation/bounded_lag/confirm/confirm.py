#!/usr/bin/env python3
"""BL1-C (../CONFIRMATION.md): A, X8 (custom plans) and the frozen P-G13-50 on the confirmation slice, plus
descriptive PX-G13-50 and equal-budget zero-lag EZ. Uses ../bl1.py's functions unchanged.

  MORLET_DSN="host=localhost port=28817 dbname=morlet_bl1c" \
  .venv-seismic/bin/python -I evaluation/bounded_lag/confirm/confirm.py evaluation/bounded_lag/confirm/results
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("MORLET_DSN", "host=localhost port=28817 dbname=morlet_bl1c")
import bl1  # noqa: E402
import probes as P  # noqa: E402

SEED, GRID, K = 2021, "G13", 50


def pick_queries(conn):
    """R2's rule: 50 uniform random day-1 windows and the 50 highest-RMS non-overlapping day-1 windows."""
    rows = conn.execute("""SELECT e.recording_id, e.start_sample FROM public.r2_envelopes e JOIN public.r2 r USING (recording_id)
                           WHERE (r.metadata->>'day')::int = 1 AND e.window_size = %s
                           ORDER BY e.recording_id, e.start_sample""", (P.WINDOW,)).fetchall()
    rng = np.random.default_rng(SEED)
    rand = [rows[i] for i in rng.choice(len(rows), 50, replace=False)]
    rms = []
    for rid, s in rows:
        x = np.asarray(conn.execute("SELECT samples FROM morlet_read_interval(%s, %s, %s, %s)",
                                    (bl1.COLL, rid, s, s + P.WINDOW)).fetchone()[0])
        rms.append(float(np.sqrt(((x - x.mean()) ** 2).mean())))
    energetic, taken = [], [tuple(r) for r in rand]
    for i in np.argsort(rms)[::-1]:
        rid, s = rows[i]
        if any(r == rid and abs(s - t) < P.WINDOW for r, t in [*taken, *energetic]):
            continue
        energetic.append((rid, s))
        if len(energetic) == 50:
            break
    return [(r, int(s), "random") for r, s in rand] + [(r, int(s), "energetic") for r, s in energetic]


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(bl1.DSN, autocommit=True) as conn:
        queries = pick_queries(conn)
        json.dump({"rule": "R2 pick_queries, seed 2021", "queries": [{"recording_id": r, "start": s, "type": t}
                                                                        for r, s, t in queries]},
                  open(out / "queries.json", "w"), indent=0)
        ids, G, keys = bl1.gallery(conn)
        conn.execute("SET hnsw.iterative_scan = relaxed_order")
        rid0, s0, _ = queries[0]
        n_idx, plan = bl1.plan_index_scans(conn, rid0, s0, GRID, K)
        res = {"gallery": len(ids), "plan_P": {"index_scans": n_idx, "exercised": n_idx == len(P.GRIDS[GRID]), "plan": plan},
               "per_query": {}}
        conn.execute("SET plan_cache_mode = force_custom_plan")
        conn.execute("SET max_parallel_workers_per_gather = 8")
        q = bl1.query_envelope(conn, rid0, s0)
        res["plan_X8"] = "\n".join(r[0] for r in conn.execute("EXPLAIN (ANALYZE, TIMING OFF) " + bl1.X_SQL,
                                   (q.tolist(), P.MAX_LAG, bl1.Jsonb({"tiers": [bl1.TIER]}), rid0, s0 + P.WINDOW, s0, bl1.X_LIMIT)).fetchall())
        print("plans", n_idx, [ln.strip() for ln in res["plan_X8"].splitlines() if "Workers" in ln], flush=True)
        arm_p = f"P-{GRID}-{K}"
        for arm in ("A", "X8", arm_p):
            for timed_pass in (False, True):
                for qi, (rid, s, _) in enumerate(queries):
                    rec = res["per_query"].setdefault(qi, {"ms": {}, "cands": {}, "cand_sets": {}})
                    if arm == "A":
                        top, ms = bl1.timed(lambda: bl1.arm_a(conn, rid, s))
                    elif arm == "X8":
                        (top, flag), ms = bl1.timed(lambda: bl1.arm_x(conn, rid, s))
                        rec.setdefault("x_exhausted", {})["X8"] = flag
                    else:
                        (top, cand), ms = bl1.timed(lambda: bl1.arm_p(conn, rid, s, GRID, K))
                        rec["cands"][arm], rec["cand_sets"][arm] = len(cand), cand
                    rec[arm], rec["ms"][arm] = top, ms
            print("timed", arm, flush=True)
        cols = [P.GRIDS["G61"].index(l) for l in P.GRIDS[GRID]]
        index = {k: i for i, k in enumerate(ids)}
        for qi, (rid, s, _) in enumerate(queries):
            rec = res["per_query"][qi]
            qv = bl1.query_envelope(conn, rid, s).astype(np.float32)
            with np.errstate(all="ignore"):
                S61 = G @ P.probe_matrix(qv, P.GRIDS["G61"])
            rec["lstar"] = [int(P.GRIDS["G61"][int(np.argmax(S61[index[tuple(a)]]))]) for a in rec["A"]]
            seen, cand = set(), []
            for c in cols:
                for i in P.topk(S61[:, c], K, keys):
                    if i not in seen:
                        seen.add(i)
                        cand.append(ids[i])
            cos0 = S61[:, 30] / (np.linalg.norm(G, axis=1) * np.linalg.norm(qv) + 1e-30)
            ez = [ids[i] for i in P.topk(cos0, len(cand), keys)]
            for arm, c in ((f"PX-{GRID}-{K}", cand), (f"EZ-{GRID}-{K}", ez)):
                rec[arm], rec["cands"][arm], rec["cand_sets"][arm] = bl1.rerank(conn, rid, s, c), len(c), c
    summarize(res, queries, out)


def summarize(res, queries, out):
    per = res["per_query"]
    types = np.array([t for _, _, t in queries])
    summ = {}
    for arm in ("A", "X8", f"P-{GRID}-{K}", f"PX-{GRID}-{K}", f"EZ-{GRID}-{K}"):
        rec = [len(set(map(tuple, per[q][arm])) & set(map(tuple, per[q]["A"]))) / len(per[q]["A"]) for q in per]
        s = {"recall_mean": float(np.mean(rec)), "recall_min": float(np.min(rec)), "per_query_recall": rec,
             "recall_by_type": {t: float(np.mean(np.array(rec)[types == t])) for t in ("random", "energetic")}}
        bylag = {}
        for b, (lo, hi) in {"0-5": (0, 5), "6-15": (6, 15), "16-30": (16, 30)}.items():
            items = [(a in set(map(tuple, per[q][arm]))) for q in per for a, l in zip(map(tuple, per[q]["A"]), per[q]["lstar"])
                     if lo <= abs(l) <= hi]
            bylag[b] = {"recall": float(np.mean(items)) if items else None, "items": len(items)}
        s["recall_by_lstar"] = bylag
        if arm in per[0]["cands"]:
            c = [per[q]["cands"][arm] for q in per]
            s["unique_candidates"] = {"mean": float(np.mean(c)), "p95": float(np.percentile(c, 95))}
            s["coverage_mean"] = float(np.mean([len(set(map(tuple, per[q]["cand_sets"][arm])) & set(map(tuple, per[q]["A"])))
                                                / len(per[q]["A"]) for q in per]))
        if arm in per[0]["ms"]:
            ms = np.array([per[q]["ms"][arm] for q in per])
            s["latency_ms"] = {"p50": float(np.percentile(ms, 50)), "p95": float(np.percentile(ms, 95))}
        summ[arm] = s
    load = json.loads((out / "load.json").read_text())
    added = load["bytes"]["halfvec_table"] + load["bytes"]["ip_hnsw"]
    p = summ[f"P-{GRID}-{K}"]
    crit = {"plan_exercised": res["plan_P"]["exercised"], "recall_ge_0.95": p["recall_mean"] >= 0.95,
            "p95_le_A_over_3": p["latency_ms"]["p95"] <= summ["A"]["latency_ms"]["p95"] / 3,
            "p95_below_X8": p["latency_ms"]["p95"] < summ["X8"]["latency_ms"]["p95"],
            "storage_le_1.5x_raw": added <= 1.5 * load["bytes"]["raw_chunks"]}
    res.update({"summary": summ, "criteria": crit, "added_bytes": added,
                "verdict": "CONFIRMED" if all(crit.values()) else "NOT CONFIRMED",
                "x8_exhausted_queries": int(sum(per[q].get("x_exhausted", {}).get("X8", False) for q in per))})
    for q in per:
        per[q].pop("cand_sets", None)
    json.dump(res, open(out / "confirm.json", "w"), indent=1)
    print(json.dumps({a: {k: v for k, v in s.items() if k in ("recall_mean", "recall_min", "unique_candidates", "latency_ms")}
                      for a, s in summ.items()}, indent=1))
    print(res["verdict"], crit)


if __name__ == "__main__":
    main()
