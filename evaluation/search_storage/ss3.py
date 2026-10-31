#!/usr/bin/env python3
"""SS3 (SS3_PROTOCOL.md): exact (E), post-filter probes (PP), filter-aware probes (PI) and the router (R) at five
metadata-filter levels; filter and RLS checks.

  .venv-seismic/bin/python -I evaluation/search_storage/ss3.py measure morlet_r2   <queries.json> OUT
  .venv-seismic/bin/python -I evaluation/search_storage/ss3.py decide  OUT                      # A and T from morlet_r2
  .venv-seismic/bin/python -I evaluation/search_storage/ss3.py route   morlet_bl1c <queries.json> OUT
  .venv-seismic/bin/python -I evaluation/search_storage/ss3.py rls     morlet_r2   <queries.json> OUT
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

HERE = Path(__file__).resolve().parent
COLL, W = "public.r2", 4096
LEVELS = ("L1", "L2", "L3", "L4", "L5")
SQL = {
    "E": "SELECT recording_id, start_sample, distance, metadata FROM morlet_search_envelope_interval(%s, %s, %s, %s, %s, 10, 0.075, true, true, 1000000)",
    "PP": "SELECT recording_id, start_sample, distance, metadata FROM morlet_search_envelope_probes(%s, %s, %s, %s, %s)",
    "PI": "SELECT recording_id, start_sample, distance, metadata FROM morlet_search_envelope_probes_pi(%s, %s, %s, %s, %s)",
}
COUNT = """SELECT count(*) FROM public.r2_envelopes e JOIN public.r2 r ON r.recording_id = e.recording_id
           WHERE e.window_size = 4096 AND r.sample_rate_hz = 100 AND r.channel = 'HHZ' AND r.unit = 'counts' AND r.metadata @> %s"""
# SS3b: complete windows on the stride grid from each eligible recording's checkpoint (cost ~ recordings, not windows)
COUNT_FAST = """SELECT coalesce(sum(greatest(0, ((checkpoint->>'end')::bigint - 4096) / 2048 + 1)), 0) FROM public.r2
                WHERE sample_rate_hz = 100 AND channel = 'HHZ' AND unit = 'counts' AND metadata @> %s"""


def dsn(db):
    return f"host=localhost port=28817 dbname={db}"


def load_queries(path):
    d = json.loads(Path(path).read_text())
    return [(q["recording_id"], int(q["start"]), q.get("type")) for q in d["queries"]]


def level_filter(level, rid):
    station = rid.split(".")[0]
    return {"L1": {"station": station, "day": 1}, "L2": {"station": station, "tiers": [2]}, "L3": {"station": station},
            "L4": {"tiers": [2]}, "L5": {}}[level]


def satisfies(md, flt):
    for k, v in flt.items():
        if isinstance(v, list):
            if not set(v) <= set(md.get(k, [])):
                return False
        elif md.get(k) != v:
            return False
    return True


def call(conn, arm, rid, s, flt):
    rows = conn.execute(SQL[arm], (COLL, rid, s, s + W, Jsonb(flt))).fetchall()
    return [(r, int(st)) for r, st, _, _ in rows], sum(not satisfies(md, flt) for _, _, _, md in rows)


def pi_plan(conn, rid, s, flt):
    q = conn.execute("SELECT morlet_envelope(samples) FROM morlet_read_interval(%s, %s, %s, %s)", (COLL, rid, s, s + W)).fetchone()[0]
    ids = conn.execute("SELECT array_agg(recording_id) FROM public.r2 WHERE metadata @> %s", (Jsonb(flt),)).fetchone()[0]
    with conn.transaction():
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        conn.execute("SET LOCAL hnsw.ef_search = 100")
        plan = [r[0] for r in conn.execute("""EXPLAIN SELECT recording_id, start_sample FROM public.r2_probes_4096
                                             WHERE recording_id = ANY(%s) ORDER BY v <#> morlet_envelope_probe(%s::float8[], 0)::halfvec LIMIT 50""",
                                           (ids, q)).fetchall()]
        n = len(conn.execute("""SELECT 1 FROM public.r2_probes_4096 WHERE recording_id = ANY(%s)
                                 ORDER BY v <#> morlet_envelope_probe(%s::float8[], 0)::halfvec LIMIT 50""", (ids, q)).fetchall())
    return {"hnsw_index_scan": any("r2_probes_4096_v_idx" in p or ("Index Scan" in p and "hnsw" in p.lower()) or "probes_4096_v" in p for p in plan),
            "plan": "\n".join(plan), "candidates_lag0": n}


