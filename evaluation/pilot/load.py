#!/usr/bin/env python3
"""Pilot Stage 2, Part C (STAGE2_PLAN.md): bulk fill (C1), then sustained ingest + concurrent search (C2-C4) on an
isolated database morlet_pilot_load.

  .venv-seismic/bin/python -I evaluation/pilot/load.py fill  data/cx-scale data/cx-confirm-2021 OUT
  .venv-seismic/bin/python -I evaluation/pilot/load.py run   data/cx-confirm-2021 OUT TAG MINUTES RATE_FACTOR [MINUTES RATE_FACTOR ...]
"""
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

DB = "morlet_pilot_load"
DSN = f"host=localhost port=28817 dbname={DB}"
BIN = Path.home() / ".pgrx/17.11/pgrx-install/bin"
COLL, W, STRIDE, CHUNK, RATE = "public.pl", 4096, 2048, 4096, 25600
CHANNELS, WRITERS = 16, 4
ABORT = {"lag_s": 60.0, "recall": 0.9, "p95_ms": 10_000, "free_gb": 200, "db_gb": 60}


def connect():
    return psycopg.connect(DSN, autocommit=True)


def segments(root, tag, reverse=False):
    for m in json.loads((root / "manifest.json").read_text()):
        for i, seg in enumerate(m["segments"]):
            yield (f"{tag}.{m['station']}.d{m['day']:02d}.{i}", {"group": f"{tag}.{m['station']}", "station": m["station"], "day": m["day"],
                                                                "year": tag[:4], "reversed": reverse}, root / seg["file"], reverse)


# -- C1 bulk fill -----------------------------------------------------------------------------------------------------
def fill_worker(items_json):
    items = json.loads(items_json)
    c = connect()
    for rid, md, path, rev in items:
        x = np.load(path).astype(np.float64)
        if rev:
            x = x[::-1].copy()
        if len(x) < W:
            continue
        with c.transaction():
            c.execute("SELECT morlet_start_recording(%s,%s,%s,'acc','counts',%s,512,256,'zscore','none',1::float8)", (COLL, rid, RATE, Jsonb(md)))
            for s in range(0, len(x), CHUNK):
                c.execute("SELECT morlet_append_chunk(%s,%s,%s,%s)", (COLL, rid, s, x[s:s + CHUNK].tolist()))
        while c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s,100000)", (COLL, rid, W, STRIDE)).fetchone()[0]:
            pass


def fill(r2025, r2021, out):
    subprocess.run([BIN / "dropdb", "-h", "localhost", "-p", "28817", "--if-exists", DB], check=True)
    subprocess.run([BIN / "createdb", "-h", "localhost", "-p", "28817", DB], check=True)
    with connect() as c:
        c.execute("CREATE EXTENSION pg_morlet")
        c.execute("CREATE EXTENSION vector")
        c.execute("SELECT morlet_create_stream_collection('public','pl')")
        c.execute("SELECT morlet_enable_envelopes('public.pl')")
    items = [(r, m, str(p), rev) for rev in (False, True) for tag, root in (("2025", r2025), ("2021", r2021))
             for r, m, p, _ in segments(root, tag + ("r" if rev else ""), rev)]
    t = time.perf_counter()
    procs = [subprocess.Popen([sys.executable, "-I", __file__, "fillworker", json.dumps(items[i::4])]) for i in range(4)]
    for p in procs:
        p.wait()
        assert p.returncode == 0
    fill_s = time.perf_counter() - t
    with connect() as c:
        c.execute("SET maintenance_work_mem = '4GB'")
        t = time.perf_counter()
        c.execute("SELECT morlet_enable_envelope_probes('public.pl', 4096)")
        probe_s = time.perf_counter() - t
        st = dict(zip([d.name for d in c.execute("SELECT * FROM morlet_collection_status('public.pl')").description],
                      c.execute("SELECT * FROM morlet_collection_status('public.pl')").fetchone()))
        db_bytes = c.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    res = {"fill_s": fill_s, "probe_enable_s": probe_s, "status": st, "db_bytes": db_bytes}
    json.dump(res, open(out / "c1_fill.json", "w"), indent=1, default=str)
    print(json.dumps(res, default=str))


