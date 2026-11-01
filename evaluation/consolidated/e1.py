"""E1: consolidated retrieval comparison on one frozen implementation (PROTOCOL.md).

Usage (one database per call):
  .venv-seismic/bin/python -I evaluation/consolidated/e1.py <dbname> <queries.json> <out.json> <build-checkout>
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bounded_lag"))
import probes as P  # noqa: E402

COLL, ENV, PROBES, W, L, TOP = "public.r2", "public.r2_envelopes", "public.r2_probes_4096", 4096, 30, 10
PROBE_INDEX = "r2_probes_4096_v_idx"
TIMED = ["EX-2", "EX-8", "PR", "Z-650", "Z-1000"]
EXACT_SQL = ("SELECT recording_id, start_sample FROM morlet_search_envelope_interval(%s, %s, %s, %s, '{}'::jsonb, 10, 0.075,"
             " true, true, 1000000)")
PROBE_SQL = "SELECT recording_id, start_sample FROM morlet_search_envelope_probes(%s, %s, %s, %s, '{}'::jsonb, 10)"
RERANK_SQL = f"""SELECT e.recording_id, e.start_sample, morlet_envelope_distance(%s::float8[], e.envelope, {L}) AS d
  FROM unnest(%s::text[], %s::bigint[]) AS c(recording_id, start_sample)
  JOIN {ENV} e ON e.recording_id = c.recording_id AND e.start_sample = c.start_sample AND e.window_size = {W}
  ORDER BY d, e.recording_id, e.start_sample"""


def loadavg():
    return list(os.getloadavg())


def environment(conn, build):
    commit = subprocess.run(["git", "-C", build, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", build, "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    pkglib = conn.execute("SELECT setting FROM pg_config WHERE name = 'PKGLIBDIR'").fetchone()[0]
    lib = next(p for p in (Path(pkglib) / "pg_morlet.dylib", Path(pkglib) / "pg_morlet.so") if p.exists())
    hw = {k: subprocess.run(["sysctl", "-n", k], capture_output=True, text=True).stdout.strip()
          for k in ("hw.model", "hw.ncpu", "hw.memsize")}
    return {
        "build_commit": commit, "build_dirty": bool(dirty),
        "library": str(lib), "library_sha256": hashlib.sha256(lib.read_bytes()).hexdigest(),
        "extensions": dict(conn.execute("SELECT extname, extversion FROM pg_extension").fetchall()),
        "server_version": conn.execute("SHOW server_version").fetchone()[0],
        "non_default_settings": {n: s for n, s in conn.execute(
            "SELECT name, setting FROM pg_settings WHERE source NOT IN ('default', 'override') ORDER BY name").fetchall()},
        "hardware": hw, "loadavg_start": loadavg(),
    }


def query_env(conn, rid, s):
    return np.asarray(conn.execute(f"SELECT morlet_envelope(samples) FROM morlet_read_interval(%s, %s, %s, %s)",
                                   (COLL, rid, s, s + W)).fetchone()[0], dtype=np.float64)


def rerank(conn, q, cand):
    rows = conn.execute(RERANK_SQL, (q.tolist(), [c[0] for c in cand], [int(c[1]) for c in cand])).fetchall()
    return rows


def arm_exact(conn, rid, s, workers):
    conn.execute(f"SET max_parallel_workers_per_gather = {workers}")
    try:
        return [(r, int(st)) for r, st in conn.execute(EXACT_SQL, (COLL, rid, s, s + W)).fetchall()], {}
    finally:
        conn.execute("RESET max_parallel_workers_per_gather")


def arm_probe(conn, rid, s):
    return [(r, int(st)) for r, st in conn.execute(PROBE_SQL, (COLL, rid, s, s + W)).fetchall()], {}


def arm_zero(conn, rid, s, k):
    """Zero-lag candidates from the same index as the probes, re-ranked with the product scorer."""
    q = query_env(conn, rid, s)
    eligible = conn.execute(f"SELECT array_agg(recording_id) FROM {COLL} WHERE sample_rate_hz = 100 AND channel = 'HHZ'"
                            " AND unit = 'counts' AND metadata @> '{}'::jsonb").fetchone()[0]
    probe = conn.execute("SELECT morlet_envelope_probe(%s::float8[], 0)", (q.tolist(),)).fetchone()[0]
    conn.execute(f"SET hnsw.ef_search = {k}")
    conn.execute("SET hnsw.iterative_scan = relaxed_order")
    try:
        cand = conn.execute(f"SELECT recording_id, start_sample FROM {PROBES} WHERE recording_id = ANY(%s)"
                            f" ORDER BY v <#> %s::float8[]::halfvec LIMIT {k}", (eligible, probe)).fetchall()
    finally:
        conn.execute("RESET hnsw.ef_search")
        conn.execute("RESET hnsw.iterative_scan")
    top = P.suppress(rerank(conn, q, cand), rid, s)
    return top, {"rows_returned": len(cand)}


ARMS = {"EX-2": lambda c, r, s: arm_exact(c, r, s, 2), "EX-8": lambda c, r, s: arm_exact(c, r, s, 8),
        "PR": arm_probe, "Z-650": lambda c, r, s: arm_zero(c, r, s, 650), "Z-1000": lambda c, r, s: arm_zero(c, r, s, 1000)}


def idx_scans(conn):
    conn.execute("SELECT pg_stat_clear_snapshot()")
    return conn.execute("SELECT idx_scan FROM pg_stat_user_indexes WHERE indexrelname = %s", (PROBE_INDEX,)).fetchone()[0]


def plans(conn, rid, s):
    out = {}
    q = query_env(conn, rid, s)
    probe = conn.execute("SELECT morlet_envelope_probe(%s::float8[], 0)", (q.tolist(),)).fetchone()[0]
    eligible = conn.execute(f"SELECT array_agg(recording_id) FROM {COLL}").fetchone()[0]
    conn.execute("SET hnsw.ef_search = 650")
    conn.execute("SET hnsw.iterative_scan = relaxed_order")
    plan = "\n".join(r[0] for r in conn.execute(
        f"EXPLAIN SELECT recording_id, start_sample FROM {PROBES} WHERE recording_id = ANY(%s)"
        f" ORDER BY v <#> %s::float8[]::halfvec LIMIT 650", (eligible, probe)).fetchall())
    conn.execute("RESET hnsw.ef_search")
    conn.execute("RESET hnsw.iterative_scan")
    out["Z"] = {"index_scan": f"Index Scan using {PROBE_INDEX}" in plan, "plan": plan}
    # index use per call, from the statistics counter (stats are flushed at transaction end; wait for them)
    for arm in ("PR", "EX-2"):
        before = idx_scans(conn)
        ARMS[arm](conn, rid, s)
        time.sleep(1.2)
        out[arm] = {"probe_index_scans_per_call": idx_scans(conn) - before}
    # nested plan of the exact statement
    notices = []
    conn.add_notice_handler(lambda d: notices.append(d.message_primary or ""))
    conn.execute("LOAD 'auto_explain'")
    conn.execute("SET auto_explain.log_min_duration = 0")
    conn.execute("SET auto_explain.log_nested_statements = on")
    conn.execute("SET client_min_messages = log")
    ARMS["EX-8"](conn, rid, s)
    conn.execute("SET auto_explain.log_min_duration = -1")
    conn.execute("RESET client_min_messages")
    text = "\n".join(notices)
    out["EX-8"] = {"parallel_seq_scan_on_envelopes": "Parallel Seq Scan on r2_envelopes" in text,
                   "workers_planned": sorted({ln.strip() for ln in text.splitlines() if "Workers Planned" in ln}),
                   "plan_excerpt": text[:4000]}
    return out


def gallery(conn):
    rows = conn.execute(f"SELECT recording_id, start_sample, envelope FROM {ENV} WHERE window_size = {W}"
                        " ORDER BY recording_id, start_sample").fetchall()
    ids = [(r, int(s)) for r, s, _ in rows]
    G = np.stack([P.decode(e) for _, _, e in rows]).astype(np.float32)
    return ids, G


def offline(conn, rid, s, ids, G, keys, index, ref):
    q = query_env(conn, rid, s)
    Q61 = P.probe_matrix(q, P.GRIDS["G61"]).astype(np.float32)
    S61 = G @ Q61
    lstar = [int(P.GRIDS["G61"][int(np.argmax(S61[index[a]]))]) for a in ref]
    out, cands = {}, {}
    # Deviation 1: for each reference item, the correlation kept by the best 13-grid probe relative to its best lag,
    # and its rank (1 = best) among all windows in that probe's exact ordering.
    gcols = [P.GRIDS["G61"].index(l) for l in P.GRIDS["G13"]]
    grid_items = []
    for a in ref:
        i = index[a]
        best = float(S61[i].max())
        c = max(gcols, key=lambda c: S61[i, c])
        rank = int((S61[:, c] > S61[i, c]).sum()) + 1
        grid_items.append({"kept": float(S61[i, c]) / best if best > 0 else None, "rank_in_probe": rank})

    def union(S, cols, k=50):
        seen, cand = set(), []
        for c in cols:
            for i in P.topk(S[:, c], k, keys):
                if i not in seen:
                    seen.add(i)
                    cand.append(ids[i])
        return cand

    col = {l: i for i, l in enumerate(P.GRIDS["G61"])}
    cands["PX-61"] = union(S61, range(61))
    cands["PX-13"] = union(S61, [col[l] for l in P.GRIDS["G13"]])
    cands["PX-7"] = union(S61, [col[l] for l in P.GRIDS["G7"]])
    Q13h = P.probe_matrix(q, P.GRIDS["G13"]).astype(np.float16).astype(np.float32)
    cands["PQ-13"] = union(G @ Q13h, range(len(P.GRIDS["G13"])))
    cands["EZ"] = [ids[i] for i in P.topk(S61[:, col[0]], len(cands["PX-13"]), keys)]
    for arm, cand in cands.items():
        out[arm] = {"top": P.suppress(rerank(conn, q, cand), rid, s), "cands": len(cand)}
    return out, lstar, grid_items


def stats(xs):
    a = np.asarray(xs, dtype=float)
    return {"p50": float(np.percentile(a, 50)), "p95": float(np.percentile(a, 95)), "p99": float(np.percentile(a, 99)),
            "max": float(a.max()), "mean": float(a.mean()), "n": int(len(a))}


def recall_summary(per, arm, types, lstars, refs):
    r = [per[q]["recall"][arm] for q in per]
    bands = {"0-5": [0, 0], "6-15": [0, 0], "16-30": [0, 0]}
    for q in per:
        got = set(map(tuple, per[q]["top"][arm]))
        for item, l in zip(refs[q], lstars[q]):
            b = "0-5" if abs(l) <= 5 else ("6-15" if abs(l) <= 15 else "16-30")
            bands[b][0] += tuple(item) in got
            bands[b][1] += 1
    by_type = {t: float(np.mean([per[q]["recall"][arm] for q in per if types[q] == t])) for t in set(types.values())}
    return {"mean": float(np.mean(r)), "min": float(min(r)), "quartiles": [float(x) for x in np.percentile(r, [25, 50, 75])],
            "below_0.9": int(sum(x < 0.9 for x in r)), "below_0.8": int(sum(x < 0.8 for x in r)),
            "by_lag_band": {b: {"recall": v[0] / v[1] if v[1] else None, "items": v[1]} for b, v in bands.items()},
            "by_type": by_type}


def main(db, queries_path, out_path, build):
    queries = [(q["recording_id"], int(q["start"]), q["type"]) for q in json.loads(Path(queries_path).read_text())["queries"]]
    with psycopg.connect(f"host=localhost port=28817 dbname={db}", autocommit=True) as conn:
        res = {"database": db, "queries": queries_path, "environment": environment(conn, build)}
        res["gallery_windows"] = conn.execute(f"SELECT count(*) FROM {ENV} WHERE window_size = {W}").fetchone()[0]
        res["plans"] = plans(conn, *queries[0][:2])
        print("plans", {k: {kk: vv for kk, vv in v.items() if kk not in ("plan", "plan_excerpt")} for k, v in res["plans"].items()},
              flush=True)

        for rid, s, _ in queries[:5]:                                   # warm-up, untimed
            for arm in TIMED:
                ARMS[arm](conn, rid, s)

        per, timings, rounds = {}, {a: [] for a in TIMED}, []
        for rnd in (1, 2):
            la0 = loadavg()
            order = queries if rnd == 1 else list(reversed(queries))
            for qi, (rid, s, typ) in enumerate(order):
                key = f"{rid}:{s}"
                rec = per.setdefault(key, {"type": typ, "top": {}, "ms": {}, "work": {}, "round2_differs": []})
                k = qi % len(TIMED)
                for arm in TIMED[k:] + TIMED[:k]:
                    t = time.perf_counter()
                    top, work = ARMS[arm](conn, rid, s)
                    ms = 1e3 * (time.perf_counter() - t)
                    timings[arm].append(ms)
                    rec["ms"].setdefault(arm, []).append(ms)
                    if rnd == 1:
                        rec["top"][arm], rec["work"][arm] = top, work
                    elif top != rec["top"][arm]:
                        rec["round2_differs"].append(arm)
                if qi % 20 == 0:
                    print("round", rnd, qi, {a: round(rec["ms"][a][-1]) for a in TIMED}, flush=True)
            rounds.append({"round": rnd, "loadavg_start": la0, "loadavg_end": loadavg()})
            if rnd == 1:
                bad = [q for q in per if per[q]["top"]["EX-2"] != per[q]["top"]["EX-8"]]
                if bad:
                    res["abort"] = f"EX-2 and EX-8 differ for {len(bad)} queries"
                    Path(out_path).write_text(json.dumps(res, indent=1))
                    sys.exit(res["abort"])
        res["rounds"] = rounds

        ids, G = gallery(conn)
        keys = np.arange(len(ids))
        index = {k: i for i, k in enumerate(ids)}
        lstars, refs = {}, {}
        for rid, s, _ in queries:
            key = f"{rid}:{s}"
            ref = [tuple(x) for x in per[key]["top"]["EX-2"]]
            off, lstar, per[key]["grid_items"] = offline(conn, rid, s, ids, G, keys, index, ref)
            refs[key], lstars[key] = ref, lstar
            for arm, v in off.items():
                per[key]["top"][arm] = v["top"]
                per[key]["work"][arm] = {"cands": v["cands"]}
        del G

        arms = TIMED + ["PX-61", "PX-13", "PX-7", "PQ-13", "EZ"]
        for key, rec in per.items():
            ref = set(refs[key])
            rec["recall"] = {a: len(ref & set(map(tuple, rec["top"][a]))) / TOP for a in arms}
            rec["lstar"] = lstars[key]
        types = {k: v["type"] for k, v in per.items()}
        res["summary"] = {a: {"recall": recall_summary(per, a, types, lstars, refs),
                              **({"latency_ms": stats(timings[a])} if a in timings else {})} for a in arms}
        res["work"] = {
            "EX-2": "scores every eligible window; 2 parallel workers", "EX-8": "scores every eligible window; 8 workers",
            "PR": "13 HNSW scans (ef_search 100, LIMIT 50 each), re-rank of the union",
            "Z-650": "1 HNSW scan (ef_search 650, LIMIT 650), re-rank of 650", "Z-1000": "1 HNSW scan (ef_search 1000), re-rank of 1000",
        }
        res["offline_union_sizes"] = {a: stats([per[q]["work"][a]["cands"] for q in per]) for a in ("PX-61", "PX-13", "PX-7", "PQ-13", "EZ")}
        gi = [it for q in per for it in per[q]["grid_items"]]
        kept = [it["kept"] for it in gi if it["kept"] is not None]
        ranks = [it["rank_in_probe"] for it in gi]
        res["grid_items"] = {"items": len(gi), "kept": {"min": float(min(kept)), "p1": float(np.percentile(kept, 1)),
                                                         "p5": float(np.percentile(kept, 5)), "median": float(np.median(kept))},
                             "rank_in_best_grid_probe": {"median": float(np.median(ranks)), "p95": float(np.percentile(ranks, 95)),
                                                         "max": int(max(ranks)), "beyond_50": int(sum(r > 50 for r in ranks))}}
        hardest = sorted(per, key=lambda q: per[q]["recall"]["PR"])[:10]
        res["hardest_PR"] = [{"query": q, "type": per[q]["type"], "recall": per[q]["recall"]} for q in hardest]
        res["round2_differs"] = {q: per[q]["round2_differs"] for q in per if per[q]["round2_differs"]}

        sizes = {}
        for name, rel in (("raw_chunks", "public.r2_chunks"), ("envelopes", ENV), ("probe_table", PROBES)):
            sizes[name] = conn.execute("SELECT pg_table_size(%s::regclass) + pg_indexes_size(%s::regclass)", (rel, rel)).fetchone()[0]
        sizes["probe_index"] = conn.execute("SELECT pg_relation_size(%s::regclass)", (PROBE_INDEX,)).fetchone()[0]
        sizes["probe_table_heap_and_pkey"] = sizes["probe_table"] - sizes["probe_index"]
        res["sizes_bytes"] = sizes
        conn.execute("SET maintenance_work_mem = '4GB'")
        la = loadavg()
        t = time.perf_counter()
        conn.execute(f"REINDEX INDEX {PROBE_INDEX}")
        res["probe_index_rebuild"] = {"seconds": time.perf_counter() - t, "maintenance_work_mem": "4GB",
                                      "loadavg_before": la, "loadavg_after": loadavg()}
        res["environment"]["loadavg_end"] = loadavg()

        H = res["summary"]
        strongest = min(("EX-2", "EX-8"), key=lambda a: H[a]["latency_ms"]["p95"])
        res["verdict"] = {
            "strongest_exact": strongest,
            "H1": H["PR"]["recall"]["mean"] >= 0.95 and H["PR"]["latency_ms"]["p95"] <= H[strongest]["latency_ms"]["p95"] / 3,
            "H2": (H["PX-13"]["recall"]["mean"] >= H["PR"]["recall"]["mean"] >= max(H["Z-650"]["recall"]["mean"], H["Z-1000"]["recall"]["mean"])
                   and H["PR"]["recall"]["mean"] - max(H["Z-650"]["recall"]["mean"], H["Z-1000"]["recall"]["mean"]) >= 0.2),
        }
        res["per_query"] = per
        Path(out_path).write_text(json.dumps(res, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
        print(json.dumps({a: {"recall": round(H[a]["recall"]["mean"], 3), "min": H[a]["recall"]["min"],
                              "p95": round(H[a].get("latency_ms", {}).get("p95", float("nan")), 1)} for a in arms}), flush=True)
        print("verdict", res["verdict"], flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:5])
