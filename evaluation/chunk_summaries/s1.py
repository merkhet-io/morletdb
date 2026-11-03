#!/usr/bin/env python3
"""S1 (PROTOCOL.md): per-chunk summaries. One step per call, results in results/<step>.json.

  B3_SET=dev .venv-b3/bin/python -I evaluation/chunk_summaries/s1.py load     # H twice, L copies, builder
  B3_SET=dev .venv-b3/bin/python -I evaluation/chunk_summaries/s1.py gate
  B3_SET=dev .venv-b3/bin/python -I evaluation/chunk_summaries/s1.py time
  B3_SET=dev .venv-b3/bin/python -I evaluation/chunk_summaries/s1.py ingest
  B3_SET=dev .venv-b3/bin/python -I evaluation/chunk_summaries/s1.py finish   # storage, h_plain builder, versions
"""
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tsdb_b3"))
import workload as wl  # noqa: E402

RES = HERE / "results"
PG = dict(host="localhost", port=28817)
RATE, H_SAMPLES, CH = 64000, 3600 * 64000, 4096
QUANTUM = wl.QUANTUM["vibration_1"]
SEED = 20261103
H_DB, LP_DB, LS_DB = "morlet_s1", "morlet_s1_lp", "morlet_s1_ls"
STATS = "SELECT sample_count,min,max,mean,rms FROM morlet_interval_stats(%s,%s,%s,%s)"
OVER = "SELECT bucket_start,bucket_end,sample_count,min,max,mean,rms FROM morlet_interval_overview(%s,%s,%s,%s,%s)"


def conn(db):
    return psycopg.connect(**PG, dbname=db, autocommit=True)


def save(name, obj):
    RES.mkdir(exist_ok=True)
    (RES / f"{name}.json").write_text(json.dumps(obj, indent=1) + "\n")


_ONCE = []


