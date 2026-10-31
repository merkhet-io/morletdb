#!/usr/bin/env python3
"""BL1 Stage A (PROTOCOL.md) on R2's database: A, X2/X8, P-G-K (HNSW inner-product probes), PX-G-K (exact
probes, offline), EZ-match (equal-budget exact zero-lag), all re-ranked by the exact scorer in the database.

  MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/bounded_lag/bl1.py setup evaluation/bounded_lag/results
  ... bl1.py run evaluation/bounded_lag/results
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probes as P  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_r2")
COLL, TIER, LAG_FRACTION, X_LIMIT = "public.r2", 3, 0.075, 200
CONFIGS = [(g, k) for g in ("G7", "G13", "G61") for k in (50, 100)]
P_CONFIGS = [(g, k) for g in ("G7", "G13") for k in (50, 100)]       # HNSW probes; G61 is the exact-probe check


def setup(conn, out):
    """The new inner-product HNSW index on R2's halfvec side table (same m, ef_construction as R2)."""
    conn.execute("SET maintenance_work_mem = '2GB'")
    t = time.perf_counter()
    conn.execute("CREATE INDEX IF NOT EXISTS r2_hv_ip ON public.r2_hv USING hnsw (v halfvec_ip_ops) WITH (m = 16, ef_construction = 64)")
    build = time.perf_counter() - t
    size = lambda q: conn.execute(q).fetchone()[0]
    res = {"ip_index_build_s": build,
           "bytes": {"raw_chunks": size("SELECT pg_total_relation_size('public.r2_chunks')"),
                     "envelopes": size("SELECT pg_total_relation_size('public.r2_envelopes')"),
                     "halfvec_table": size("SELECT pg_table_size('public.r2_hv')"),
                     "cosine_hnsw": size("SELECT pg_relation_size('public.r2_hv_hnsw')"),
                     "ip_hnsw": size("SELECT pg_relation_size('public.r2_hv_ip')")}}
    json.dump(res, open(out / "setup.json", "w"), indent=1)
    print(json.dumps(res))


def gallery(conn):
    rows = conn.execute("""SELECT e.recording_id, e.start_sample, e.envelope FROM public.r2_envelopes e
                           JOIN public.r2_hv h USING (recording_id, start_sample)
                           WHERE e.window_size = 4096 AND h.tiers @> ARRAY[%s]::int[]
                           ORDER BY e.recording_id, e.start_sample""", (TIER,)).fetchall()
    ids = [(r, int(s)) for r, s, _ in rows]
    G = np.stack([P.decode(e) for _, _, e in rows]).astype(np.float16).astype(np.float32)   # = side-table halfvec
    return ids, G, np.arange(len(ids))                                                     # keys follow (rid, start) order


def query_envelope(conn, rid, s):
    return np.asarray(conn.execute("SELECT morlet_envelope(samples) FROM morlet_read_interval(%s, %s, %s, %s)",
                                   (COLL, rid, s, s + P.WINDOW)).fetchone()[0], dtype=np.float64)


def rerank(conn, rid, s, cand):
    rows = conn.execute("""WITH q AS (SELECT morlet_envelope(samples) AS env FROM morlet_read_interval(%s, %s, %s, %s)),
                                c AS (SELECT * FROM unnest(%s::text[], %s::bigint[]) AS c(recording_id, start_sample))
                           SELECT e.recording_id, e.start_sample, morlet_envelope_distance(q.env, e.envelope, %s) AS d
                           FROM c JOIN public.r2_envelopes e ON e.recording_id = c.recording_id
                                AND e.start_sample = c.start_sample AND e.window_size = %s, q
                           ORDER BY d, e.recording_id, e.start_sample""",
                        (COLL, rid, s, s + P.WINDOW, [c[0] for c in cand], [c[1] for c in cand], P.MAX_LAG, P.WINDOW)).fetchall()
    return P.suppress(rows, rid, s)


