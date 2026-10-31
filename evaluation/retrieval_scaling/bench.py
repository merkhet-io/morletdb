#!/usr/bin/env python3
"""R2 benchmark (PROTOCOL.md): exact pg_morlet envelope search (A) against an HNSW zero-lag prefilter with
exact lag re-ranking (H-100, H-1000) and zero-lag only (Z), per gallery tier.

  MORLET_DSN="host=localhost port=28817 dbname=morlet_r2" \
  .venv-seismic/bin/python -I evaluation/retrieval_scaling/bench.py evaluation/retrieval_scaling/results
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Json

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_r2")
COLL, WINDOW, TOP, LAG_FRACTION, LAG = "public.r2", 4096, 10, 0.075, 30
KS, TIERS, SEED = (100, 1000), (0, 1, 2, 3), 2025


def pick_queries(conn):
    """50 random day-1 windows (seed 2025) and the 50 highest-RMS non-overlapping day-1 windows."""
    rows = conn.execute("""SELECT e.recording_id, e.start_sample FROM public.r2_envelopes e JOIN public.r2 r USING (recording_id)
                           WHERE (r.metadata->>'day')::int = 1 AND e.window_size = %s
                           ORDER BY e.recording_id, e.start_sample""", (WINDOW,)).fetchall()
    rng = np.random.default_rng(SEED)
    rand = [rows[i] for i in rng.choice(len(rows), 50, replace=False)]
    rms = []
    for rid, s in rows:
        x = np.asarray(conn.execute("SELECT samples FROM morlet_read_interval(%s, %s, %s, %s)",
                                    (COLL, rid, s, s + WINDOW)).fetchone()[0])
        rms.append(float(np.sqrt(((x - x.mean()) ** 2).mean())))
    energetic, taken = [], set(map(tuple, rand))
    for i in np.argsort(rms)[::-1]:
        rid, s = rows[i]
        if any(r == rid and abs(s - t) < WINDOW for r, t in [*taken, *energetic]):
            continue
        energetic.append((rid, s))
        if len(energetic) == 50:
            break
    return [("random", r, s) for r, s in rand] + [("energetic", r, s) for r, s in energetic]


def suppress(ranked, rid, start):
    """pg_morlet's exclusion and greedy overlap suppression on (recording, start, distance) in rank order."""
    out = []
    for r, s, _ in ranked:
        if r == rid and s < start + WINDOW and s + WINDOW > start:
            continue
        if any(r == pr and s < ps + WINDOW and s + WINDOW > ps for pr, ps in out):
            continue
        out.append((r, s))
        if len(out) == TOP:
            break
    return out


def arm_a(conn, rid, s, tier):
    rows = conn.execute("SELECT recording_id, start_sample FROM morlet_search_envelope_interval(%s, %s, %s, %s, %s, %s, %s, true, true, 1000000)",
                        (COLL, rid, s, s + WINDOW, Json({"tiers": [tier]}), TOP, LAG_FRACTION)).fetchall()
    return [tuple(r) for r in rows]


def stage1(conn, rid, s, tier, k):
    conn.execute(f"SET hnsw.ef_search = {max(k, 100)}")
    return conn.execute("""SELECT h.recording_id, h.start_sample, h.v <=> q.v AS d
                           FROM public.r2_hv h, (SELECT v FROM public.r2_hv WHERE recording_id = %s AND start_sample = %s) q
                           WHERE h.tiers @> ARRAY[%s] ORDER BY h.v <=> q.v LIMIT %s""", (rid, s, tier, k)).fetchall()


def arm_h(conn, rid, s, tier, k):
    cand = stage1(conn, rid, s, tier, k)
    rows = conn.execute("""WITH q AS (SELECT morlet_envelope(samples) AS env FROM morlet_read_interval(%s, %s, %s, %s)),
                                c AS (SELECT * FROM unnest(%s::text[], %s::bigint[]) AS c(recording_id, start_sample))
                           SELECT e.recording_id, e.start_sample, morlet_envelope_distance(q.env, e.envelope, %s) AS d
                           FROM c JOIN public.r2_envelopes e ON e.recording_id = c.recording_id AND e.start_sample = c.start_sample
                                AND e.window_size = %s, q
                           ORDER BY d, e.recording_id, e.start_sample""",
                        (COLL, rid, s, s + WINDOW, [c[0] for c in cand], [c[1] for c in cand], LAG, WINDOW)).fetchall()
    return suppress(rows, rid, s), cand


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, 1e3 * (time.perf_counter() - t)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("SET hnsw.iterative_scan = relaxed_order")
        queries = pick_queries(conn)
        res = {"queries": [{"type": t, "recording_id": r, "start": int(s)} for t, r, s in queries], "tiers": {}}
        for tier in TIERS:
            runs = {}
            for timed_pass in (False, True):   # warm-up, then the timed pass
                for qi, (_, rid, s) in enumerate(queries):
                    a, ta = timed(lambda: arm_a(conn, rid, s, tier))
                    rec = {"A": a, "ms": {"A": ta}}
                    for k in KS:
                        (h, cand), th = timed(lambda: arm_h(conn, rid, s, tier, k))
                        rec[f"H-{k}"], rec["ms"][f"H-{k}"] = h, th
                        if k == max(KS):
                            rec["Z"] = suppress([(r, st, d) for r, st, d in cand], rid, s)
                    runs[qi] = rec
            recall = {arm: [len(set(map(tuple, runs[q][arm])) & set(map(tuple, runs[q]["A"]))) / max(len(runs[q]["A"]), 1)
                            for q in runs] for arm in ["Z"] + [f"H-{k}" for k in KS]}
            types = np.array([t for t, _, _ in queries])
            ms = {arm: np.array([runs[q]["ms"][arm] for q in runs]) for arm in ["A"] + [f"H-{k}" for k in KS]}
            res["tiers"][tier] = {
                "recall_at_10": {arm: float(np.mean(v)) for arm, v in recall.items()},
                "recall_by_type": {arm: {t: float(np.mean(np.array(v)[types == t])) for t in ("random", "energetic")}
                                   for arm, v in recall.items()},
                "latency_ms": {arm: {"p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95))} for arm, v in ms.items()},
                "a_result_size": [len(runs[q]["A"]) for q in runs],
                "per_query": {q: {"A": runs[q]["A"], **{f"H-{k}": runs[q][f"H-{k}"] for k in KS}, "Z": runs[q]["Z"],
                                  "ms": runs[q]["ms"]} for q in runs}}
            t = res["tiers"][tier]
            print(tier, json.dumps({"recall": t["recall_at_10"], "latency": t["latency_ms"]}), flush=True)
        last = res["tiers"][max(TIERS)]
        ok = [k for k in KS if last["recall_at_10"][f"H-{k}"] >= 0.95
              and last["latency_ms"][f"H-{k}"]["p95"] <= 0.1 * last["latency_ms"]["A"]["p95"]]
        res["decision"] = {"prefilter_supported": bool(ok), "qualifying_K": ok}
    json.dump(res, open(out / "bench.json", "w"), indent=1)
    print(json.dumps(res["decision"]))


if __name__ == "__main__":
    main()
