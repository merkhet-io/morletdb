#!/usr/bin/env python3
"""SS1 (SS1_PROTOCOL.md): a single halfvec table holding the stored float16 envelope bits serves exact scoring
(morlet_envelope_distance_send over halfvec_send) and probe search. Scratch table `<c>_env1` in an existing database.

  .venv-seismic/bin/python -I evaluation/search_storage/ss1.py morlet_r2  evaluation/bounded_lag/results/../../retrieval_scaling/results/bench.json OUT
  .venv-seismic/bin/python -I evaluation/search_storage/ss1.py morlet_bl1c evaluation/bounded_lag/confirm/results/confirm.json OUT
"""
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

COLL, W, LAG, TOP = "public.r2", 4096, 30, 10
LAGS = list(range(-30, 31, 5))
PER_PROBE, EF = 50, 100


def dsn(db):
    return f"host=localhost port=28817 dbname={db}"


def references(path):
    """Queries and exact top-10 lists: R2's bench.json (tier 3) or BL1-C's confirm.json."""
    r = json.loads(Path(path).read_text())
    if "tiers" in r:
        qs = [(q["recording_id"], q["start"]) for q in r["queries"]]
        return qs, [[tuple(x) for x in r["tiers"]["3"]["per_query"][str(i)]["A"]] for i in range(len(qs))]
    qp = Path(path).with_name("queries.json")
    qs = [(q["recording_id"], q["start"]) for q in json.loads(qp.read_text())["queries"]]
    return qs, [[tuple(x) for x in r["per_query"][str(i)]["A"]] for i in range(len(qs))]


def build(conn):
    """<c>_env1 from the envelope payload bits (exact float16 values), then the inner-product index."""
    conn.execute("DROP TABLE IF EXISTS public.r2_env1")
    conn.execute("""CREATE TABLE public.r2_env1 (recording_id text NOT NULL, window_size integer NOT NULL,
                    start_sample bigint NOT NULL, v halfvec(410) NOT NULL, PRIMARY KEY (recording_id, window_size, start_sample))""")
    t = time.perf_counter()
    with conn.transaction(), conn.cursor(name="env") as cur, conn.cursor() as out:
        cur.itersize = 5000
        cur.execute("SELECT recording_id, start_sample, envelope FROM public.r2_envelopes WHERE window_size = %s", (W,))
        with out.copy("COPY public.r2_env1 (recording_id, window_size, start_sample, v) FROM STDIN") as cp:
            for rid, s, env in cur:
                vals = np.frombuffer(bytes(env)[1:], "<f2").astype(np.float64)    # exact: binary16 -> binary64
                cp.write_row((rid, W, s, "[" + ",".join(repr(float(x)) for x in vals) + "]"))
    load_s = time.perf_counter() - t
    t = time.perf_counter()
    conn.execute("SET maintenance_work_mem = '2GB'")
    conn.execute("CREATE INDEX r2_env1_ip ON public.r2_env1 USING hnsw (v halfvec_ip_ops) WITH (m = 16, ef_construction = 64)")
    conn.execute("VACUUM ANALYZE public.r2_env1")
    return {"load_s": load_s, "index_s": time.perf_counter() - t}


def value_identity(conn):
    """Every window: halfvec_send(v) float16 values == bytea payload, bit for bit."""
    bad = n = 0
    with conn.transaction(), conn.cursor(name="cmp") as cur:
        cur.itersize = 5000
        cur.execute("""SELECT e.envelope, halfvec_send(n.v) FROM public.r2_envelopes e JOIN public.r2_env1 n
                       USING (recording_id, window_size, start_sample) WHERE e.window_size = %s""", (W,))
        for env, send in cur:
            a = np.frombuffer(bytes(env)[1:], "<u2")
            b = np.frombuffer(bytes(send)[4:], ">u2")
            n += 1
            bad += int(not np.array_equal(a, b))
    return {"windows": n, "mismatching_windows": bad}


