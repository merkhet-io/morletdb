#!/usr/bin/env python3
"""R2-D (PROTOCOL.md): exact zero-lag shortlists (E-K) against R2's HNSW shortlists (H-K), both followed by the
same exact lag re-ranking, on R2's queries and galleries.

  MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/retrieval_scaling/diagnostic/diag.py evaluation/retrieval_scaling/results \
      evaluation/retrieval_scaling/diagnostic/results
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bench as B  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_r2")
KS, TIERS = (100, 1000), ("0", "1", "2", "3")

E_SQL = """SELECT h.recording_id, h.start_sample, h.v <=> %s::halfvec AS d FROM public.r2_hv h
           WHERE h.tiers @> ARRAY[%s]::int[] ORDER BY d, h.recording_id, h.start_sample LIMIT %s"""


def query_vector(conn, rid, s):
    return conn.execute("SELECT v::text FROM public.r2_hv WHERE recording_id = %s AND start_sample = %s", (rid, s)).fetchone()[0]


def shortlist_exact(conn, rid, s, tier, k):
    q = query_vector(conn, rid, s)
    with conn.transaction():
        conn.execute("SET LOCAL enable_indexscan = off")
        conn.execute("SET LOCAL enable_bitmapscan = off")
        return [tuple(r) for r in conn.execute(E_SQL, (q, int(tier), k)).fetchall()]


def shortlist_hnsw(conn, rid, s, tier, k):
    """R2's stage 1, then an explicit sort: relaxed_order output is not globally sorted."""
    return sorted((tuple(r) for r in B.stage1(conn, rid, s, int(tier), k)), key=lambda c: (c[2], c[0], c[1]))


def rerank(conn, rid, s, cand):
    """R2's stage 2: exact lag distance in the database, same tie-breaking, then exclusion and suppression."""
    rows = conn.execute("""WITH q AS (SELECT morlet_envelope(samples) AS env FROM morlet_read_interval(%s, %s, %s, %s)),
                                c AS (SELECT * FROM unnest(%s::text[], %s::bigint[]) AS c(recording_id, start_sample))
                           SELECT e.recording_id, e.start_sample, morlet_envelope_distance(q.env, e.envelope, %s) AS d
                           FROM c JOIN public.r2_envelopes e ON e.recording_id = c.recording_id AND e.start_sample = c.start_sample
                                AND e.window_size = %s, q
                           ORDER BY d, e.recording_id, e.start_sample""",
                        (B.COLL, rid, s, s + B.WINDOW, [c[0] for c in cand], [c[1] for c in cand], B.LAG, B.WINDOW)).fetchall()
    return B.suppress(rows, rid, s)


def plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from plan_nodes(child)


def check_plan(conn, rid, s, tier, n_tier, n_table):
    q = query_vector(conn, rid, s)
    with conn.transaction():
        conn.execute("SET LOCAL enable_indexscan = off")
        conn.execute("SET LOCAL enable_bitmapscan = off")
        plan = conn.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + E_SQL, (q, int(tier), 1000)).fetchone()[0][0]
    nodes = list(plan_nodes(plan["Plan"]))
    assert not [n["Node Type"] for n in nodes if "Index" in n["Node Type"] or "Bitmap" in n["Node Type"]], "index used"
    scans = [n for n in nodes if n["Node Type"] in ("Seq Scan", "Parallel Seq Scan") and n.get("Relation Name") == "r2_hv"]
    assert len(scans) == 1, "expected one scan of r2_hv"
    sc = scans[0]
    loops = sc["Actual Loops"]
    kept, removed = sc["Actual Rows"] * loops, sc.get("Rows Removed by Filter", 0) * loops
    # parallel workers report per-loop averages rounded to integers: allow at most one row per loop
    assert abs(kept + removed - n_table) <= loops and abs(kept - n_tier) <= loops, (kept, removed, n_table, n_tier)
    return {"node": sc["Node Type"], "loops": loops, "rows_kept": kept, "rows_removed": removed,
            "table_rows": n_table, "tier_rows": n_tier, "plan": plan}


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, 1e3 * (time.perf_counter() - t)


