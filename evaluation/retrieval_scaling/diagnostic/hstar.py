#!/usr/bin/env python3
"""R2-D amendment 1 (PROTOCOL.md): H*-K, R2's HNSW shortlist with the query vector as a parameter so the existing
index can be used, then the same exact lag re-ranking. Compared with E-K from diag.json.

  MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/retrieval_scaling/diagnostic/hstar.py evaluation/retrieval_scaling/results \
      evaluation/retrieval_scaling/diagnostic/results
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag as D  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_r2")
H_SQL = """SELECT h.recording_id, h.start_sample, h.v <=> %s::halfvec AS d FROM public.r2_hv h
           WHERE h.tiers @> ARRAY[%s]::int[] ORDER BY h.v <=> %s::halfvec LIMIT %s"""


def shortlist_hstar(conn, rid, s, tier, k):
    q = D.query_vector(conn, rid, s)
    conn.execute(f"SET hnsw.ef_search = {max(k, 100)}")
    rows = conn.execute(H_SQL, (q, int(tier), q, k)).fetchall()
    return sorted((tuple(r) for r in rows), key=lambda c: (c[2], c[0], c[1]))


def uses_index(conn, rid, s, tier, k):
    q = D.query_vector(conn, rid, s)
    conn.execute(f"SET hnsw.ef_search = {max(k, 100)}")
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + H_SQL, (q, int(tier), q, k)).fetchone()[0][0]
    return any(n.get("Index Name") == "r2_hv_hnsw" for n in D.plan_nodes(plan["Plan"]))


def main():
    r2, out = Path(sys.argv[1]), Path(sys.argv[2])
    bench = json.loads((r2 / "bench.json").read_text())
    diag = json.loads((out / "diag.json").read_text())
    queries = [(q["recording_id"], q["start"]) for q in bench["queries"]]
    res = {"tiers": {}}
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET hnsw.iterative_scan = relaxed_order")
        for tier in D.TIERS:
            m = {"index_used": {}, "recall_at_10": {}, "coverage": {}, "covered_but_lost": {}, "shortlist_size": {},
                 "agreement_with_exact": {}, "latency_ms": {}}
            A = {qi: set(map(tuple, bench["tiers"][tier]["per_query"][str(qi)]["A"])) for qi in range(len(queries))}
            for k in D.KS:
                arm = f"H*-{k}"
                ok = uses_index(conn, *queries[0], tier, k)
                m["index_used"][arm] = ok
                if not ok:
                    continue
                per = {}
                for timed_pass in (False, True):
                    for qi, (rid, s) in enumerate(queries):
                        t = time.perf_counter()
                        cand = shortlist_hstar(conn, rid, s, tier, k)
                        final = D.rerank(conn, rid, s, cand)
                        per[qi] = (final, cand, 1e3 * (time.perf_counter() - t))
                # E-K's shortlist is not stored; its final list is. Exact shortlists are recomputed for agreement.
                agree, cov, lost = [], [], []
                for qi, (rid, s) in enumerate(queries):
                    final, cand = per[qi][0], {c[:2] for c in per[qi][1]}
                    exact = {c[:2] for c in D.shortlist_exact(conn, rid, s, tier, k)}
                    agree.append(len(cand & exact) / len(exact))
                    cov.append(len(cand & A[qi]) / len(A[qi]))
                    lost.append(len((cand & A[qi]) - set(final)))
                ms = np.array([per[q][2] for q in per])
                m["recall_at_10"][arm] = float(np.mean([len(set(per[q][0]) & A[q]) / len(A[q]) for q in per]))
                m["coverage"][arm] = float(np.mean(cov))
                m["covered_but_lost"][arm] = {"mean_per_query": float(np.mean(lost)), "queries_affected": int(sum(x > 0 for x in lost))}
                m["shortlist_size"][arm] = {"mean": float(np.mean([len(per[q][1]) for q in per])), "min": int(min(len(per[q][1]) for q in per))}
                m["agreement_with_exact"][arm] = {"mean": float(np.mean(agree)), "min": float(min(agree))}
                m["latency_ms"][arm] = {"p50": float(np.percentile(ms, 50)), "p95": float(np.percentile(ms, 95)),
                                        "path": "HNSW index scan (parameter vector) + in-DB re-rank"}
                m["E_recall_at_10"] = {f"E-{kk}": diag["tiers"][tier]["recall_at_10"][f"E-{kk}"] for kk in D.KS}
            res["tiers"][tier] = m
            print(tier, json.dumps({k: m[k] for k in ("index_used", "recall_at_10", "coverage", "agreement_with_exact")}), flush=True)
    t3 = res["tiers"]["3"]
    if t3["index_used"].get("H*-1000"):
        diff = diag["tiers"]["3"]["recall_at_10"]["E-1000"] - t3["recall_at_10"]["H*-1000"]
        res["tier3_E1000_minus_Hstar1000"] = diff
        res["interpretation"] = ("ANN approximation is the main loss" if diff >= 0.10 else
                                 "zero-lag candidate metric is the main loss"
                                 if diag["tiers"]["3"]["recall_at_10"]["E-1000"] < 0.95 else "both contribute")
    json.dump(res, open(out / "hstar.json", "w"), indent=1)
    print(res.get("interpretation"), res.get("tier3_E1000_minus_Hstar1000"))


if __name__ == "__main__":
    main()