def stats(recalls, ms):
    return {"recall_mean": float(np.mean(recalls)), "recall_min": float(np.min(recalls)),
            "recall_p5": float(np.percentile(recalls, 5)), "p50_ms": float(np.percentile(ms, 50)),
            "p95_ms": float(np.percentile(ms, 95))}


def measure(db, qpath, out):
    queries = load_queries(qpath)
    res = {"database": db, "levels": {}}
    with psycopg.connect(dsn(db), autocommit=True) as conn:
        for level in LEVELS:
            lv = {"plans_PI": pi_plan(conn, queries[0][0], queries[0][1], level_filter(level, queries[0][0]))}
            ref, lists, ms, viol, counts, count_ms = {}, {a: {} for a in SQL}, {a: [] for a in SQL}, {a: 0 for a in SQL}, [], []
            for timed in (0, 1):
                for qi, (rid, s, _) in enumerate(queries):
                    flt = level_filter(level, rid)
                    if timed:
                        t = time.perf_counter()
                        counts.append(conn.execute(COUNT, (Jsonb(flt),)).fetchone()[0])
                        count_ms.append(1e3 * (time.perf_counter() - t))
                    for arm in SQL:
                        t = time.perf_counter()
                        got, bad = call(conn, arm, rid, s, flt)
                        if timed:
                            ms[arm].append(1e3 * (time.perf_counter() - t))
                            lists[arm][qi] = got
                            viol[arm] += bad
            for arm in SQL:
                rec = [len(set(lists[arm][q]) & set(lists["E"][q])) / max(len(lists["E"][q]), 1) for q in lists["E"]]
                lv[arm] = stats(rec, ms[arm]) | {"filter_violations": viol[arm], "per_query_recall": rec}
            lv["eligible_windows"] = {"mean": float(np.mean(counts)), "min": int(min(counts)), "max": int(max(counts))}
            lv["count_ms"] = {"p50": float(np.percentile(count_ms, 50)), "p95": float(np.percentile(count_ms, 95))}
            lv["E_lists"] = {q: lists["E"][q] for q in lists["E"]}
            res["levels"][level] = lv
            print(level, json.dumps({a: {k: round(v, 3) for k, v in lv[a].items() if k in ("recall_mean", "p50_ms", "p95_ms")} for a in SQL}),
                  "eligible", lv["eligible_windows"]["mean"], "PI plan", lv["plans_PI"]["hnsw_index_scan"], lv["plans_PI"]["candidates_lag0"], flush=True)
    json.dump(res, open(out / f"ss3_{db}.json", "w"), indent=1)


def decide(out):
    r = json.loads((out / "ss3_morlet_r2.json").read_text())["levels"]
    mean = {a: float(np.mean([r[l][a]["recall_mean"] for l in LEVELS])) for a in ("PP", "PI")}
    p95 = {a: float(np.mean([r[l][a]["p95_ms"] for l in LEVELS])) for a in ("PP", "PI")}
    A = max(("PP", "PI"), key=lambda a: (mean[a], -p95[a]))
    pts = sorted((r[l]["eligible_windows"]["mean"], r[l]["E"]["p95_ms"]) for l in LEVELS)
    if pts[0][1] > 100:
        T = 0.0
    elif pts[-1][1] <= 100:
        T = pts[-1][0]
    else:
        T = next(x0 + (100 - y0) * (x1 - x0) / (y1 - y0) for (x0, y0), (x1, y1) in zip(pts, pts[1:]) if y0 <= 100 < y1)
    res = {"A": A, "T": T, "mean_recall_over_levels": mean, "mean_p95_over_levels": p95, "E_p95_points": pts}
    json.dump(res, open(out / "ss3_decision.json", "w"), indent=1)
    print(json.dumps(res))


