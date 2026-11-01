#!/usr/bin/env python3
"""Pilot Stage 2, Part B (STAGE2_PLAN.md): recovery and lifecycle tests B1-B6 on an isolated database
morlet_pilot_rec (collection `cw`, so CW1's external worker code applies). B7 lives in the upgrade test script.

  MORLET_DSN="host=localhost port=28817 dbname=morlet_pilot_rec" \
  .venv-seismic/bin/python -I evaluation/pilot/recovery.py data/cx-scale data/cx-confirm-2021 \
      target/release/examples/envelope_worker evaluation/pilot/results
"""
import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "external_worker"))
DB = "morlet_pilot_rec"
os.environ["MORLET_DSN"] = f"host=localhost port=28817 dbname={DB}"
import cw1  # noqa: E402  (worker_ext, collection `public.cw`)

BIN = Path.home() / ".pgrx/17.11/pgrx-install/bin"
COLL, W, STRIDE, CHUNK = "public.cw", 4096, 2048, 4096
RNG = random.Random(17)


def dsn(db=DB):
    return f"host=localhost port=28817 dbname={db}"


def connect(db=DB):
    return psycopg.connect(dsn(db), autocommit=True)


def run(*cmd):
    t = time.perf_counter()
    subprocess.run([str(c) for c in cmd], check=True, capture_output=True)
    return time.perf_counter() - t


# -- dataset --------------------------------------------------------------------------------------------------------
def segments(root, days, tag):
    for m in json.loads((root / "manifest.json").read_text()):
        if m["day"] in days:
            for i, seg in enumerate(m["segments"]):
                yield f"{tag}.{m['station']}.d{m['day']:02d}.{i}", m["station"], root / seg["file"]


def build_dataset(r2025, r2021):
    subprocess.run([BIN / "dropdb", "-h", "localhost", "-p", "28817", "--if-exists", DB], check=True)
    subprocess.run([BIN / "createdb", "-h", "localhost", "-p", "28817", DB], check=True)
    with connect() as c:
        c.execute("CREATE EXTENSION pg_morlet")
        c.execute("CREATE EXTENSION vector")
        c.execute("SELECT morlet_create_stream_collection('public','cw')")
        c.execute("SELECT morlet_enable_envelopes('public.cw')")
        for rid, st, path in [*segments(r2025, (1, 2), "y25"), *segments(r2021, (1,), "y21")]:
            x = np.load(path).astype(np.float64)
            with c.transaction():
                c.execute("SELECT morlet_start_recording(%s,%s,100,'HHZ','counts',%s,512,256,'zscore','none',1::float8)",
                          (COLL, rid, json.dumps({"station": st, "group": rid.split(".")[0]})))
                for s in range(0, len(x), CHUNK):
                    c.execute("SELECT morlet_append_chunk(%s,%s,%s,%s)", (COLL, rid, s, x[s:s + CHUNK].tolist()))
            while c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,100000)", (COLL, rid, W, STRIDE)).fetchone()[0]:
                pass
        c.execute("SET maintenance_work_mem = '2GB'")
        c.execute("SELECT morlet_enable_envelope_probes('public.cw', 4096)")
        return status(c)


# -- checks ---------------------------------------------------------------------------------------------------------
def status(c):
    r = c.execute("SELECT * FROM morlet_collection_status('public.cw')").fetchone()
    cols = [d.name for d in c.execute("SELECT * FROM morlet_collection_status('public.cw')").description]
    return dict(zip(cols, r))


def fingerprint(c):
    """Byte identity of raw chunks and envelopes, expected window count, probe completeness."""
    raw = c.execute("SELECT md5(string_agg(md5(encoded), '' ORDER BY recording_id, start_sample)) FROM public.cw_chunks").fetchone()[0]
    env = c.execute("SELECT md5(string_agg(md5(envelope), '' ORDER BY recording_id, start_sample)) FROM public.cw_envelopes").fetchone()[0]
    ckpt = c.execute("""SELECT bool_and((r.checkpoint->>'end')::bigint = coalesce(s.n, 0)) FROM public.cw r
                        LEFT JOIN (SELECT recording_id, sum(sample_count) n FROM public.cw_chunks GROUP BY 1) s USING (recording_id)""").fetchone()[0]
    expected = c.execute(f"SELECT coalesce(sum(greatest(0, ((checkpoint->>'end')::bigint - {W}) / {STRIDE} + 1)), 0) FROM public.cw").fetchone()[0]
    st = status(c)
    return {"raw_md5": raw, "envelopes_md5": env, "checkpoints_match_chunks": ckpt, "expected_windows": int(expected),
            "envelopes": st["envelopes"], "probe_rows": st["probe_rows"], "probe_backlog": st["probe_backlog"]}


