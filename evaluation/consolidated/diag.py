"""Diagnostics D1 and D2 (DIAGNOSTICS_PROTOCOL.md), development database only.

  .venv-seismic/bin/python -I evaluation/consolidated/diag.py d1 morlet_r2 evaluation/bounded_lag/dev_queries.json out.json
  .venv-seismic/bin/python -I evaluation/consolidated/diag.py d2 morlet_r2 evaluation/bounded_lag/dev_queries.json out.json
"""
import json
import os
import random
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

HERE = Path(__file__).resolve().parent
COLL, W = "public.r2", 4096
EXACT = ("SELECT recording_id, start_sample FROM morlet_search_envelope_interval(%s, %s, %s, %s, %s, %s, 0.075, true, true,"
         " 1000000)")
ROUTED = "SELECT recording_id, start_sample FROM morlet_search_envelope_probes(%s, %s, %s, %s, %s, 10)"


def stats(xs):
    a = np.asarray(xs, dtype=float)
    return {"p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)), "mean": float(a.mean()),
            "max": float(a.max()), "n": int(len(a))}


def timed(conn, sql, args):
    t = time.perf_counter()
    rows = conn.execute(sql, args).fetchall()
    return [(r, int(s)) for r, s in rows], 1e3 * (time.perf_counter() - t)


def d1(conn, queries):
    conn.execute((HERE / "d1_profile.sql").read_text())
    res = {"loadavg_start": list(os.getloadavg()), "per_query": {}}
    for rid, s, _ in queries[:5]:                                           # warm-up
        conn.execute("SELECT diag.probe_profile(%s, %s, %s, %s)", (COLL, rid, s, s + W))
        timed(conn, ROUTED, (COLL, rid, s, s + W, Jsonb({})))
    for p in (1, 2):
        for rid, s, typ in (queries if p == 1 else list(reversed(queries))):
            key = f"{rid}:{s}"
            t = time.perf_counter()
            prof = conn.execute("SELECT diag.probe_profile(%s, %s, %s, %s)", (COLL, rid, s, s + W)).fetchone()[0]
            prof_ms = 1e3 * (time.perf_counter() - t)
            top, prod_ms = timed(conn, ROUTED, (COLL, rid, s, s + W, Jsonb({})))
            rec = res["per_query"].setdefault(key, {"type": typ, "passes": []})
            rec["passes"].append({"profile": prof, "profile_client_ms": prof_ms, "product_ms": prod_ms,
                                  "same_result": [tuple(h) for h in prof["hits"]] == top})
    res["loadavg_end"] = list(os.getloadavg())
    passes = [p for q in res["per_query"].values() for p in q["passes"]]
    stages = ["read_interval", "routing", "query_envelope", "probes_total", "rerank", "suppression", "total"]
    res["summary"] = {
        "stage_ms": {st: stats([p["profile"]["ms"][st] for p in passes]) for st in stages},
        "stage_share_of_total": {st: float(np.mean([p["profile"]["ms"][st] / p["profile"]["ms"]["total"] for p in passes]))
                                 for st in stages[:-1]},
        "per_probe_ms": stats([x for p in passes for x in p["profile"]["probe_ms"]]),
        "product_ms": stats([p["product_ms"] for p in passes]),
        "instrumented_client_ms": stats([p["profile_client_ms"] for p in passes]),
        "instrumentation_overhead_ms": stats([p["profile_client_ms"] - p["product_ms"] for p in passes]),
        "rows_per_probe": stats([x for p in passes for x in p["profile"]["probe_rows"]]),
        "unique_candidates": stats([p["profile"]["unique_candidates"] for p in passes]),
        "excluded_source": stats([p["profile"]["excluded_source"] for p in passes]),
        "suppressed": stats([p["profile"]["suppressed"] for p in passes]),
        "considered": stats([p["profile"]["considered"] for p in passes]),
        "short_results": int(sum(p["profile"]["returned"] < 10 for p in passes)),
        "result_differs_from_product": int(sum(not p["same_result"] for p in passes)),
    }
    # predicate share for one probe statement (first query, lag 0)
    rid, s, _ = queries[0]
    q = conn.execute("SELECT morlet_envelope(samples) FROM morlet_read_interval(%s, %s, %s, %s)", (COLL, rid, s, s + W)).fetchone()[0]
    probe = conn.execute("SELECT morlet_envelope_probe(%s::float8[], 0)", (q,)).fetchone()[0]
    eligible = conn.execute(f"SELECT array_agg(recording_id) FROM {COLL}").fetchone()[0]
    conn.execute("SET hnsw.ef_search = 100")
    conn.execute("SET hnsw.iterative_scan = relaxed_order")
    plans = {}
    for name, where, args in (("with_predicate", "WHERE recording_id = ANY(%s)", (eligible, probe)), ("without", "", (probe,))):
        sql = (f"EXPLAIN (ANALYZE, BUFFERS) SELECT recording_id, start_sample FROM public.r2_probes_4096 {where}"
               " ORDER BY v <#> %s::float8[]::halfvec LIMIT 50")
        for _ in range(3):
            plan = "\n".join(r[0] for r in conn.execute(sql, args).fetchall())
        plans[name] = plan
    conn.execute("RESET hnsw.ef_search")
    conn.execute("RESET hnsw.iterative_scan")
    res["probe_statement_plans"] = plans
    return res