# -- live writers ---------------------------------------------------------------------------------------------------
def writer_main(idx, source, schedule_json, log_path):
    """Appends chunks for its 4 channels at RATE × factor (schedule: [[seconds, factor], ...]); one transaction per tick
    with the append and the envelope build (synchronous maintenance). Logs per transaction."""
    schedule = json.loads(schedule_json)
    files = sorted(Path(source).glob("*.npy"))
    c = connect()
    chans = [f"live.ch{idx * 4 + k:02d}" for k in range(4)]
    data = [np.load(files[(idx * 4 + k) % len(files)]).astype(np.float64) for k in range(4)]
    for ch in chans:
        if c.execute(f"SELECT 1 FROM {COLL} WHERE recording_id=%s", (ch,)).fetchone() is None:
            c.execute("SELECT morlet_start_recording(%s,%s,%s,'acc','counts',%s,512,256,'zscore','none',1::float8)",
                      (COLL, ch, RATE, Jsonb({"group": "live", "channel": ch})))
    pos = [c.execute(f"SELECT (checkpoint->>'end')::bigint FROM {COLL} WHERE recording_id=%s", (ch,)).fetchone()[0] for ch in chans]
    data_t = 0.0                                                     # seconds of signal written per channel
    stream = 0.0                                                     # seconds of signal that should have arrived
    with open(log_path, "a") as log:
        for seconds, factor in schedule:
            phase_start, stream_start = time.time(), stream
            end_wall = phase_start + seconds
            while time.time() < end_wall:
                stream = stream_start + (time.time() - phase_start) * factor
                if data_t + CHUNK / RATE > stream:                   # ahead of the stream: wait for the next chunk
                    time.sleep(min(0.05, (data_t + CHUNK / RATE - stream) / factor))
                    continue
                start = time.perf_counter()
                err = None
                try:
                    with c.transaction():
                        for k, ch in enumerate(chans):
                            src = data[k]
                            off = pos[k] % (len(src) - CHUNK)
                            c.execute("SELECT morlet_append_chunk(%s,%s,%s,%s)", (COLL, ch, pos[k], src[off:off + CHUNK].tolist()))
                            c.execute("SELECT morlet_build_envelopes(%s,%s,%s,%s)", (COLL, ch, W, STRIDE))
                    for k in range(4):
                        pos[k] += CHUNK
                    data_t += CHUNK / RATE
                except psycopg.Error as e:
                    err = str(e).splitlines()[0]
                stream = stream_start + (time.time() - phase_start) * factor
                log.write(json.dumps({"t": time.time(), "ms": 1e3 * (time.perf_counter() - start), "lag_s": stream - data_t,
                                      "samples": 4 * CHUNK, "err": err}) + "\n")
                log.flush()


