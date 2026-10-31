#!/usr/bin/env python3
"""BL1 Stage B (STAGE_B.md): replay R2's day-1 recordings into morlet_bl1s with synchronous (S) or background-worker
(W) maintenance of envelopes, the halfvec side table and its inner-product HNSW index; measure throughput, delays,
backlog, query latency and same-snapshot recall during ingestion; failure drills.

  MORLET_DSN="host=localhost port=28817 dbname=morlet_bl1s" \
  .venv-seismic/bin/python -I evaluation/bounded_lag/stageb.py all data/cx-scale evaluation/bounded_lag/results/stageb
"""
import json
import os
import random
import resource
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bl1  # noqa: E402
import probes as P  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_bl1s")
COLL, CHUNK, STRIDE, B = "public.r2", 4096, 2048, 64
STATIONS, DAY, PACE = ("PB01", "PB02", "PB06", "PSGCX"), 1, 20_000
QUERY_EVERY, SAMPLE_EVERY = 0.25, 0.5

MAINTAIN = """INSERT INTO public.r2_hv
              SELECT e.recording_id, e.start_sample, ARRAY[3], morlet_envelope_decode(e.envelope)::halfvec(410)
              FROM public.r2_envelopes e
              WHERE e.recording_id = %s AND e.window_size = 4096
                AND e.start_sample > coalesce((SELECT max(start_sample) FROM public.r2_hv WHERE recording_id = %s), -1)
              ON CONFLICT DO NOTHING"""


def connect():
    return psycopg.connect(DSN, autocommit=True)


def recordings(root):
    out = []
    for m in json.loads((root / "manifest.json").read_text()):
        if m["station"] in STATIONS and m["day"] == DAY:
            for i, seg in enumerate(m["segments"]):
                out.append((f"{m['station']}.d{DAY:02d}.{i}", m["station"], seg, root / seg["file"]))
    return out


def reset(conn, recs):
    conn.execute("DROP TABLE IF EXISTS public.r2_hv, public.r2_envelopes, public.r2_segments, public.r2_chunks, public.r2 CASCADE")
    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute("SELECT morlet_create_stream_collection('public', 'r2')")
    conn.execute("SELECT morlet_enable_envelopes(%s)", (COLL,))
    conn.execute("""CREATE TABLE public.r2_hv (recording_id text NOT NULL, start_sample bigint NOT NULL, tiers int[] NOT NULL,
                    v halfvec(410) NOT NULL, PRIMARY KEY (recording_id, start_sample))""")
    conn.execute("CREATE INDEX r2_hv_ip ON public.r2_hv USING hnsw (v halfvec_ip_ops) WITH (m = 16, ef_construction = 64)")
    for rid, st, seg, _ in recs:
        conn.execute("SELECT morlet_start_recording(%s, %s, %s, 'HHZ', 'counts', %s, 512, 256, 'zscore', 'none', 1::float8)",
                     (COLL, rid, seg["rate"], Jsonb({"station": st, "day": DAY, "tiers": [3]})))
    conn.execute("CHECKPOINT")


def maintain(conn, rid):
    """One maintenance step (inside the caller's transaction): new envelopes, then their side-table rows."""
    n = conn.execute("SELECT morlet_build_envelopes(%s, %s, 4096, %s, %s)", (COLL, rid, STRIDE, B)).fetchone()[0]
    if n:
        conn.execute(MAINTAIN, (rid, rid))
    return n


def visible_max(conn, rid):
    return conn.execute("SELECT max(start_sample) FROM public.r2_hv WHERE recording_id = %s", (rid,)).fetchone()[0]