def h_samples(n=H_SAMPLES):
    """vibration_1 of B3's development acquisitions, concatenated in order and repeated, cut at n samples."""
    if not _ONCE:
        _ONCE.append(np.concatenate([wl.load(path)["vibration_1"] for _, _, path, _ in wl.acquisitions()]))
    once = _ONCE[0]
    return np.ascontiguousarray(np.tile(once, -(-n // len(once)))[:n], dtype="<f8")


def load_h(c, coll, x, summarize):
    c.execute(f"SELECT morlet_create_stream_collection('public','{coll}')")
    if summarize:
        c.execute(f"SELECT morlet_enable_chunk_summaries('{coll}')")
    c.execute(f"SELECT morlet_start_recording('{coll}','h',{RATE},'vibration_1','as distributed','{{}}',512,256,"
              f"'none','none',{QUANTUM!r})")
    t = time.perf_counter()
    with c.pipeline():
        for pos in range(0, len(x), CH):
            c.execute(f"SELECT morlet_append_chunk_raw('{coll}','h',%s,%s)", (pos, x[pos:pos + CH].tobytes()))
    return len(x) / (time.perf_counter() - t)


def step_load():
    with conn("postgres") as c:
        for db in (H_DB, LP_DB, LS_DB):
            c.execute(f"DROP DATABASE IF EXISTS {db}")
        for db in (H_DB, LP_DB, LS_DB):
            c.execute(f"CREATE DATABASE {db}")
    x = h_samples()
    out = {"h_samples": len(x), "h_sha256": hashlib.sha256(x.tobytes()).hexdigest()}
    with conn(H_DB) as c:
        c.execute("CREATE EXTENSION pg_morlet")
        out["load_h_plain_samples_per_s"] = load_h(c, "h_plain", x, False)
        out["load_h_sum_samples_per_s"] = load_h(c, "h_sum", x, True)
    # L: E4's N1 gallery's recordings and chunks, copied into fresh databases with this build's extension
    psql = str(Path.home() / ".pgrx/17.11/pgrx-install/bin")
    dump = subprocess.run([f"{psql}/pg_dump", "-h", "localhost", "-p", "28817", "--data-only", "-t", "r2", "-t", "r2_chunks",
                           "morlet_r2"], capture_output=True, check=True).stdout
    for db in (LP_DB, LS_DB):
        with conn(db) as c:
            c.execute("CREATE EXTENSION pg_morlet")
            c.execute("SELECT morlet_create_stream_collection('public','r2')")
        subprocess.run([f"{psql}/psql", "-h", "localhost", "-p", "28817", "-q", "-v", "ON_ERROR_STOP=1", "-d", db],
                       input=dump, check=True, capture_output=True)
        with conn(db) as c:
            c.execute("ANALYZE")
    out["l_dump_sha256"] = hashlib.sha256(dump).hexdigest()
    with conn(LS_DB) as c:
        c.execute("SELECT morlet_enable_chunk_summaries('r2')")
        ids = [r[0] for r in c.execute("SELECT recording_id FROM r2 ORDER BY 1").fetchall()]
        t, n = time.perf_counter(), 0
        for rid in ids:
            n += c.execute("SELECT morlet_build_chunk_summaries('r2',%s)", (rid,)).fetchone()[0]
        out["builder_l"] = {"chunks": n, "seconds": time.perf_counter() - t}
        out["builder_l"]["chunks_per_s"] = n / out["builder_l"]["seconds"]
    save("load", out)
    print(json.dumps(out, indent=1))


def l_recordings(c):
    return [(r, int(e)) for r, e in c.execute(
        "SELECT recording_id,(checkpoint->>'end')::bigint FROM r2 ORDER BY 1").fetchall()]


def intervals(rng, recs, classes, per):
    """[(recording, start, end)] with `per` intervals per (lo, hi) length class."""
    out = []
    for lo, hi in classes:
        pool = [(r, e) for r, e in recs if e >= lo]
        for _ in range(per):
            r, e = pool[rng.integers(len(pool))]
            length = int(rng.integers(lo, min(hi, e) + 1))
            a = int(rng.integers(0, e - length + 1))
            out.append((r, a, a + length))
    return out


def bits(rows):
    return [[float(v).hex() if isinstance(v, float) else v for v in row] for row in rows]


def step_gate():
    rng = np.random.default_rng(SEED)
    classes = [(1, 4096), (4097, 1_000_000), (1_000_001, 16_777_216)]
    res = {}
    with conn(H_DB) as h, conn(LP_DB) as lp, conn(LS_DB) as ls:
        archives = {
            "H": (h, h, "h_plain", "h_sum", [("h", H_SAMPLES)]),
            "L": (lp, ls, "r2", "r2", l_recordings(lp)),
        }
        for name, (cp, cs, plain, summ, recs) in archives.items():
            bad, n = [], 0
            for r, a, b in intervals(rng, recs, classes, 100):
                for sql, extra in [(STATS, ())] + [(OVER, (k,)) for k in (1, 10, 100, 1000)]:
                    x = bits(cp.execute(sql, (plain, r, a, b, *extra)).fetchall())
                    y = bits(cs.execute(sql, (summ, r, a, b, *extra)).fetchall())
                    n += 1
                    if x != y:
                        bad.append({"recording": r, "start": a, "end": b, "extra": extra, "plain": x, "summarized": y})
            res[f"identity_{name}"] = {"comparisons": n, "differ": len(bad), "examples": bad[:5]}
            print(name, "identity", n, "differ", len(bad), flush=True)
        x = h_samples()
        worst, fails = {"mean": 0.0, "rms": 0.0}, []
        for _ in range(20):
            length = int(rng.integers(16_777_217, H_SAMPLES + 1))
            a = int(rng.integers(0, H_SAMPLES - length + 1))
            cnt, mn, mx, mean, rms = h.execute(STATS, ("h_sum", "h", a, a + length)).fetchone()
            seg = x[a:a + length]
            rm, rr = float(np.mean(seg, dtype=np.float64)), float(np.sqrt(np.mean(np.square(seg), dtype=np.float64)))
            em, er = abs(mean - rm) / max(abs(rm), 1e-300), abs(rms - rr) / rr
            worst = {"mean": max(worst["mean"], em), "rms": max(worst["rms"], er)}
            if cnt != length or mn != seg.min() or mx != seg.max() or em > 1e-12 or er > 1e-12:
                fails.append({"start": a, "end": a + length, "got": [cnt, mn, mx, mean, rms],
                              "numpy": [length, float(seg.min()), float(seg.max()), rm, rr]})
        res["beyond_limit_H"] = {"intervals": 20, "fails": fails, "max_rel_diff": worst}
        print("beyond limit fails", len(fails), worst, flush=True)
        cons = {}
        for label, c, coll in (("h_sum", h, "h_sum"), ("ls", ls, "r2")):
            cons[label] = c.execute(f"SELECT count(*), count(*) FILTER (WHERE summary IS DISTINCT FROM "
                                    f"morlet_encoded_chunk_summary(encoded)) FROM {coll}_chunks").fetchone()
        res["summaries_consistent"] = cons
        print("consistency", cons, flush=True)
    res["passed"] = (res["identity_H"]["differ"] == 0 and res["identity_L"]["differ"] == 0
                     and not res["beyond_limit_H"]["fails"] and all(v[1] == 0 for v in cons.values()))
    save("gate", res)
    print("passed", res["passed"])
    sys.exit(0 if res["passed"] else 1)


def timed(c, sql, args, reps=5):
    c.execute(sql, args).fetchall()
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        c.execute(sql, args).fetchall()
        out.append(1e3 * (time.perf_counter() - t))
    return out


def pct(xs):
    return {"p50": float(np.percentile(xs, 50)), "p95": float(np.percentile(xs, 95)), "n": len(xs)}


def step_time():
    rng = np.random.default_rng(SEED + 1)
    res = {"loadavg_start": list(os.getloadavg())}
    with conn(H_DB) as h, conn(LP_DB) as lp, conn(LS_DB) as ls:
        cases = [("H", s, h, h, "h_plain", "h_sum", [("h", H_SAMPLES)], RATE) for s in (1, 10, 60, 600, 3600)]
        cases += [("L", s, lp, ls, "r2", "r2", [x for x in l_recordings(lp) if x[1] >= 8_640_000], 100)
                  for s in (3600, 6 * 3600, 24 * 3600)]
        for arch, secs, cp, cs, plain, summ, recs, rate in cases:
            length = secs * rate
            plain_len = min(length, 16_777_216)
            tp, ts, tq = [], [], []
            for _ in range(20):
                r, e = recs[rng.integers(len(recs))]
                a = int(rng.integers(0, e - length + 1))
                tp += timed(cp, STATS, (plain, r, a, a + plain_len))
                ts += timed(cs, STATS, (summ, r, a, a + length))
                if plain_len != length:
                    tq += timed(cs, STATS, (summ, r, a, a + plain_len))
            key = f"{arch} {secs} s"
            res[key] = {"samples": length, "plain_samples": plain_len, "plain_ms": pct(tp), "summarized_ms": pct(ts)}
            if tq:
                res[key]["summarized_at_plain_length_ms"] = pct(tq)
            res[key]["speedup_p95"] = res[key]["plain_ms"]["p95"] / res[key][
                "summarized_at_plain_length_ms" if tq else "summarized_ms"]["p95"]
            print(key, {k: (round(v["p50"], 1), round(v["p95"], 1)) for k, v in res[key].items() if isinstance(v, dict)},
                  "speedup p95", round(res[key]["speedup_p95"], 1), flush=True)
        res["overview H 3600 s, 1000 buckets, summarized"] = pct(timed(h, OVER, ("h_sum", "h", 0, H_SAMPLES, 1000), 10))
        res["overview H 16777216, 1000 buckets"] = {
            "plain_ms": pct(timed(h, OVER, ("h_plain", "h", 0, 16_777_216, 1000), 10)),
            "summarized_ms": pct(timed(h, OVER, ("h_sum", "h", 0, 16_777_216, 1000), 10))}
        print("overviews", {k: v for k, v in res.items() if k.startswith("overview")}, flush=True)
    res["loadavg_end"] = list(os.getloadavg())
    save("time", res)


def step_ingest():
    x = h_samples(600 * RATE)
    res = {"samples": len(x), "plain": [], "summarized": []}
    with conn(H_DB) as c:
        for rnd in range(5):
            for summ in (False, True):
                coll = "ing_sum" if summ else "ing_plain"
                c.execute(f"DROP TABLE IF EXISTS {coll}_segments, {coll}_chunks, {coll} CASCADE")
                rate = load_h(c, coll, x, summ)
                res["summarized" if summ else "plain"].append(rate)
                print(rnd, coll, round(rate / 1e6, 3), "M samples/s", flush=True)
            c.execute("CHECKPOINT")
        for coll in ("ing_sum", "ing_plain"):
            c.execute(f"DROP TABLE IF EXISTS {coll}_segments, {coll}_chunks, {coll} CASCADE")
    res["median_plain"] = statistics.median(res["plain"])
    res["median_summarized"] = statistics.median(res["summarized"])
    res["change"] = res["median_summarized"] / res["median_plain"] - 1
    save("ingest", res)
    print("median change", round(100 * res["change"], 1), "%")


def step_finish():
    res = {}
    with conn(H_DB) as c:
        res["chunk_table_bytes"] = {k: c.execute(f"SELECT pg_total_relation_size('{k}_chunks')").fetchone()[0]
                                    for k in ("h_plain", "h_sum")}
        res["chunk_table_growth"] = res["chunk_table_bytes"]["h_sum"] / res["chunk_table_bytes"]["h_plain"] - 1
        c.execute("SELECT morlet_enable_chunk_summaries('h_plain')")
        t, n = time.perf_counter(), 0
        while True:
            k = c.execute("SELECT morlet_build_chunk_summaries('h_plain','h',100000)").fetchone()[0]
            n += k
            if k == 0:
                break
        res["builder_h"] = {"chunks": n, "seconds": time.perf_counter() - t}
        res["builder_h"]["chunks_per_s"] = n / res["builder_h"]["seconds"]
        res["builder_h"]["samples_per_s"] = n * CH / res["builder_h"]["seconds"]
        res["extension"] = c.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]
    lib = Path.home() / ".pgrx/17.11/pgrx-install/lib/postgresql/pg_morlet.dylib"
    res["library_sha256"] = hashlib.sha256(lib.read_bytes()).hexdigest()
    res["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
    save("finish", res)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    {"load": step_load, "gate": step_gate, "time": step_time, "ingest": step_ingest, "finish": step_finish}[sys.argv[1]]()