# -- search clients -------------------------------------------------------------------------------------------------
def client_main(idx, seconds, log_path):
    rng = random.Random(100 + idx)
    c = connect()
    targets = c.execute("SELECT recording_id, start_sample, metadata->>'group', (metadata->>'day')::int FROM public.pl_envelopes e "
                        "JOIN public.pl r USING (recording_id) WHERE r.metadata->>'group' <> 'live' AND e.start_sample % 65536 = 0").fetchall()
    end, k, last_heavy = time.time() + seconds, 0, time.time()
    with open(log_path, "a") as log:
        while time.time() < end:
            rid, s, group, day = rng.choice(targets)
            k += 1
            filtered = k % 2 == 0
            flt = {"group": group, "day": day} if filtered else {}            # one station-day: ~4.2k windows, exact path
            rec, err = None, None
            t = time.perf_counter()
            try:
                if idx == 0 and time.time() - last_heavy >= 60:
                    kind = "exact_unfiltered"
                    last_heavy = time.time()
                    c.execute("SELECT count(*) FROM morlet_search_envelope_interval(%s,%s,%s,%s,'{}',10,0.075,true,true,100000000)", (COLL, rid, s, s + W)).fetchone()
                else:
                    kind = "filtered" if filtered else "unfiltered"
                    with c.transaction():
                        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                        got = {tuple(r) for r in c.execute("SELECT recording_id, start_sample FROM morlet_search_envelope_probes(%s,%s,%s,%s,%s)",
                                                           (COLL, rid, s, s + W, Jsonb(flt))).fetchall()}
                        ms = 1e3 * (time.perf_counter() - t)
                        if not filtered and k % 10 == 1:                 # spot check in the same snapshot
                            ref = {tuple(r) for r in c.execute("SELECT recording_id, start_sample FROM morlet_search_envelope_interval(%s,%s,%s,%s,%s,10,0.075,true,true,100000000)",
                                                               (COLL, rid, s, s + W, Jsonb(flt))).fetchall()}
                            rec = len(got & ref) / max(len(ref), 1)
                if kind == "exact_unfiltered":
                    ms = 1e3 * (time.perf_counter() - t)
            except psycopg.Error as e:
                err, ms, kind = str(e).splitlines()[0], 1e3 * (time.perf_counter() - t), "error"
            log.write(json.dumps({"t": time.time(), "kind": kind, "ms": ms, "recall": rec, "err": err}) + "\n")
            log.flush()
            time.sleep(max(0.0, 2.0 - (time.perf_counter() - t)))