def route(db, qpath, out, count_sql=None, tag=""):
    d = json.loads((out / "ss3_decision.json").read_text())
    A, T = d["A"], d["T"]
    queries = load_queries(qpath)
    base = json.loads((out / f"ss3_{db}.json").read_text())["levels"]
    res = {"database": db, "A": A, "T": T, "levels": {}}
    with psycopg.connect(dsn(db), autocommit=True) as conn:
        for level in LEVELS:
            ms, rec, paths, viol, mistakes = [], [], [], 0, 0
            for timed in (0, 1):
                for qi, (rid, s, _) in enumerate(queries):
                    flt = level_filter(level, rid)
                    t = time.perf_counter()
                    n = conn.execute(count_sql or COUNT, (Jsonb(flt),)).fetchone()[0]
                    arm = "E" if n <= T else A
                    got, bad = call(conn, arm, rid, s, flt)
                    if timed:
                        ms.append(1e3 * (time.perf_counter() - t))
                        ref = [tuple(x) for x in base[level]["E_lists"][str(qi)]]
                        r = len(set(got) & set(ref)) / max(len(ref), 1)
                        rec.append(r)
                        paths.append(arm)
                        viol += bad
                        mistakes += arm != "E" and r < 0.9
            st = stats(rec, ms)
            bl = base[level]
            bound = 1.25 * min(bl["E"]["p95_ms"], bl[A]["p95_ms"])
            st.update({"paths": {p: paths.count(p) for p in set(paths)}, "filter_violations": viol, "routing_mistakes": mistakes,
                       "p95_bound": bound, "dependable": st["recall_mean"] >= 0.95 and viol == 0 and st["p95_ms"] <= bound})
            res["levels"][level] = st
            print(level, json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in st.items()}), flush=True)
    res["dependable"] = all(v["dependable"] for v in res["levels"].values())
    json.dump(res, open(out / f"ss3{tag}_route_{db}.json", "w"), indent=1)
    print("dependable:", res["dependable"])


def rls(db, qpath, out):
    """RLS on the recordings table limits a role to one station; every strategy must return only that station."""
    queries = load_queries(qpath)
    rid, s, _ = next(q for q in queries if q[0].startswith(queries[0][0].split(".")[0]))
    station = rid.split(".")[0]
    res = {"station": station}
    with psycopg.connect(dsn(db), autocommit=True) as conn:
        conn.execute("DROP ROLE IF EXISTS ss3_reader")
        conn.execute("CREATE ROLE ss3_reader")
        conn.execute("GRANT USAGE ON SCHEMA public TO ss3_reader")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO ss3_reader")
        conn.execute("ALTER TABLE public.r2 ENABLE ROW LEVEL SECURITY")
        conn.execute(f"CREATE POLICY ss3_station ON public.r2 FOR SELECT TO ss3_reader USING (metadata->>'station' = '{station}')")
        try:
            conn.execute("SET ROLE ss3_reader")
            for arm in SQL:
                got = conn.execute(SQL[arm], (COLL, rid, s, s + W, Jsonb({}))).fetchall()
                res[arm] = {"rows": len(got), "other_station_rows": sum(not r.startswith(station + ".") for r, _, _, _ in got)}
            other = next(q for q in queries if not q[0].startswith(station + "."))
            try:
                conn.execute(SQL["E"], (COLL, other[0], other[1], other[1] + W, Jsonb({}))).fetchall()
                res["hidden_source"] = "readable (unexpected)"
            except psycopg.Error as e:
                res["hidden_source"] = "rejected: " + str(e).splitlines()[0]
        finally:
            conn.execute("RESET ROLE")
            conn.execute("DROP POLICY IF EXISTS ss3_station ON public.r2")
            conn.execute("ALTER TABLE public.r2 DISABLE ROW LEVEL SECURITY")
            conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ss3_reader")
            conn.execute("REVOKE USAGE ON SCHEMA public FROM ss3_reader")
            conn.execute("DROP ROLE ss3_reader")
    res["ok"] = all(res[a]["other_station_rows"] == 0 and res[a]["rows"] > 0 for a in SQL) and res["hidden_source"].startswith("rejected")
    json.dump(res, open(out / f"ss3_rls_{db}.json", "w"), indent=1)
    print(json.dumps(res))


def main():
    cmd = sys.argv[1]
    if cmd == "decide":
        return decide(Path(sys.argv[2]))
    db, qpath, out = sys.argv[2], sys.argv[3], Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    if cmd == "route_fast":
        return route(db, qpath, out, COUNT_FAST, "b")
    {"measure": measure, "route": route, "rls": rls}[cmd](db, qpath, out)


if __name__ == "__main__":
    main()