def d2(conn, queries):
    rng = random.Random(20261101)
    days = [r[0] for r in conn.execute(f"SELECT DISTINCT (metadata->>'day')::int FROM {COLL} ORDER BY 1").fetchall()]
    stations = [r[0] for r in conn.execute(f"SELECT DISTINCT metadata->>'station' FROM {COLL} ORDER BY 1").fetchall()]
    meta = {r: (int(m["day"]), m["station"]) for r, m in conn.execute(f"SELECT recording_id, metadata FROM {COLL}").fetchall()}
    windows = {}
    for key, flt in [(("day", d), {"day": d}) for d in days] + [(("station", st), {"station": st}) for st in stations]:
        windows[key] = conn.execute(f"SELECT count(*) FROM public.r2_envelopes e JOIN {COLL} r USING (recording_id)"
                                    " WHERE e.window_size = 4096 AND r.metadata @> %s", (Jsonb(flt),)).fetchone()[0]
    res = {"loadavg_start": list(os.getloadavg()), "eligible_windows": {f"{k[0]}={k[1]}": v for k, v in windows.items()},
           "per_query": {}}
    for rid, s, _ in queries[:3]:                                            # warm-up
        timed(conn, ROUTED, (COLL, rid, s, s + W, Jsonb({"day": days[0]})))
    for rid, s, typ in queries:
        key = f"{rid}:{s}"
        top100, _ = timed(conn, EXACT, (COLL, rid, s, s + W, Jsonb({}), 100))
        share = {}
        for dim, values, pos in (("day", days, 0), ("station", stations, 1)):
            share[dim] = {v: sum(meta[r][pos] == v for r, _ in top100) for v in values}
        rec = {"type": typ, "top100_share": {d: {str(k): v for k, v in sh.items()} for d, sh in share.items()}, "filters": {}}
        for dim, values in (("day", days), ("station", stations)):
            sh = share[dim]
            choice = {"positive": max(values, key=lambda v: (sh[v], -values.index(v))),
                      "negative": min(values, key=lambda v: (sh[v], values.index(v))),
                      "neutral": rng.choice(values)}
            for corr, v in choice.items():
                flt = Jsonb({dim: v})
                ref, ex_ms = timed(conn, EXACT, (COLL, rid, s, s + W, flt, 10))
                got, ms = timed(conn, ROUTED, (COLL, rid, s, s + W, flt))
                rec["filters"][f"{dim}:{corr}"] = {"value": v, "neighbours_in_filter": sh[v], "eligible_windows": windows[(dim, v)],
                                                   "recall": len(set(ref) & set(got)) / max(len(ref), 1), "returned": len(got),
                                                   "reference_size": len(ref), "ms": ms, "exact_ms": ex_ms}
        res["per_query"][key] = rec
    res["loadavg_end"] = list(os.getloadavg())
    summ = {}
    for cell in [f"{d}:{c}" for d in ("day", "station") for c in ("positive", "neutral", "negative")]:
        xs = [q["filters"][cell] for q in res["per_query"].values()]
        r = [x["recall"] for x in xs]
        summ[cell] = {"recall_mean": float(np.mean(r)), "recall_min": float(min(r)), "below_0.9": int(sum(v < 0.9 for v in r)),
                      "short_results": int(sum(x["returned"] < x["reference_size"] for x in xs)),
                      "neighbours_in_filter_mean": float(np.mean([x["neighbours_in_filter"] for x in xs])),
                      "eligible_windows_mean": float(np.mean([x["eligible_windows"] for x in xs])),
                      "latency_ms": stats([x["ms"] for x in xs]), "exact_latency_ms": stats([x["exact_ms"] for x in xs])}
    res["summary"] = summ
    dp, dn = summ["day:positive"], summ["day:negative"]
    res["verdict"] = {"H_recall_diff_lt_0.05": abs(dp["recall_mean"] - dn["recall_mean"]) < 0.05,
                      "H_p95_ratio_lt_2": max(dp["latency_ms"]["p95"], dn["latency_ms"]["p95"])
                      / min(dp["latency_ms"]["p95"], dn["latency_ms"]["p95"]) < 2}
    return res


def main(which, db, queries_path, out_path):
    queries = [(q["recording_id"], int(q["start"]), q["type"]) for q in json.loads(Path(queries_path).read_text())["queries"]]
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as conn:
        res = {"d1": d1, "d2": d2}[which](conn, queries)
    res["database"] = db
    Path(out_path).write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res["summary"], indent=1, default=str)[:4000])
    if "verdict" in res:
        print("verdict", res["verdict"])


if __name__ == "__main__":
    main(*sys.argv[1:5])