def arm_a(conn, rid, s):
    return [tuple(r) for r in conn.execute(
        "SELECT recording_id, start_sample FROM morlet_search_envelope_interval(%s, %s, %s, %s, %s, 10, %s, true, true, 1000000)",
        (COLL, rid, s, s + P.WINDOW, Jsonb({"tiers": [TIER]}), LAG_FRACTION)).fetchall()]


X_SQL = """SELECT e.recording_id, e.start_sample, morlet_envelope_distance(%s::float8[], e.envelope, %s) AS d
           FROM public.r2_envelopes e JOIN public.r2 r ON r.recording_id = e.recording_id
           WHERE e.window_size = 4096 AND r.sample_rate_hz = 100 AND r.channel = 'HHZ' AND r.unit = 'counts'
             AND r.metadata @> %s AND NOT (e.recording_id = %s AND e.start_sample < %s AND e.start_sample + 4096 > %s)
           ORDER BY d, e.recording_id, e.start_sample LIMIT %s"""


def arm_x(conn, rid, s):
    q = query_envelope(conn, rid, s)
    rows = conn.execute(X_SQL, (q.tolist(), P.MAX_LAG, Jsonb({"tiers": [TIER]}), rid, s + P.WINDOW, s, X_LIMIT)).fetchall()
    top = P.suppress(rows, rid, s)
    return top, len(top) < P.TOP and len(rows) == X_LIMIT


def vec(v):
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"


def probe_sql(n_probes, k):
    branch = f"(SELECT recording_id, start_sample FROM public.r2_hv WHERE tiers @> ARRAY[{TIER}]::int[] ORDER BY v <#> %s::halfvec LIMIT {k})"
    return " UNION ALL ".join([branch] * n_probes)


def arm_p(conn, rid, s, grid, k):
    q = query_envelope(conn, rid, s)
    probes = [vec(P.shifted(q, l)) for l in P.GRIDS[grid]]
    conn.execute(f"SET hnsw.ef_search = {max(k, 100)}")
    rows = conn.execute(probe_sql(len(probes), k), probes).fetchall()
    cand = list(dict.fromkeys((r, int(st)) for r, st in rows))
    return rerank(conn, rid, s, cand), cand


def plan_index_scans(conn, rid, s, grid, k):
    q = query_envelope(conn, rid, s)
    probes = [vec(P.shifted(q, l)) for l in P.GRIDS[grid]]
    conn.execute(f"SET hnsw.ef_search = {max(k, 100)}")
    plan = "\n".join(r[0] for r in conn.execute("EXPLAIN " + probe_sql(len(probes), k), probes).fetchall())
    return plan.count("Index Scan using r2_hv_ip"), plan


def plan_x(conn, rid, s):
    q = query_envelope(conn, rid, s)
    return "\n".join(r[0] for r in conn.execute("EXPLAIN " + X_SQL, (q.tolist(), P.MAX_LAG, Jsonb({"tiers": [TIER]}), rid,
                                                                     s + P.WINDOW, s, X_LIMIT)).fetchall())


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, 1e3 * (time.perf_counter() - t)


