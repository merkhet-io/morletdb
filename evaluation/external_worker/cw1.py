#!/usr/bin/env python3
"""CW1 (CW1_PROTOCOL.md): in-database envelope building (IN) vs external compute workers (EXT) on R2's days 1-2.

  .venv-seismic/bin/python -I evaluation/external_worker/cw1.py setup  data/cx-scale
  .venv-seismic/bin/python -I evaluation/external_worker/cw1.py run    target/release/examples/envelope_worker OUT
"""
import hashlib
import json
import os
import re
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_cw1")
COLL, W, STRIDE, BATCH, CHUNK = "public.cw", 4096, 2048, 64, 4096
STATIONS, DAYS, NS = ("PB01", "PB02", "PB06", "PSGCX"), (1, 2), (1, 2, 4, 8)


def connect():
    return psycopg.connect(DSN, autocommit=True)


# -- setup ----------------------------------------------------------------------------------------------------------
def setup(root):
    with connect() as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        if c.execute("SELECT to_regclass(%s)", (COLL,)).fetchone()[0] is None:
            c.execute("SELECT morlet_create_stream_collection('public', 'cw')")
            c.execute("SELECT morlet_enable_envelopes(%s)", (COLL,))
        for m in json.loads((root / "manifest.json").read_text()):
            if m["station"] not in STATIONS or m["day"] not in DAYS:
                continue
            for i, seg in enumerate(m["segments"]):
                rid = f"{m['station']}.d{m['day']:02d}.{i}"
                x = np.load(root / seg["file"]).astype(np.float64)
                with c.transaction():
                    if c.execute(f"SELECT 1 FROM {COLL} WHERE recording_id = %s", (rid,)).fetchone() is None:
                        c.execute("SELECT morlet_start_recording(%s, %s, 100, 'HHZ', 'counts', '{}', 512, 256, 'zscore', 'none', 1::float8)",
                                  (COLL, rid))
                    for s in range(0, len(x), CHUNK):
                        c.execute("SELECT morlet_append_chunk(%s, %s, %s, %s)", (COLL, rid, s, x[s:s + CHUNK].tolist()))
                print(rid, len(x), flush=True)


