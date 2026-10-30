#!/usr/bin/env python3
"""P1: can plain PostgreSQL + pgvector halfvec replace pg_morlet's materialized envelopes?

Engineering benchmark (plan fixed in README.md before running). Data: the 1,826 CX 2022
seismic windows (4096 samples, 100 Hz) already loaded in morlet_envelope_check; this script
runs on a copy (morlet_halfvec_bench). Every window is a query once: same station, itself
excluded, top 10.

  A  pg_morlet   morlet_search_envelope_interval: stored f16 envelopes, max cross-correlation
                 over lags up to floor(0.075 n) = 30, scored in Rust in the database. Reference.
  B  halfvec     same envelope values in a plain halfvec(410) column; exact cosine
                 (no lag), ORDER BY env <=> q LIMIT 10.
  C  halfvec     as B through an HNSW index (halfvec_cosine_ops, m 16, ef_construction 64,
                 ef_search 100, iterative_scan strict_order).
  D  client      plain halfvec storage; the client fetches the station's envelopes and
                 scores the same lagged cross-correlation in numpy.

Envelopes for B-D are computed in the client from the raw samples (the R1 reference
definition), never with pg_morlet functions.

  .venv-seismic/bin/python -I evaluation/plain_postgres/halfvec_bench.py evaluation/plain_postgres/results
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from scipy.signal import hilbert

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_halfvec_bench")
COLL = "envcheck_2022"
WINDOW, TOP, LAG_FRACTION = 4096, 10, 0.075


def envelope(x):
    """R1 reference envelope (evaluation/decision/timing.py): z-score, |Hilbert|, 50-sample mean, every 10th, z-score."""
    s = x.std()
    x = (x - x.mean()) / (s if s > 0 else 1.0)
    env = np.convolve(np.abs(hilbert(x)), np.ones(50) / 50, mode="same")[::10]
    s = env.std()
    return (env - env.mean()) / (s if s > 0 else 1.0)


def zscore(v):
    s = v.std()
    return (v - v.mean()) / (s if s > 0 else 1.0)


def lag_distance(q, G, max_lag):
    """1 - max over |lag| <= max_lag of (1/n) sum q[t+lag] g[t], for every row of G (as morlet_core::envelope::distance)."""
    n = len(q)
    best = G @ q
    for lag in range(1, max_lag + 1):
        best = np.maximum(best, G[:, :n - lag] @ q[lag:])
        best = np.maximum(best, G[:, lag:] @ q[:n - lag])
    return 1 - best / n


def vec(v):
    return "[" + ",".join(f"{x:.9g}" for x in v) + "]"


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, 1e3 * (time.perf_counter() - t)


def table_bytes(conn, rel):
    return conn.execute("SELECT pg_total_relation_size(%s::regclass)", (rel,)).fetchone()[0]


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    res = {"plan": "README.md", "windows": None}
    with psycopg.connect(DSN, autocommit=True) as conn:
        recs = conn.execute(f"SELECT recording_id, metadata->>'station' FROM {COLL} ORDER BY recording_id").fetchall()
        res["windows"] = len(recs)
        station = dict(recs)

        # -- plain table: envelopes computed in the client, stored as halfvec -----------------------
        envs = {}
        for rid, _ in recs:
            s = conn.execute("SELECT samples FROM morlet_read_interval(%s, %s, 0, %s)", (COLL, rid, WINDOW)).fetchone()[0]
            envs[rid] = envelope(np.asarray(s, float))
        n = len(next(iter(envs.values())))
        max_lag = int(np.floor(LAG_FRACTION * n))
        # Representation check: client f16 values vs pg_morlet's stored bytes.
        same = 0
        for rid, (blob,) in ((r, conn.execute(f"SELECT envelope FROM {COLL}_envelopes WHERE recording_id=%s", (r,)).fetchone())
                             for r, _ in recs):
            same += bytes(blob[1:]) == envs[rid].astype("<f2").tobytes()
        res["representation"] = {"values_per_envelope": n, "max_lag": max_lag,
                                 "client_f16_identical_to_stored": f"{same}/{len(recs)}"}

        conn.execute("DROP TABLE IF EXISTS plain_env")
        conn.execute(f"CREATE TABLE plain_env (recording_id text PRIMARY KEY, station text NOT NULL, env halfvec({n}) NOT NULL)")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO plain_env VALUES (%s, %s, %s::halfvec)",
                            [(rid, station[rid], vec(envs[rid])) for rid, _ in recs])
        conn.execute("CREATE INDEX plain_env_station ON plain_env (station)")
        conn.execute("VACUUM ANALYZE plain_env")
        conn.execute(f"VACUUM ANALYZE {COLL}_envelopes")
        res["storage_bytes"] = {"pg_morlet_envelopes_table": table_bytes(conn, f"{COLL}_envelopes"),
                                "plain_halfvec_table": table_bytes(conn, "plain_env")}

        # -- searches ------------------------------------------------------------------------------
        def run_a(rid):
            return [r[0] for r in conn.execute(
                "SELECT recording_id FROM morlet_search_envelope_interval(%s, %s, 0, %s, metadata_filter => %s::jsonb,"
                " top_k => %s, exclude_source => true)", (COLL, rid, WINDOW, json.dumps({"station": station[rid]}), TOP))]

        def run_b(rid):
            return [r[0] for r in conn.execute(
                "SELECT recording_id FROM plain_env WHERE station = %s AND recording_id <> %s"
                " ORDER BY env <=> %s::halfvec LIMIT %s", (station[rid], rid, vec(envs[rid]), TOP))]

        def run_d(rid):
            rows = conn.execute("SELECT recording_id, env::real[] FROM plain_env WHERE station = %s AND recording_id <> %s",
                                (station[rid], rid)).fetchall()
            G = np.array([zscore(np.asarray(e, float)) for _, e in rows])
            d = lag_distance(envs[rid], G, max_lag)            # full-precision query, as in A
            return [rows[i][0] for i in np.argsort(d, kind="stable")[:TOP]]

        results, lat = {}, {}
        for name, fn in (("A", run_a), ("B", run_b), ("D", run_d)):
            for rid, _ in recs[:20]:
                fn(rid)                                          # warm-up
            results[name], lat[name] = {}, []
            for rid, _ in recs:
                r, ms = timed(lambda: fn(rid))
                results[name][rid], lat[name] = r, lat[name] + [ms]

        conn.execute("SET maintenance_work_mem = '256MB'")
        _, build_ms = timed(lambda: conn.execute(
            "CREATE INDEX plain_env_hnsw ON plain_env USING hnsw (env halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)"))
        conn.execute("SET hnsw.ef_search = 100")
        conn.execute("SET hnsw.iterative_scan = strict_order")
        res["storage_bytes"]["plain_halfvec_hnsw_index"] = table_bytes(conn, "plain_env_hnsw")
        res["hnsw_build_ms"] = build_ms
        plan = conn.execute("EXPLAIN SELECT recording_id FROM plain_env WHERE station = 'PB01' AND recording_id <> 'x'"
                            " ORDER BY env <=> %s::halfvec LIMIT 10", (vec(envs[recs[0][0]]),)).fetchall()
        res["hnsw_plan_uses_index"] = any("plain_env_hnsw" in p[0] for p in plan)
        for rid, _ in recs[:20]:
            run_b(rid)
        results["C"], lat["C"] = {}, []
        for rid, _ in recs:
            r, ms = timed(lambda: run_b(rid))
            results["C"][rid], lat["C"] = r, lat["C"] + [ms]

    ref = results["A"]
    res["recall_at_10_vs_pg_morlet"] = {k: float(np.mean([len(set(results[k][r]) & set(ref[r])) / len(ref[r]) for r in ref]))
                                        for k in ("B", "C", "D")}
    res["identical_order_vs_pg_morlet"] = {k: float(np.mean([results[k][r] == ref[r] for r in ref])) for k in ("B", "C", "D")}
    res["recall_C_vs_B"] = float(np.mean([len(set(results["C"][r]) & set(results["B"][r])) / TOP for r in ref]))
    res["latency_ms"] = {k: {"p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95))} for k, v in lat.items()}
    (out / "halfvec.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