def run(conn, out):
    queries = [(q["recording_id"], q["start"], q["type"]) for q in json.loads((HERE / "dev_queries.json").read_text())["queries"]]
    ids, G, keys = gallery(conn)
    index = {k: i for i, k in enumerate(ids)}
    conn.execute("SET hnsw.iterative_scan = relaxed_order")
    res = {"gallery": len(ids), "plans": {}, "per_query": {}}

    # plans (one query per arm) before any timing
    rid0, s0, _ = queries[0]
    for g, k in P_CONFIGS:
        n_idx, plan = plan_index_scans(conn, rid0, s0, g, k)
        res["plans"][f"P-{g}-{k}"] = {"index_scans": n_idx, "probes": len(P.GRIDS[g]), "exercised": n_idx == len(P.GRIDS[g]), "plan": plan}
    for w in (2, 8):
        conn.execute(f"SET max_parallel_workers_per_gather = {w}")
        plan = plan_x(conn, rid0, s0)
        res["plans"][f"X{w}"] = {"parallel_seq_scan": "Parallel Seq Scan on r2_envelopes" in plan,
                                 "workers_planned": [ln.strip() for ln in plan.splitlines() if "Workers Planned" in ln], "plan": plan}
    conn.execute("RESET max_parallel_workers_per_gather")
    print("plans", {a: (p.get("exercised"), p.get("parallel_seq_scan"), p.get("workers_planned")) for a, p in res["plans"].items()}, flush=True)

    # database-vs-client check of the scorer on real windows
    q = query_envelope(conn, rid0, s0)
    for i in (0, 1000, 200000):
        r, st = ids[i]
        d_db = conn.execute("SELECT morlet_envelope_distance(%s::float8[], envelope, 30) FROM public.r2_envelopes "
                            "WHERE recording_id = %s AND start_sample = %s AND window_size = 4096", (q.tolist(), r, st)).fetchone()[0]
        d_cl = P.distance(q, P.zscore(G[i].astype(np.float64)))
        assert abs(d_db - d_cl) < 2e-3, (d_db, d_cl)

    timed_arms = ["A", "X2", "X8"] + [f"P-{g}-{k}" for g, k in P_CONFIGS]
    for arm in timed_arms:
        for timed_pass in (False, True):
            for qi, (rid, s, _) in enumerate(queries):
                rec = res["per_query"].setdefault(qi, {"ms": {}, "cands": {}})
                if arm == "A":
                    top, ms = timed(lambda: arm_a(conn, rid, s))
                elif arm.startswith("X"):
                    conn.execute(f"SET max_parallel_workers_per_gather = {arm[1:]}")
                    (top, flag), ms = timed(lambda: arm_x(conn, rid, s))
                    rec.setdefault("x_exhausted", {})[arm] = flag
                    conn.execute("RESET max_parallel_workers_per_gather")
                else:
                    _, g, k = arm.split("-")
                    (top, cand), ms = timed(lambda: arm_p(conn, rid, s, g, int(k)))
                    rec["cands"][arm] = len(cand)
                    rec.setdefault("cand_sets", {})[arm] = cand
                rec[arm], rec["ms"][arm] = top, ms
        print("timed", arm, flush=True)

    # offline arms: exact probes and equal-budget exact zero-lag
    Q61 = None
    for qi, (rid, s, typ) in enumerate(queries):
        rec = res["per_query"][qi]
        q = query_envelope(conn, rid, s).astype(np.float32)
        Q61 = P.probe_matrix(q, P.GRIDS["G61"])
        with np.errstate(all="ignore"):
            S61 = G @ Q61                                                # N x 61 exact lag terms (float32)
        A = [tuple(x) for x in rec["A"]]
        rec["lstar"] = [int(P.GRIDS["G61"][int(np.argmax(S61[index[a]]))]) for a in A]
        cos0 = S61[:, 30] / (np.linalg.norm(G, axis=1) * np.linalg.norm(q) + 1e-30)
        for g, k in CONFIGS:
            cols = [P.GRIDS["G61"].index(l) for l in P.GRIDS[g]]
            seen, cand = set(), []
            for c in cols:
                for i in P.topk(S61[:, c], k, keys):
                    if i not in seen:
                        seen.add(i)
                        cand.append(ids[i])
            arm = f"PX-{g}-{k}"
            rec[arm], rec["cands"][arm] = rerank(conn, rid, s, cand), len(cand)
            rec.setdefault("cand_sets", {})[arm] = cand
            if g != "G61":
                ez = [ids[i] for i in P.topk(cos0, len(cand), keys)]
                rec[f"EZ-{g}-{k}"], rec["cands"][f"EZ-{g}-{k}"] = rerank(conn, rid, s, ez), len(ez)
                rec["cand_sets"][f"EZ-{g}-{k}"] = ez
        if qi % 20 == 0:
            print("offline", qi, flush=True)
    summarize(res, queries, out)