# -- worker process -------------------------------------------------------------------------------------------------
def worker(log_path, stop_path):
    """Loops over recordings with backlog; one transaction per step; logs (rid, max visible start, commit time)."""
    conn = connect()
    rids = [r for (r,) in conn.execute("SELECT recording_id FROM public.r2 ORDER BY recording_id").fetchall()]
    with open(log_path, "a") as log:
        steps = 0
        while True:
            did = 0
            for rid in rids:
                with conn.transaction():
                    n = maintain(conn, rid)
                    vm = visible_max(conn, rid) if n else None
                if n:
                    did += n
                    steps += 1
                    log.write(json.dumps({"rid": rid, "max_start": vm, "t": time.time(), "n": n,
                                          "maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}) + "\n")
                    log.flush()
            if not did:
                if os.path.exists(stop_path):
                    return
                time.sleep(0.05)


# -- one run --------------------------------------------------------------------------------------------------------
def completed_windows(end):
    return 0 if end < 4096 else (end - 4096) // STRIDE + 1


def run(root, out, mode, paced, kill_worker_after=None):
    recs = recordings(root)
    tag = f"{mode}-{'paced' if paced else 'max'}"
    conn = connect()
    reset(conn, recs)
    lsn0 = conn.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
    wlog, stop = out / f"{tag}.worker.jsonl", out / f"{tag}.stop"
    for p in (wlog, stop):
        if p.exists():
            p.unlink()
    proc = None
    if mode == "W":
        proc = subprocess.Popen([sys.executable, "-I", __file__, "worker", str(wlog), str(stop)], env=dict(os.environ, MORLET_DSN=DSN))
    commits, done, lock = [], threading.Event(), threading.Lock()
    t_start = time.time()

    def appender(rid, path):
        c = connect()
        x = np.load(path).astype(np.float64)
        t0 = time.time()
        for off in range(0, len(x), CHUNK):
            end = min(off + CHUNK, len(x))
            if paced:
                wait = t0 + end / PACE - time.time()
                if wait > 0:
                    time.sleep(wait)
            with c.transaction():
                c.execute("SELECT morlet_append_chunk(%s, %s, %s, %s)", (COLL, rid, off, x[off:end].tolist()))
                if mode == "S":
                    while maintain(c, rid):
                        pass
            t = time.time()
            with lock:
                commits.append({"rid": rid, "end": end, "t": t, "arrival": (t0 + end / PACE) if paced else None})
        c.close()

    queries, backlog = [], []

    def query_loop():
        c = connect()
        c.execute("SET hnsw.iterative_scan = relaxed_order")
        c.execute("SET plan_cache_mode = force_custom_plan")
        rng = random.Random(7)
        while not done.is_set():
            time.sleep(QUERY_EVERY)
            row = c.execute("SELECT recording_id, start_sample FROM public.r2_hv ORDER BY random() LIMIT 1").fetchone()
            if row is None:
                continue
            rid, s = row[0], int(row[1])
            with c.transaction():
                c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                n_vis = c.execute("SELECT count(*) FROM public.r2_hv").fetchone()[0]
                (p_top, _), p_ms = bl1.timed(lambda: bl1.arm_p(c, rid, s, "G13", 50))
                c.execute("SET LOCAL max_parallel_workers_per_gather = 8")
                (x_top, _), x_ms = bl1.timed(lambda: bl1.arm_x(c, rid, s))
            rec = len(set(p_top) & set(x_top)) / max(len(x_top), 1)
            queries.append({"t": time.time(), "visible": n_vis, "p_ms": p_ms, "x_ms": x_ms, "recall": rec, "x_size": len(x_top)})
        c.close()

    def sampler():
        c = connect()
        while not done.is_set() or (mode == "W" and backlog and backlog[-1]["backlog"] > 0):
            ends = c.execute("SELECT (checkpoint->>'end')::bigint FROM public.r2").fetchall()
            comp = sum(completed_windows(e or 0) for (e,) in ends)
            vis = c.execute("SELECT count(*) FROM public.r2_hv").fetchone()[0]
            backlog.append({"t": time.time(), "completed": comp, "visible": vis, "backlog": comp - vis})
            time.sleep(SAMPLE_EVERY)
        c.close()

    threads = [threading.Thread(target=appender, args=(rid, path)) for rid, _, _, path in recs]
    side = [threading.Thread(target=sampler)] + ([threading.Thread(target=query_loop)] if paced else [])
    for t in side + threads:
        t.start()
    killed = None
    if kill_worker_after is not None and proc is not None:
        time.sleep(kill_worker_after)
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        killed = time.time()
        proc = subprocess.Popen([sys.executable, "-I", __file__, "worker", str(wlog), str(stop)], env=dict(os.environ, MORLET_DSN=DSN))
    for t in threads:
        t.join()
    t_appended = time.time()
    done.set()
    if proc is not None:
        stop.touch()
        proc.wait()
    t_drained = time.time()
    for t in side:
        t.join()
    lsn1 = conn.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
    samples = sum(c_["end"] - (c_["end"] - 1) // CHUNK * CHUNK for c_ in commits)
    res = {"tag": tag, "mode": mode, "paced": paced, "samples": samples, "append_s": t_appended - t_start,
           "append_samples_per_s": samples / (t_appended - t_start), "drain_s": t_drained - t_appended,
           "wal_bytes": conn.execute("SELECT pg_wal_lsn_diff(%s, %s)", (lsn1, lsn0)).fetchone()[0],
           "worker_killed_at_s": (killed - t_start) if killed else None}
    res.update(check(conn, recs))
    res["bytes"] = {k: conn.execute(f"SELECT pg_total_relation_size('{r}')").fetchone()[0]
                    for k, r in (("raw_chunks", "public.r2_chunks"), ("envelopes", "public.r2_envelopes"), ("halfvec_table", "public.r2_hv"))}
    res["bytes"]["ip_hnsw"] = conn.execute("SELECT pg_relation_size('public.r2_hv_ip')").fetchone()[0]
    res.update(delays(commits, wlog if mode == "W" else None, paced))
    res["backlog"] = {"peak": max((b["backlog"] for b in backlog), default=0), "series": backlog}
    if mode == "W" and wlog.exists():
        rss = [json.loads(l)["maxrss"] for l in wlog.read_text().splitlines()]
        res["worker_maxrss_bytes"] = {"mid": rss[len(rss) // 2] if rss else None, "end": rss[-1] if rss else None}
    if queries:
        res["queries"] = {"n": len(queries), "p_ms": pct([q["p_ms"] for q in queries]), "x_ms": pct([q["x_ms"] for q in queries]),
                          "recall_mean": float(np.mean([q["recall"] for q in queries])),
                          "recall_min": float(np.min([q["recall"] for q in queries])), "series": queries}
    conn.close()
    json.dump(res, open(out / f"{tag}.json", "w"), indent=1)
    print(tag, json.dumps({k: v for k, v in res.items() if k not in ("backlog", "queries", "delays_series")} |
                          {"backlog_peak": res["backlog"]["peak"],
                           "queries": {k: v for k, v in res.get("queries", {}).items() if k != "series"}}), flush=True)
    return res


def pct(v):
    return {"p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95)), "max": float(np.max(v))}


def check(conn, recs):
    """Every completed window has exactly one envelope and one side row; nothing else."""
    out = {}
    for rid, _, _, _ in recs:
        end = conn.execute("SELECT (checkpoint->>'end')::bigint FROM public.r2 WHERE recording_id = %s", (rid,)).fetchone()[0]
        e = conn.execute("SELECT count(*), count(DISTINCT start_sample) FROM public.r2_envelopes WHERE recording_id = %s", (rid,)).fetchone()
        h = conn.execute("SELECT count(*), count(DISTINCT start_sample) FROM public.r2_hv WHERE recording_id = %s", (rid,)).fetchone()
        out[rid] = {"completed": completed_windows(end), "envelopes": e[0], "envelopes_distinct": e[1], "side": h[0], "side_distinct": h[1]}
    ok = all(v["completed"] == v["envelopes"] == v["envelopes_distinct"] == v["side"] == v["side_distinct"] for v in out.values())
    return {"complete_and_unique": ok, "per_recording": out}


def delays(commits, wlog, paced):
    """Per window: completion delay (commit of completing chunk - paced arrival of its last sample) and processing
    delay (visible - that commit)."""
    by = {}
    for c in sorted(commits, key=lambda c: c["t"]):
        by.setdefault(c["rid"], []).append(c)
    vis = {}
    if wlog is not None and wlog.exists():
        for l in wlog.read_text().splitlines():
            e = json.loads(l)
            vis.setdefault(e["rid"], []).append((e["max_start"], e["t"]))
    comp, proc = [], []
    for rid, cs in by.items():
        ends = np.array([c["end"] for c in cs])
        for w in range(completed_windows(int(ends.max()))):
            wend = w * STRIDE + 4096
            c = cs[int(np.searchsorted(ends, wend))]
            if paced:
                comp.append(c["t"] - c["arrival"] + (c["end"] - wend) / PACE)
            if wlog is None:
                proc.append(0.0)
            else:
                t_vis = next((t for ms, t in vis.get(rid, []) if ms >= w * STRIDE), None)
                proc.append(None if t_vis is None else max(0.0, t_vis - c["t"]))
    missing = sum(p is None for p in proc)
    proc = [p for p in proc if p is not None]
    out = {"processing_delay_s": pct(proc) if proc else None, "windows_without_visibility_log": missing}
    if paced:
        out["completion_delay_s"] = pct(comp)
    return out


def drills(root, out):
    """Duplicate replay and a gap on the final W-paced state (STAGE_B.md)."""
    conn = connect()
    rid = recordings(root)[0][0]
    x = np.load(recordings(root)[0][3]).astype(np.float64)
    count = lambda: (conn.execute("SELECT count(*) FROM public.r2_chunks").fetchone()[0],
                     conn.execute("SELECT count(*) FROM public.r2_envelopes").fetchone()[0],
                     conn.execute("SELECT count(*) FROM public.r2_hv").fetchone()[0],
                     conn.execute("SELECT checkpoint FROM public.r2 WHERE recording_id = %s", (rid,)).fetchone()[0])
    before = count()
    conn.execute("SELECT morlet_append_chunk(%s, %s, 0, %s)", (COLL, rid, x[:CHUNK].tolist()))
    after_dup = count()
    end = before[3]["end"]
    try:
        conn.execute("SELECT morlet_append_chunk(%s, %s, %s, %s)", (COLL, rid, end + 4096, x[:CHUNK].tolist()))
        gap = {"rejected": False}
    except psycopg.Error as e:
        gap = {"rejected": True, "error": str(e).splitlines()[0]}
    after_gap = count()
    res = {"duplicate_replay": {"unchanged": before == after_dup}, "gap": gap | {"unchanged": before == after_gap}}
    json.dump(res, open(out / "drills.json", "w"), indent=1)
    print("drills", json.dumps(res), flush=True)
    return res


def decide(out):
    r = {t: json.loads((out / f"{t}.json").read_text()) for t in ("S-max", "W-max", "S-paced", "W-paced")}
    verdict = {}
    for mode in ("S", "W"):
        p = r[f"{mode}-paced"]
        q = p["queries"]
        c = {"recall_ge_0.95": q["recall_mean"] >= 0.95, "p_p95_below_exact_p95": q["p_ms"]["p95"] < q["x_ms"]["p95"],
             "complete_and_unique": p["complete_and_unique"] and r[f"{mode}-max"]["complete_and_unique"]}
        if mode == "W":
            c["processing_delay_p95_le_5s"] = p["processing_delay_s"]["p95"] <= 5.0
        else:
            c["throughput_ge_half_of_W"] = r["S-max"]["append_samples_per_s"] >= 0.5 * r["W-max"]["append_samples_per_s"]
        verdict[mode] = c | {"usable": all(c.values())}
    rec = "S" if verdict["S"]["usable"] else "W" if verdict["W"]["usable"] else None
    res = {"modes": verdict, "recommended": rec, "verdict": "USABLE DURING INGESTION" if rec else "NOT USABLE AS IS"}
    json.dump(res, open(out / "decision.json", "w"), indent=1)
    print(json.dumps(res))


def main():
    if sys.argv[1] == "worker":
        return worker(sys.argv[2], sys.argv[3])
    root, out = Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    run(root, out, "S", paced=False)
    run(root, out, "W", paced=False, kill_worker_after=20.0)
    run(root, out, "S", paced=True)
    run(root, out, "W", paced=True)
    drills(root, out)
    decide(out)


if __name__ == "__main__":
    main()