def queries(c, n=20):
    rows = c.execute("SELECT recording_id, start_sample FROM public.cw_envelopes ORDER BY recording_id, start_sample").fetchall()
    rng = random.Random(5)
    return [rows[i] for i in sorted(rng.sample(range(len(rows)), n))]


def search_check(c, qs, ref=None):
    exact, recall = {}, []
    for rid, s in qs:
        e = [tuple(r) for r in c.execute("SELECT recording_id, start_sample, distance FROM morlet_search_envelope_interval(%s,%s,%s,%s,'{}',10,0.075,true,true,10000000)",
                                         (COLL, rid, s, s + W)).fetchall()]
        p = {tuple(r) for r in c.execute("SELECT recording_id, start_sample FROM morlet_search_envelope_probes(%s,%s,%s,%s)", (COLL, rid, s, s + W)).fetchall()}
        exact[f"{rid}:{s}"] = e
        recall.append(len(p & {(r, st) for r, st, _ in e}) / max(len(e), 1))
    out = {"probe_recall_mean": float(np.mean(recall)), "probe_recall_min": float(min(recall))}
    if ref is not None:
        out["exact_identical"] = exact == ref
    return out, exact


# -- B1 backup / restore --------------------------------------------------------------------------------------------
def b1(before, qs, ref, out):
    res = {}
    dump = out / "b1_full.dump"
    res["full_dump_s"] = run(BIN / "pg_dump", "-h", "localhost", "-p", "28817", "-Fc", "-f", dump, DB)
    res["full_dump_bytes"] = dump.stat().st_size
    for name, args in (("full", []), ("raw_only", None)):
        tgt = f"{DB}_{name}"
        subprocess.run([BIN / "dropdb", "-h", "localhost", "-p", "28817", "--if-exists", tgt], check=True)
        subprocess.run([BIN / "createdb", "-h", "localhost", "-p", "28817", tgt], check=True)
        if name == "full":
            res["full_restore_s"] = run(BIN / "pg_restore", "-h", "localhost", "-p", "28817", "-d", tgt, dump)
        else:
            raw = out / "b1_raw.dump"
            res["raw_dump_s"] = run(BIN / "pg_dump", "-h", "localhost", "-p", "28817", "-Fc", "-t", "public.cw", "-t", "public.cw_chunks",
                                    "-t", "public.cw_segments", "-f", raw, DB)
            res["raw_dump_bytes"] = raw.stat().st_size
            with connect(tgt) as c:
                c.execute("CREATE EXTENSION pg_morlet")
                c.execute("CREATE EXTENSION vector")
            res["raw_restore_s"] = run(BIN / "pg_restore", "-h", "localhost", "-p", "28817", "-d", tgt, raw)
            with connect(tgt) as c:
                t = time.perf_counter()
                c.execute("SELECT morlet_enable_envelopes('public.cw')")
                for (rid,) in c.execute("SELECT recording_id FROM public.cw ORDER BY 1").fetchall():
                    while c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,100000)", (COLL, rid, W, STRIDE)).fetchone()[0]:
                        pass
                res["rebuild_envelopes_s"] = time.perf_counter() - t
                t = time.perf_counter()
                c.execute("SET maintenance_work_mem = '2GB'")
                c.execute("SELECT morlet_enable_envelope_probes('public.cw', 4096)")
                res["rebuild_probes_s"] = time.perf_counter() - t
        with connect(tgt) as c:
            fp = fingerprint(c)
            sc, _ = search_check(c, qs, ref)
            res[name] = {"fingerprint_equal": fp == before, "fingerprint": fp, "search": sc,
                         "db_bytes": c.execute("SELECT pg_database_size(current_database())").fetchone()[0]}
        subprocess.run([BIN / "dropdb", "-h", "localhost", "-p", "28817", tgt], check=True)
    for f in out.glob("b1_*.dump"):
        f.unlink()
    return res