def qenv(conn, rid, s):
    return conn.execute("SELECT morlet_envelope(samples) FROM morlet_read_interval(%s, %s, %s, %s)", (COLL, rid, s, s + W)).fetchone()[0]


def distance_identity(conn, queries):
    """100 queries x their 200 best candidates, and 20 queries x every window: bitwise-equal distances."""
    out = {"top200_pairs": 0, "top200_unequal": 0, "full_pairs": 0, "full_unequal": 0}
    for i, (rid, s) in enumerate(queries):
        q = qenv(conn, rid, s)
        full = i < 20
        row = conn.execute(f"""WITH d AS (SELECT morlet_envelope_distance(%s::float8[], e.envelope, {LAG}) a,
                                                 morlet_envelope_distance_send(%s::float8[], halfvec_send(n.v), {LAG}) b
                                          FROM public.r2_envelopes e JOIN public.r2_env1 n USING (recording_id, window_size, start_sample)
                                          WHERE e.window_size = {W} ORDER BY a LIMIT %s)
                               SELECT count(*), count(*) FILTER (WHERE a IS DISTINCT FROM b) FROM d""",
                           (q, q, 10 ** 9 if full else 200)).fetchone()
        key = "full" if full else "top200"
        out[f"{key}_pairs"] += row[0]
        out[f"{key}_unequal"] += row[1]
    return out


def suppress(ranked, rid, start):
    out = []
    for r, s, d in ranked:
        if r == rid and s < start + W and s + W > start:
            continue
        if any(r == pr and s < ps + W and s + W > ps for pr, ps, _ in out):
            continue
        out.append((r, s, d))
        if len(out) == TOP:
            break
    return out


EXACT = {"bytea": f"""SELECT e.recording_id, e.start_sample, morlet_envelope_distance(%s::float8[], e.envelope, {LAG}) d
                      FROM public.r2_envelopes e WHERE e.window_size = {W} ORDER BY d, e.recording_id, e.start_sample LIMIT 200""",
         "halfvec": f"""SELECT n.recording_id, n.start_sample, morlet_envelope_distance_send(%s::float8[], halfvec_send(n.v), {LAG}) d
                        FROM public.r2_env1 n WHERE n.window_size = {W} ORDER BY d, n.recording_id, n.start_sample LIMIT 200"""}