def main():
    r2, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    bench = json.loads((r2 / "bench.json").read_text())
    load = json.loads((r2 / "load.json").read_text())
    queries = [(q["recording_id"], q["start"], q["type"]) for q in bench["queries"]]
    res = {"plans": {}, "tiers": {}}
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET hnsw.iterative_scan = relaxed_order")
        # A is deterministic: re-run 10 queries at tier 0 and require identical lists
        for qi in range(10):
            rid, s, _ = queries[qi]
            again = B.arm_a(conn, rid, s, 0)
            assert [list(x) for x in again] == bench["tiers"]["0"]["per_query"][str(qi)]["A"], f"A differs for query {qi}"
        res["a_recheck"] = "10/10 identical at tier 0"
        n_table = conn.execute("SELECT count(*) FROM public.r2_hv").fetchone()[0]
        for tier in TIERS:
            rid, s, _ = queries[0]
            res["plans"][tier] = check_plan(conn, rid, s, tier, load["windows_per_tier"][tier], n_table)
        print("plans ok", {t: (p["node"], p["rows_kept"]) for t, p in res["plans"].items()}, flush=True)

        for tier in TIERS:
            per = {}
            for timed_pass in (False, True):
                for qi, (rid, s, _) in enumerate(queries):
                    rec = {"ms": {}}
                    for k in KS:
                        for name, fn in (("E", shortlist_exact), ("H", shortlist_hnsw)):
                            (final, cand), ms = timed(lambda: (lambda c: (rerank(conn, rid, s, c), c))(fn(conn, rid, s, tier, k)))
                            rec[f"{name}-{k}"], rec[f"{name}-{k}_cand"], rec["ms"][f"{name}-{k}"] = final, cand, ms
                    rec["ZE"] = B.suppress(rec["E-1000_cand"], rid, s)
                    rec["ZH"] = B.suppress(rec["H-1000_cand"], rid, s)
                    per[qi] = rec
            A = {qi: set(map(tuple, bench["tiers"][tier]["per_query"][str(qi)]["A"])) for qi in per}
            m = {"recall_at_10": {}, "coverage": {}, "covered_but_lost": {}, "shortlist_size": {}, "latency_ms": {}}
            for arm in [f"{n}-{k}" for k in KS for n in "EH"] + ["ZE", "ZH"]:
                m["recall_at_10"][arm] = float(np.mean([len(set(per[q][arm]) & A[q]) / len(A[q]) for q in per]))
            for k in KS:
                for n in "EH":
                    arm = f"{n}-{k}"
                    cov = [len({(c[0], c[1]) for c in per[q][f"{arm}_cand"]} & A[q]) / len(A[q]) for q in per]
                    lost = [len(({(c[0], c[1]) for c in per[q][f"{arm}_cand"]} & A[q]) - set(per[q][arm])) for q in per]
                    sizes = [len(per[q][f"{arm}_cand"]) for q in per]
                    ms = np.array([per[q]["ms"][arm] for q in per])
                    m["coverage"][arm] = float(np.mean(cov))
                    m["covered_but_lost"][arm] = {"mean_per_query": float(np.mean(lost)), "queries_affected": int(sum(x > 0 for x in lost))}
                    m["shortlist_size"][arm] = {"mean": float(np.mean(sizes)), "min": int(min(sizes))}
                    m["latency_ms"][arm] = {"p50": float(np.percentile(ms, 50)), "p95": float(np.percentile(ms, 95)),
                                            "path": "seq scan + top-K sort + in-DB re-rank" if n == "E" else "HNSW index scan + in-DB re-rank"}
                m.setdefault("hnsw_exact_agreement", {})[str(k)] = float(np.mean(
                    [len({c[:2] for c in per[q][f"H-{k}_cand"]} & {c[:2] for c in per[q][f"E-{k}_cand"]}) / max(len(per[q][f"E-{k}_cand"]), 1)
                     for q in per]))
            m["zero_lag_result_size"] = {z: {"min": int(min(len(per[q][z]) for q in per))} for z in ("ZE", "ZH")}
            m["per_query"] = {q: {a: per[q][a] for a in ("E-100", "E-1000", "H-100", "H-1000", "ZE", "ZH")} | {"ms": per[q]["ms"]}
                              for q in per}
            res["tiers"][tier] = m
            print(tier, json.dumps({k: m[k] for k in ("recall_at_10", "coverage", "hnsw_exact_agreement")}), flush=True)

    t3 = res["tiers"]["3"]["recall_at_10"]
    diff = t3["E-1000"] - t3["H-1000"]
    res["interpretation"] = ("ANN approximation is the main loss" if diff >= 0.10 else
                             "zero-lag candidate metric is the main loss" if t3["E-1000"] < 0.95 else "both contribute")
    res["tier3_E1000_minus_H1000"] = diff
    json.dump(res, open(out / "diag.json", "w"), indent=1)
    print(res["interpretation"], round(diff, 3))


if __name__ == "__main__":
    main()