# -- B2 abort during append ------------------------------------------------------------------------------------------
def b2(r2021):
    """20 aborts of append + build transactions: 10 terminated mid-statement, 10 terminated before COMMIT."""
    path = next(p for _, _, p in segments(r2021, (2,), "y21"))
    x = np.load(path).astype(np.float64)[: 64 * CHUNK * 20]
    rid = "abort.test"
    with connect() as c:
        c.execute("SELECT morlet_start_recording(%s,%s,100,'HHZ','counts','{\"group\":\"abort\"}',512,256,'zscore','none',1::float8)", (COLL, rid))
    trials = []
    pos = 0
    for k in range(20):
        a = connect()
        pid = a.execute("SELECT pg_backend_pid()").fetchone()[0]
        mid = k % 2 == 0
        batch = 64                                                     # 64 chunks per transaction
        chunks = [(pos + i * CHUNK, x[pos + i * CHUNK: pos + (i + 1) * CHUNK].tolist()) for i in range(batch)]
        killer = threading.Timer(RNG.uniform(0.005, 0.15), lambda: connect().execute("SELECT pg_terminate_backend(%s)", (pid,))) if mid else None
        outcome = "committed"
        try:
            a.execute("BEGIN")
            if killer:
                killer.start()
            for s, v in chunks:
                a.execute("SELECT morlet_append_chunk(%s,%s,%s,%s)", (COLL, rid, s, v))
            a.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s)", (COLL, rid, W, STRIDE))
            if not mid:
                connect().execute("SELECT pg_terminate_backend(%s)", (pid,))
                time.sleep(0.05)
            a.execute("COMMIT")
        except psycopg.Error:
            outcome = "aborted"
        finally:
            if killer:
                killer.cancel()
            try:
                a.close()
            except Exception:
                pass
        with connect() as c:
            end = c.execute("SELECT (checkpoint->>'end')::bigint FROM public.cw WHERE recording_id=%s", (rid,)).fetchone()[0]
            stored = c.execute("SELECT coalesce(sum(sample_count),0) FROM public.cw_chunks WHERE recording_id=%s", (rid,)).fetchone()[0]
            env_beyond = c.execute(f"SELECT count(*) FROM public.cw_envelopes WHERE recording_id=%s AND start_sample+{W} > %s", (rid, end)).fetchone()[0]
            probes_gap = c.execute("SELECT (SELECT count(*) FROM public.cw_envelopes WHERE recording_id=%s) - (SELECT count(*) FROM public.cw_probes_4096 WHERE recording_id=%s)",
                                   (rid, rid)).fetchone()[0]
            if outcome == "aborted":                                   # replay the same batch: must succeed
                with c.transaction():
                    for s, v in chunks:
                        c.execute("SELECT morlet_append_chunk(%s,%s,%s,%s)", (COLL, rid, s, v))
                    c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s)", (COLL, rid, W, STRIDE))
        trials.append({"mid_statement": mid, "outcome": outcome, "checkpoint_equals_stored": end == stored, "envelopes_beyond_checkpoint": env_beyond,
                       "probe_rows_missing": probes_gap})
        pos += batch * CHUNK
    return {"trials": trials, "aborted": sum(t["outcome"] == "aborted" for t in trials),
            "all_consistent": all(t["checkpoint_equals_stored"] and t["envelopes_beyond_checkpoint"] == 0 and t["probe_rows_missing"] == 0 for t in trials)}


# -- B3 abort during envelope build and probe enable -----------------------------------------------------------------
def terminate_after(seconds, pid):
    threading.Timer(seconds, lambda: connect().execute("SELECT pg_terminate_backend(%s)", (pid,))).start()