def exact_search(conn, queries, refs, timed=True):
    """Exact top 10 from each container (same statement shape, custom plans, up to 8 workers); identity vs the
    reference lists and between containers; latency per container."""
    conn.execute("SET plan_cache_mode = force_custom_plan")
    conn.execute("SET max_parallel_workers_per_gather = 8")
    res = {"identical_to_reference": 0, "containers_identical_with_distances": 0, "ms": {"bytea": [], "halfvec": []}}
    for p in (0, 1):
        for (rid, s), ref in zip(queries, refs):
            q = qenv(conn, rid, s)
            got = {}
            for k, sql in EXACT.items():
                t = time.perf_counter()
                got[k] = suppress([(r, int(st), d) for r, st, d in conn.execute(sql, (q,)).fetchall()], rid, s)
                if p:
                    res["ms"][k].append(1e3 * (time.perf_counter() - t))
            if p:
                res["identical_to_reference"] += [(r, st) for r, st, _ in got["halfvec"]] == ref
                res["containers_identical_with_distances"] += got["halfvec"] == got["bytea"]
    res["latency"] = {k: {"p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95))} for k, v in res["ms"].items()}
    del res["ms"]
    return res


def probe_uses_index(conn, table, queries):
    q = qenv(conn, *queries[0])
    probe = conn.execute("SELECT morlet_envelope_probe(%s::float8[], 0)", (q,)).fetchone()[0]
    plan = "\n".join(r[0] for r in conn.execute(
        f"EXPLAIN SELECT recording_id, start_sample FROM {table} ORDER BY v <#> %s::float8[]::halfvec LIMIT {PER_PROBE}", (probe,)).fetchall())
    assert "Index Scan using" in plan, plan
    return plan


def probe_search(conn, table, scorer, queries, refs):
    """13 shifted probes x 50 (ef_search 100) on `table`, union, exact re-rank with `scorer` on the same table."""
    conn.execute(f"SET hnsw.ef_search = {EF}")
    plan = probe_uses_index(conn, table, queries)
    recalls, ms = [], []
    for p in (0, 1):
        for (rid, s), ref in zip(queries, refs):
            t = time.perf_counter()
            q = qenv(conn, rid, s)
            ids, starts = [], []
            for lag in LAGS:
                probe = conn.execute("SELECT morlet_envelope_probe(%s::float8[], %s)", (q, lag)).fetchone()[0]
                for r, st in conn.execute(f"SELECT recording_id, start_sample FROM {table} ORDER BY v <#> %s::float8[]::halfvec LIMIT {PER_PROBE}",
                                          (probe,)).fetchall():
                    ids.append(r)
                    starts.append(st)
            rows = conn.execute(f"""SELECT t.recording_id, t.start_sample, {scorer} d
                                    FROM (SELECT DISTINCT * FROM unnest(%s::text[], %s::bigint[]) c(recording_id, start_sample)) c
                                    JOIN {table} t USING (recording_id, start_sample)
                                    ORDER BY d, t.recording_id, t.start_sample""", (q, ids, starts)).fetchall()
            top = suppress([(r, int(st), d) for r, st, d in rows], rid, s)
            if p:
                ms.append(1e3 * (time.perf_counter() - t))
                recalls.append(len({(r, st) for r, st, _ in top} & set(ref)) / len(ref))
    return {"plan": plan, "recall_mean": float(np.mean(recalls)), "recall_min": float(np.min(recalls)), "per_query": recalls,
            "p50_ms": float(np.percentile(ms, 50)), "p95_ms": float(np.percentile(ms, 95))}


def sizes(conn):
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {"raw_chunks": q("SELECT pg_total_relation_size('public.r2_chunks')"),
            "envelopes": q("SELECT pg_total_relation_size('public.r2_envelopes')"),
            "current_probe_table": q("SELECT pg_table_size('public.r2_hv') + pg_relation_size('public.r2_hv_pkey')"),
            "single_table": q("SELECT pg_table_size('public.r2_env1') + pg_relation_size('public.r2_env1_pkey')"),
            "ip_index_single": q("SELECT pg_relation_size('public.r2_env1_ip')"),
            "windows": q(f"SELECT count(*) FROM public.r2_envelopes WHERE window_size = {W}")}


def main():
    db, ref_path, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    queries, refs = references(ref_path)
    with psycopg.connect(dsn(db), autocommit=True) as conn:
        res = {"database": db, "build": build(conn)}
        print("built", res["build"], flush=True)
        res["value_identity"] = value_identity(conn)
        print("values", res["value_identity"], flush=True)
        res["distance_identity"] = distance_identity(conn, queries)
        print("distances", res["distance_identity"], flush=True)
        res["exact_search"] = exact_search(conn, queries, refs)
        print("exact", {k: v for k, v in res["exact_search"].items()}, flush=True)
        current = "public.r2_hv"
        res["probe_current"] = probe_search(conn, current,
                                            f"(SELECT morlet_envelope_distance(%s::float8[], e.envelope, {LAG}) FROM public.r2_envelopes e "
                                            f"WHERE e.recording_id = t.recording_id AND e.start_sample = t.start_sample AND e.window_size = {W})",
                                            queries, refs)
        res["probe_single"] = probe_search(conn, "public.r2_env1", f"morlet_envelope_distance_send(%s::float8[], halfvec_send(t.v), {LAG})",
                                           queries, refs)
        print("probe", {k: res[k]["recall_mean"] for k in ("probe_current", "probe_single")}, flush=True)
        res["bytes"] = sizes(conn)
    json.dump(res, open(out / f"ss1_{db}.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ("probe_current", "probe_single")}, indent=1))


if __name__ == "__main__":
    main()