def summarize(res, queries, out):
    per = res["per_query"]
    arms = [a for a in per[0] if a not in ("ms", "cands", "cand_sets", "lstar", "x_exhausted")]
    types = np.array([t for _, _, t in queries])
    bins = {"0-5": (0, 5), "6-15": (6, 15), "16-30": (16, 30)}
    summ = {}
    for arm in arms:
        rec = [len(set(map(tuple, per[q][arm])) & set(map(tuple, per[q]["A"]))) / len(per[q]["A"]) for q in per]
        cov = [len(set(map(tuple, per[q]["cand_sets"][arm])) & set(map(tuple, per[q]["A"]))) / len(per[q]["A"])
               for q in per] if arm in per[0].get("cand_sets", {}) else None
        bylag = {}
        for b, (lo, hi) in bins.items():
            hit = tot = 0
            for q in per:
                got = set(map(tuple, per[q][arm]))
                for a, l in zip(map(tuple, per[q]["A"]), per[q]["lstar"]):
                    if lo <= abs(l) <= hi:
                        tot += 1
                        hit += a in got
            bylag[b] = {"recall": hit / tot if tot else None, "items": tot}
        s = {"recall_mean": float(np.mean(rec)), "recall_min": float(np.min(rec)),
             "recall_quantiles": [float(np.percentile(rec, p)) for p in (5, 25, 50)],
             "recall_by_type": {t: float(np.mean(np.array(rec)[types == t])) for t in ("random", "energetic")},
             "recall_by_lstar": bylag, "per_query_recall": rec}
        if cov is not None:
            s["coverage_mean"] = float(np.mean(cov))
        if arm in per[0]["cands"]:
            c = [per[q]["cands"][arm] for q in per]
            s["unique_candidates"] = {"mean": float(np.mean(c)), "p95": float(np.percentile(c, 95)), "max": int(max(c))}
        if arm in per[0]["ms"]:
            ms = np.array([per[q]["ms"][arm] for q in per])
            s["latency_ms"] = {"p50": float(np.percentile(ms, 50)), "p95": float(np.percentile(ms, 95))}
        summ[arm] = s
    res["x_exhausted_queries"] = {a: int(sum(per[q].get("x_exhausted", {}).get(a, False) for q in per)) for a in ("X2", "X8")}
    setup = json.loads((out / "setup.json").read_text())
    b = setup["bytes"]
    added = b["halfvec_table"] + b["ip_hnsw"]
    a95 = summ["A"]["latency_ms"]["p95"]
    x95 = min(summ["X2"]["latency_ms"]["p95"], summ["X8"]["latency_ms"]["p95"])
    gate = {}
    for g, k in P_CONFIGS:
        arm = f"P-{g}-{k}"
        s = summ[arm]
        gate[arm] = {"exercised": res["plans"][arm]["exercised"], "recall_ge_0.95": s["recall_mean"] >= 0.95,
                     "p95_le_A_over_3": s["latency_ms"]["p95"] <= a95 / 3, "p95_below_best_X": s["latency_ms"]["p95"] < x95,
                     "storage_le_1.5x_raw": added <= 1.5 * b["raw_chunks"]}
        gate[arm]["qualifies"] = all(gate[arm].values())
    xwins = [a for a in ("X2", "X8") if summ[a]["recall_mean"] == 1.0 and summ[a]["latency_ms"]["p95"] <= a95 / 3]
    verdict = ("CONTINUE" if any(v["qualifies"] for v in gate.values()) else "EXACT KERNEL WINS" if xwins else "STOP")
    res.update({"summary": summ, "gate": gate, "added_bytes": added, "verdict": verdict, "exact_kernel_meeting_3x": xwins})
    for q in per:
        per[q].pop("cand_sets", None)
    json.dump(res, open(out / "bl1.json", "w"), indent=1)
    print(json.dumps({a: {k: v for k, v in s.items() if k in ("recall_mean", "coverage_mean", "unique_candidates", "latency_ms")}
                      for a, s in summ.items()}, indent=1))
    print(verdict, xwins, json.dumps(gate))


def main():
    cmd, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DSN, autocommit=True) as conn:
        {"setup": setup, "run": run}[cmd](conn, out)


if __name__ == "__main__":
    main()