def b3():
    res = {}
    with connect() as c:
        rids = [r for (r,) in c.execute("SELECT recording_id FROM public.cw WHERE recording_id LIKE 'y25.%' ORDER BY 1 LIMIT 2").fetchall()]
        c.execute("DELETE FROM public.cw_envelopes WHERE recording_id = ANY(%s)", (rids,))
    a = connect()
    pid = a.execute("SELECT pg_backend_pid()").fetchone()[0]
    terminate_after(1.0, pid)
    try:
        for rid in rids:
            a.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,1000000)", (COLL, rid, W, STRIDE))
        res["build_terminated"] = False
    except psycopg.Error:
        res["build_terminated"] = True
    with connect() as c:
        res["envelopes_after_abort"] = c.execute("SELECT count(*) FROM public.cw_envelopes WHERE recording_id = ANY(%s)", (rids,)).fetchone()[0]
        for rid in rids:
            while c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,100000)", (COLL, rid, W, STRIDE)).fetchone()[0]:
                pass
        res["resumed_complete"] = fingerprint(c)
        c.execute("DROP TABLE public.cw_probes_4096")
    a = connect()
    pid = a.execute("SELECT pg_backend_pid()").fetchone()[0]
    terminate_after(2.0, pid)
    try:
        a.execute("SET maintenance_work_mem = '2GB'")
        a.execute("SELECT morlet_enable_envelope_probes('public.cw', 4096)")
        res["enable_terminated"] = False
    except psycopg.Error:
        res["enable_terminated"] = True
    with connect() as c:
        res["probe_table_after_abort"] = c.execute("SELECT to_regclass('public.cw_probes_4096') IS NOT NULL").fetchone()[0]
        c.execute("SET maintenance_work_mem = '2GB'")
        t = time.perf_counter()
        c.execute("SELECT morlet_enable_envelope_probes('public.cw', 4096)")
        res["enable_retry_s"] = time.perf_counter() - t
        res["after_retry"] = fingerprint(c)
    return res