# -- workers (separate processes) -----------------------------------------------------------------------------------
def recording_state(c, rid):
    end = c.execute(f"SELECT (checkpoint->>'end')::bigint FROM {COLL} WHERE recording_id = %s", (rid,)).fetchone()[0]
    last = c.execute("SELECT max(start_sample) FROM public.cw_envelopes WHERE recording_id = %s AND window_size = %s", (rid, W)).fetchone()[0]
    return end, (0 if last is None else (last // STRIDE + 1) * STRIDE)


def worker_in(rids):
    c = connect()
    for rid in rids:
        while c.execute("SELECT morlet_build_envelopes(%s, %s, %s, %s, %s)", (COLL, rid, W, STRIDE, BATCH)).fetchone()[0]:
            pass
    return c


def worker_ext(rids, binary):
    c = connect()
    proc = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    for rid in rids:
        while True:
            end, s = recording_state(c, rid)
            if s + W > end:
                break
            hi = min(end, s + (BATCH - 1) * STRIDE + W)
            chunks = c.execute("""SELECT start_sample, encoded FROM public.cw_chunks WHERE recording_id = %s
                                  AND start_sample < %s AND start_sample + sample_count > %s ORDER BY start_sample""",
                               (rid, hi, s)).fetchall()
            req = [struct.pack("<I", len(chunks))]
            for st, enc in chunks:
                enc = bytes(enc)
                req += [struct.pack("<qI", st, len(enc)), enc]
            req.append(struct.pack("<qqIII", s, end, W, STRIDE, BATCH))
            proc.stdin.write(b"".join(req))
            proc.stdin.flush()
            n = struct.unpack("<I", proc.stdout.read(4))[0]
            rows = []
            for _ in range(n):
                st, ln = struct.unpack("<qI", proc.stdout.read(12))
                rows.append((st, proc.stdout.read(ln)))
            with c.transaction():
                c.execute("""INSERT INTO public.cw_envelopes (recording_id, window_size, start_sample, envelope)
                             SELECT %s, %s, s, e FROM unnest(%s::bigint[], %s::bytea[]) AS t(s, e) ON CONFLICT DO NOTHING""",
                          (rid, W, [r[0] for r in rows], [r[1] for r in rows]))
    proc.stdin.close()
    proc.wait()
    return c


def worker_main(kind, rids, binary):
    """Builds, then reports and waits so the driver can read this backend's CPU before it exits."""
    c = worker_in(rids) if kind == "in" else worker_ext(rids, binary)
    pid = c.execute("SELECT pg_backend_pid()").fetchone()[0]
    print(json.dumps({"done": True, "backend_pid": pid}), flush=True)
    sys.stdin.readline()
    c.close()


# -- driver ---------------------------------------------------------------------------------------------------------
def postgres_cpu():
    """CPU seconds per postgres process (pid -> seconds)."""
    out = subprocess.run(["ps", "-axo", "pid=,comm=,time="], capture_output=True, text=True).stdout
    cpu = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "postgres" in parts[1]:
            t = parts[-1]
            m = re.match(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$", t)
            d, h, mi, s = (float(g) if g else 0.0 for g in m.groups())
            cpu[int(parts[0])] = d * 86400 + h * 3600 + mi * 60 + s
    return cpu


def recordings():
    with connect() as c:
        return [r for (r,) in c.execute(f"SELECT recording_id FROM {COLL} ORDER BY recording_id").fetchall()]


def expected_windows(c):
    ends = c.execute(f"SELECT (checkpoint->>'end')::bigint FROM {COLL}").fetchall()
    return sum((e - W) // STRIDE + 1 for (e,) in ends if e >= W)


def digest(c):
    rows = c.execute("SELECT recording_id, start_sample, envelope FROM public.cw_envelopes ORDER BY recording_id, start_sample").fetchall()
    return {(r, int(s)): hashlib.sha256(bytes(e)).hexdigest() for r, s, e in rows}


def run_arm(kind, n, binary, rids, assignment=None, kill_after=None):
    with connect() as c:
        c.execute("TRUNCATE public.cw_envelopes")
        c.execute("CHECKPOINT")
        lsn0 = c.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
    assignment = assignment or [rids[i::n] for i in range(n)]
    before = postgres_cpu()
    r0 = os.times()
    t0 = time.perf_counter()
    procs = [subprocess.Popen([sys.executable, "-I", __file__, "worker", kind, ",".join(a), binary],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=dict(os.environ, MORLET_DSN=DSN))
             for a in assignment]
    killed = None
    if kill_after is not None:
        time.sleep(kill_after)
        victim = procs[0]
        subprocess.run(["pkill", "-KILL", "-P", str(victim.pid)])        # its Rust child
        victim.send_signal(signal.SIGKILL)
        victim.wait()
        killed = time.perf_counter() - t0
        procs[0] = subprocess.Popen([sys.executable, "-I", __file__, "worker", kind, ",".join(assignment[0]), binary],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=dict(os.environ, MORLET_DSN=DSN))
    reports = [json.loads(p.stdout.readline()) for p in procs]
    wall = time.perf_counter() - t0
    after = postgres_cpu()
    for p in procs:
        p.stdin.write("\n")
        p.stdin.flush()
        p.wait()
    r1 = os.times()
    db_cpu = sum(after[p] - before.get(p, 0.0) for p in after)
    with connect() as c:
        windows = c.execute("SELECT count(*) FROM public.cw_envelopes").fetchone()[0]
        unique = c.execute("SELECT count(DISTINCT (recording_id, start_sample)) FROM public.cw_envelopes").fetchone()[0]
        wal = int(c.execute("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), %s)", (lsn0,)).fetchone()[0])
        exp = expected_windows(c)
        dig = digest(c)
    worker_cpu = (r1.children_user + r1.children_system) - (r0.children_user + r0.children_system)
    return {"kind": kind, "n": n, "wall_s": wall, "windows": windows, "expected": exp, "unique": unique,
            "windows_per_s": windows / wall, "db_cpu_s": db_cpu, "db_cpu_ms_per_window": 1e3 * db_cpu / max(windows, 1),
            "worker_cpu_s": worker_cpu, "worker_cpu_ms_per_window": 1e3 * worker_cpu / max(windows, 1), "wal_bytes": wal,
            "killed_at_s": killed, "backend_pids": [r["backend_pid"] for r in reports]}, dig


def run(binary, out):
    out.mkdir(parents=True, exist_ok=True)
    rids = recordings()
    res, ref = {"runs": []}, None
    for kind in ("in", "ext"):
        for n in NS:
            r, dig = run_arm(kind, n, binary, rids)
            if ref is None:
                ref = dig                                   # IN, N = 1 is the reference
            r["identical_to_in"] = dig == ref
            res["runs"].append(r)
            print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "backend_pids"}), flush=True)
    r, dig = run_arm("ext", 2, binary, rids, kill_after=3.0)
    r["identical_to_in"] = dig == ref
    res["kill_restart"] = r
    r, dig = run_arm("ext", 2, binary, rids, assignment=[rids, rids])  # both workers on every recording
    r["identical_to_in"] = dig == ref
    res["duplicate_workers"] = r
    print("robustness", json.dumps({k: {x: res[k][x] for x in ("windows", "expected", "unique", "identical_to_in", "killed_at_s")}
                                    for k in ("kill_restart", "duplicate_workers")}), flush=True)
    by = {(r["kind"], r["n"]): r for r in res["runs"]}
    ident = all(r["identical_to_in"] and r["windows"] == r["expected"] == r["unique"] for r in res["runs"])
    robust = all(res[k]["identical_to_in"] and res[k]["windows"] == res[k]["expected"] == res[k]["unique"]
                 for k in ("kill_restart", "duplicate_workers"))
    cpu = all(by[("ext", n)]["db_cpu_ms_per_window"] <= 0.3 * by[("in", n)]["db_cpu_ms_per_window"] for n in NS)
    thr = by[("ext", 4)]["windows_per_s"] >= by[("in", 4)]["windows_per_s"]
    verdict = ("STOP" if not (ident and robust) else "PURSUE" if cpu and thr else "NARROW" if cpu or thr else "STOP")
    res["decision"] = {"identity": ident, "robustness": robust, "db_cpu_moved_70pct": cpu, "ext4_throughput_ge_in4": thr, "verdict": verdict}
    json.dump(res, open(out / "cw1.json", "w"), indent=1)
    print(json.dumps(res["decision"]))


def main():
    if sys.argv[1] == "worker":
        return worker_main(sys.argv[2], sys.argv[3].split(","), sys.argv[4])
    if sys.argv[1] == "setup":
        return setup(Path(sys.argv[2]))
    run(sys.argv[2], Path(sys.argv[3]))


if __name__ == "__main__":
    main()
