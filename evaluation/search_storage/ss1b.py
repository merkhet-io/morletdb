#!/usr/bin/env python3
"""SS1b (SS1_PROTOCOL.md, amendment 1): an HNSW expression index on the envelope table replaces the probe table.
Probe search over the expression index vs the current probe table, same session, same procedure.

  .venv-seismic/bin/python -I evaluation/search_storage/ss1b.py morlet_r2 <reference json> OUT
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ss1  # noqa: E402

EXPR = "(public.morlet_envelope_decode(envelope)::public.halfvec(410))"
CANDIDATES = {
    "current": "SELECT recording_id, start_sample FROM public.r2_hv ORDER BY v <#> %s::float8[]::halfvec LIMIT 50",
    "expression": f"SELECT recording_id, start_sample FROM public.r2_envelopes WHERE window_size = 4096 "
                  f"ORDER BY {EXPR} <#> %s::float8[]::halfvec LIMIT 50",
}
RERANK = f"""SELECT e.recording_id, e.start_sample, morlet_envelope_distance(%s::float8[], e.envelope, {ss1.LAG}) d
             FROM (SELECT DISTINCT * FROM unnest(%s::text[], %s::bigint[]) c(recording_id, start_sample)) c
             JOIN public.r2_envelopes e ON e.recording_id = c.recording_id AND e.start_sample = c.start_sample AND e.window_size = {ss1.W}
             ORDER BY d, e.recording_id, e.start_sample"""


def build(conn):
    conn.execute("SET maintenance_work_mem = '2GB'")
    conn.execute("DROP INDEX IF EXISTS public.r2_envelopes_probe_ip")
    t = time.perf_counter()
    conn.execute(f"CREATE INDEX r2_envelopes_probe_ip ON public.r2_envelopes USING hnsw ({EXPR} public.halfvec_ip_ops) "
                 "WITH (m = 16, ef_construction = 64) WHERE window_size = 4096")
    conn.execute("ANALYZE public.r2_envelopes")
    return time.perf_counter() - t


def plan(conn, arm, queries):
    q = ss1.qenv(conn, *queries[0])
    probe = conn.execute("SELECT morlet_envelope_probe(%s::float8[], 0)", (q,)).fetchone()[0]
    p = "\n".join(r[0] for r in conn.execute("EXPLAIN " + CANDIDATES[arm], (probe,)).fetchall())
    assert "Index Scan using" in p, p
    return p


def run(conn, arm, queries, refs):
    conn.execute(f"SET hnsw.ef_search = {ss1.EF}")
    recalls, ms = [], []
    for timed in (0, 1):
        for (rid, s), ref in zip(queries, refs):
            t = time.perf_counter()
            q = ss1.qenv(conn, rid, s)
            ids, starts = [], []
            for lag in ss1.LAGS:
                probe = conn.execute("SELECT morlet_envelope_probe(%s::float8[], %s)", (q, lag)).fetchone()[0]
                for r, st in conn.execute(CANDIDATES[arm], (probe,)).fetchall():
                    ids.append(r)
                    starts.append(st)
            rows = conn.execute(RERANK, (q, ids, starts)).fetchall()
            top = ss1.suppress([(r, int(st), d) for r, st, d in rows], rid, s)
            if timed:
                ms.append(1e3 * (time.perf_counter() - t))
                recalls.append(len({(r, st) for r, st, _ in top} & set(ref)) / len(ref))
    return {"recall_mean": float(np.mean(recalls)), "recall_min": float(np.min(recalls)), "per_query": recalls,
            "p50_ms": float(np.percentile(ms, 50)), "p95_ms": float(np.percentile(ms, 95))}


def main():
    db, ref_path, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    queries, refs = ss1.references(ref_path)
    with psycopg.connect(ss1.dsn(db), autocommit=True) as conn:
        res = {"database": db, "index_build_s": build(conn)}
        res["plans"] = {arm: plan(conn, arm, queries) for arm in CANDIDATES}
        for arm in ("current", "expression", "current", "expression"):     # alternate; keep the second pass of each
            res[arm] = run(conn, arm, queries, refs)
            print(arm, round(res[arm]["recall_mean"], 4), round(res[arm]["p50_ms"]), round(res[arm]["p95_ms"]), flush=True)
        q = lambda sql: conn.execute(sql).fetchone()[0]
        res["bytes"] = {"envelopes_table": q("SELECT pg_relation_size('public.r2_envelopes') + pg_relation_size('public.r2_envelopes_pkey')"
                                             " + coalesce(pg_total_relation_size(reltoastrelid), 0) FROM pg_class WHERE oid = 'public.r2_envelopes'::regclass"),
                        "expression_index": q("SELECT pg_relation_size('public.r2_envelopes_probe_ip')"),
                        "current_probe_table": q("SELECT pg_table_size('public.r2_hv') + pg_relation_size('public.r2_hv_pkey')"),
                        "current_ip_index": q("SELECT pg_relation_size(i.indexrelid) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                                              "WHERE i.indrelid = 'public.r2_hv'::regclass AND pg_get_indexdef(i.indexrelid) LIKE '%halfvec_ip_ops%' LIMIT 1")}
    json.dump(res, open(out / f"ss1b_{db}.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ("plans", "current", "expression")}, indent=1))


if __name__ == "__main__":
    main()