# -- B4 worker abort, duplicates, worker + in-database builder concurrently ------------------------------------------
def b4(binary):
    with connect() as c:
        rids = [r for (r,) in c.execute("SELECT recording_id FROM public.cw WHERE recording_id LIKE 'y21.%' ORDER BY 1").fetchall()]
        c.execute("DELETE FROM public.cw_envelopes WHERE recording_id = ANY(%s)", (rids,))
    procs = [subprocess.Popen([sys.executable, "-I", cw1.__file__, "worker", "ext", ",".join(rids), str(binary)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=dict(os.environ)) for _ in range(2)]
    time.sleep(1.5)
    subprocess.run(["pkill", "-KILL", "-P", str(procs[0].pid)])
    procs[0].kill()
    procs[0].wait()
    procs[0] = subprocess.Popen([sys.executable, "-I", cw1.__file__, "worker", "ext", ",".join(rids), str(binary)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=dict(os.environ))
    with connect() as c:                                               # in-database batched builder on the same recordings
        for rid in rids:
            while c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,100000)", (COLL, rid, W, STRIDE)).fetchone()[0]:
                pass
    for p in procs:
        p.stdout.readline()
        p.stdin.write("\n")
        p.stdin.flush()
        p.wait()
    with connect() as c:
        status_before_fill = status(c)
        # rows written by the worker skip the probe table (the worker writes envelopes only): fill them as maintenance does
        c.execute("""INSERT INTO public.cw_probes_4096 SELECT recording_id, window_size, start_sample, morlet_envelope_decode(envelope)::halfvec
                     FROM public.cw_envelopes WHERE window_size=4096 ON CONFLICT DO NOTHING""")
        return {"status_before_probe_fill": status_before_fill, "fingerprint": fingerprint(c),
                "duplicates": c.execute("SELECT count(*) - count(DISTINCT (recording_id, start_sample)) FROM public.cw_envelopes").fetchone()[0]}


# -- B6 index rebuild under load -------------------------------------------------------------------------------------
def b6(qs):
    stop, lat, errors = threading.Event(), {"before": [], "during": [], "after": []}, []
    phase = {"p": "before"}

    def client():
        c = connect()
        while not stop.is_set():
            rid, s = RNG.choice(qs)
            t = time.perf_counter()
            try:
                c.execute("SELECT count(*) FROM morlet_search_envelope_probes(%s,%s,%s,%s)", (COLL, rid, s, s + W)).fetchone()
                lat[phase["p"]].append(1e3 * (time.perf_counter() - t))
            except psycopg.Error as e:
                errors.append(str(e).splitlines()[0])
        c.close()

    threads = [threading.Thread(target=client) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(20)
    with connect() as c:
        idx = c.execute("SELECT i.indexrelid::regclass::text FROM pg_index i WHERE i.indrelid='public.cw_probes_4096'::regclass AND NOT i.indisprimary").fetchone()[0]
        size0 = c.execute("SELECT pg_relation_size(%s::regclass)", (idx,)).fetchone()[0]
        peak = {"bytes": 0}
        done = threading.Event()

        def sampler():
            m = connect()
            while not done.is_set():
                b = m.execute("SELECT coalesce(sum(pg_relation_size(i.indexrelid)),0) FROM pg_index i WHERE i.indrelid='public.cw_probes_4096'::regclass AND NOT i.indisprimary").fetchone()[0]
                peak["bytes"] = max(peak["bytes"], b)
                time.sleep(0.5)
            m.close()

        smp = threading.Thread(target=sampler)
        smp.start()
        c.execute("SET maintenance_work_mem = '2GB'")
        phase["p"] = "during"
        t = time.perf_counter()
        c.execute(f"REINDEX INDEX CONCURRENTLY {idx}")
        rebuild = time.perf_counter() - t
        phase["p"] = "after"
        done.set()
        smp.join()
    time.sleep(20)
    stop.set()
    for t in threads:
        t.join()
    pct = lambda v: {"n": len(v), "p50": float(np.percentile(v, 50)) if v else None, "p95": float(np.percentile(v, 95)) if v else None}
    return {"rebuild_s": rebuild, "index_bytes_before": size0, "peak_index_bytes_during": peak["bytes"],
            "latency_ms": {k: pct(v) for k, v in lat.items()}, "errors": errors[:5], "error_count": len(errors)}


# -- B5 deletion and index maintenance -------------------------------------------------------------------------------
def b5(qs):
    with connect() as c:
        rids = [r for (r,) in c.execute("SELECT recording_id FROM public.cw ORDER BY recording_id").fetchall()]
        drop = [r for i, r in enumerate(rids) if i % 5 == 0 and r != "abort.test"]
        idx = c.execute("SELECT i.indexrelid::regclass::text FROM pg_index i WHERE i.indrelid='public.cw_probes_4096'::regclass AND NOT i.indisprimary").fetchone()[0]
        before = {"index_bytes": c.execute("SELECT pg_relation_size(%s::regclass)", (idx,)).fetchone()[0],
                  "probe_rows": c.execute("SELECT count(*) FROM public.cw_probes_4096").fetchone()[0]}
        c.execute("DELETE FROM public.cw WHERE recording_id = ANY(%s)", (drop,))
        orphans = {t: c.execute(f"SELECT count(*) FROM public.{t} WHERE recording_id = ANY(%s)", (drop,)).fetchone()[0]
                   for t in ("cw_chunks", "cw_envelopes", "cw_probes_4096")}
        t = time.perf_counter()
        c.execute("VACUUM public.cw_probes_4096")
        vac = time.perf_counter() - t
        kept = [q for q in qs if q[0] not in drop]
        sc, exact = search_check(c, kept)
        leaked = sum(1 for lst in exact.values() for r, _, _ in lst if r in drop)
        probe_leak = 0
        for rid, s in kept:
            probe_leak += sum(1 for (r,) in c.execute("SELECT recording_id FROM morlet_search_envelope_probes(%s,%s,%s,%s)", (COLL, rid, s, s + W)).fetchall()
                              if r in drop)
        return {"deleted_recordings": len(drop), "before": before, "orphans": orphans, "vacuum_s": vac,
                "index_bytes_after": c.execute("SELECT pg_relation_size(%s::regclass)", (idx,)).fetchone()[0],
                "probe_rows_after": c.execute("SELECT count(*) FROM public.cw_probes_4096").fetchone()[0],
                "deleted_in_results": {"exact": leaked, "probe": probe_leak}, "search_after": sc, "queries": len(kept)}


def main():
    r2025, r2021, binary, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    t = time.perf_counter()
    res["dataset"] = {k: (int(v) if isinstance(v, (int, np.integer)) else v) for k, v in build_dataset(r2025, r2021).items()}
    res["dataset"]["build_s"] = time.perf_counter() - t
    print("dataset", res["dataset"], flush=True)
    with connect() as c:
        before = fingerprint(c)
        qs = queries(c)
        sc, ref = search_check(c, qs)
    res["baseline"] = {"fingerprint": before, "search": sc}
    for name, fn in (("B1", lambda: b1(before, qs, ref, out)), ("B2", lambda: b2(r2021)), ("B3", b3), ("B4", lambda: b4(binary)),
                     ("B6", lambda: b6(qs)), ("B5", lambda: b5(qs))):
        t = time.perf_counter()
        res[name] = fn()
        res[name]["wall_s"] = time.perf_counter() - t
        print(name, json.dumps(res[name], default=str)[:900], flush=True)
        json.dump(res, open(out / "recovery.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