# -- driver ---------------------------------------------------------------------------------------------------------
def postgres_cpu_rss():
    out = subprocess.run(["ps", "-axo", "pid=,comm=,time=,rss="], capture_output=True, text=True).stdout
    cpu, rss = 0.0, 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and "postgres" in parts[1]:
            m = re.match(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$", parts[2])
            d, h, mi, s = (float(g) if g else 0.0 for g in m.groups())
            cpu += d * 86400 + h * 3600 + mi * 60 + s
            rss = max(rss, int(parts[3]) * 1024)
    return cpu, rss


def run(source, out, tag, schedule):
    seconds = sum(s for s, _ in schedule)
    logs = out / f"{tag}_logs"
    logs.mkdir(parents=True, exist_ok=True)
    for f in logs.glob("*.jsonl"):
        f.unlink()
    with connect() as c:
        lsn0 = c.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
        db0 = c.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    writers = [subprocess.Popen([sys.executable, "-I", __file__, "writer", str(i), str(source), json.dumps(schedule), str(logs / f"w{i}.jsonl")]) for i in range(WRITERS)]
    clients = [subprocess.Popen([sys.executable, "-I", __file__, "client", str(i), str(seconds), str(logs / f"c{i}.jsonl")]) for i in range(4)]
    samples, aborted, t_end = [], None, time.time() + seconds
    cpu0, _ = postgres_cpu_rss()
    while time.time() < t_end and aborted is None:
        time.sleep(10)
        cpu, rss = postgres_cpu_rss()
        with connect() as c:
            lsn = c.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
            wal = int(c.execute("SELECT pg_wal_lsn_diff(%s, %s)", (lsn, lsn0)).fetchone()[0])
            db = c.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        free = int(subprocess.run(["df", "-k", str(out)], capture_output=True, text=True).stdout.splitlines()[1].split()[3]) * 1024
        w = [json.loads(l) for f in logs.glob("w*.jsonl") for l in f.read_text().splitlines()[-50:]]
        q = [json.loads(l) for f in logs.glob("c*.jsonl") for l in f.read_text().splitlines()]
        lag = max((x["lag_s"] for x in w), default=0.0)
        recalls = [x["recall"] for x in q if x["recall"] is not None][-50:]
        recent = [x["ms"] for x in q if x["t"] > time.time() - 300 and x["kind"] in ("filtered", "unfiltered")]
        snap = {"t": time.time(), "load_avg": os.getloadavg()[0], "pg_cpu_s": cpu - cpu0, "pg_max_rss": rss, "wal_bytes": wal,
                "db_bytes": db, "free_bytes": free, "max_writer_lag_s": lag, "writer_errors": sum(1 for x in w if x["err"])}
        samples.append(snap)
        if snap["writer_errors"]:
            aborted = "append error"
        elif lag > ABORT["lag_s"] and len(samples) > 30 and samples[-30]["max_writer_lag_s"] < lag:
            aborted = "ingest lag above 60 s and growing for 5 min"
        elif len(recalls) >= 50 and np.mean(recalls) < ABORT["recall"]:
            aborted = "spot-check recall below 0.9"
        elif len(recent) > 20 and np.percentile(recent, 95) > ABORT["p95_ms"]:
            aborted = "search p95 above 10 s"
        elif free < ABORT["free_gb"] * 1e9 or db > ABORT["db_gb"] * 1e9:
            aborted = "disk budget"
    for p in writers + clients:
        if aborted:
            p.terminate()
        p.wait()
    res = summarize(logs, samples, schedule, db0, aborted)
    json.dump(res, open(out / f"{tag}.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "timeline"}, indent=1))


def pct(v):
    return {"n": len(v), "p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95)), "p99": float(np.percentile(v, 99)),
            "max": float(np.max(v))} if v else {"n": 0}


def summarize(logs, samples, schedule, db0, aborted):
    w = [json.loads(l) for f in sorted(logs.glob("w*.jsonl")) for l in f.read_text().splitlines()]
    q = [json.loads(l) for f in sorted(logs.glob("c*.jsonl")) for l in f.read_text().splitlines()]
    t0 = min(x["t"] for x in w) if w else 0
    phases, start = [], t0
    for seconds, factor in schedule:
        ww = [x for x in w if start <= x["t"] < start + seconds]
        qq = [x for x in q if start <= x["t"] < start + seconds]
        phases.append({"seconds": seconds, "rate_factor": factor, "target_samples_per_s": CHANNELS * RATE * factor,
                       "achieved_samples_per_s": sum(x["samples"] for x in ww) / seconds,
                       "append_txn_ms": pct([x["ms"] for x in ww]), "max_lag_s": max((x["lag_s"] for x in ww), default=None),
                       "lag_at_end_s": max((x["lag_s"] for x in ww[-WRITERS * 5:]), default=None),
                       "search_ms": {k: pct([x["ms"] for x in qq if x["kind"] == k]) for k in ("filtered", "unfiltered", "exact_unfiltered")},
                       "recall": {"n": len([x for x in qq if x["recall"] is not None]),
                                  "mean": float(np.mean([x["recall"] for x in qq if x["recall"] is not None])) if any(x["recall"] is not None for x in qq) else None},
                       "errors": sum(1 for x in ww + qq if x.get("err"))})
        start += seconds
    last = samples[-1] if samples else {}
    return {"aborted": aborted, "phases": phases, "db_growth_bytes": (last.get("db_bytes", db0) - db0),
            "wal_bytes": last.get("wal_bytes"), "pg_cpu_s": last.get("pg_cpu_s"), "pg_max_rss": max((s["pg_max_rss"] for s in samples), default=None),
            "load_avg": {"mean": float(np.mean([s["load_avg"] for s in samples])) if samples else None,
                         "max": max((s["load_avg"] for s in samples), default=None)},
            "timeline": samples}


def main():
    cmd = sys.argv[1]
    if cmd == "fillworker":
        return fill_worker(sys.argv[2])
    if cmd == "writer":
        return writer_main(int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5])
    if cmd == "client":
        return client_main(int(sys.argv[2]), float(sys.argv[3]), sys.argv[4])
    if cmd == "fill":
        out = Path(sys.argv[4])
        out.mkdir(parents=True, exist_ok=True)
        return fill(Path(sys.argv[2]), Path(sys.argv[3]), out)
    source, out, tag = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    vals = sys.argv[5:]
    schedule = [[float(vals[i]) * 60, float(vals[i + 1])] for i in range(0, len(vals), 2)]
    run(source, out, tag, schedule)


if __name__ == "__main__":
    main()
