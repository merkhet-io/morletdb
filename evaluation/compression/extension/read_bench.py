"""Usage: read_bench.py DATA.npy OUT.json (needs psycopg; DSN in $MORLET_DSN, data dir in $PGDATA_DIR).

Read and ingest performance of float8[] vs encoded chunks at 4096 and 32768 samples
per chunk, on one recording of development-period Paderborn counts.

For each layout and chunk size it measures:
- ingest through morlet_append_chunk, including feature extraction (window 512,
  stride 256), committing up to 262144 samples per transaction;
- random short reads (512 samples) at uniform offsets, and random 4096-sample reads;
- 512-sample reads centred on the configuration's own chunk boundaries;
- sustained sequential reads in 32768-sample intervals. Two variants:
  - server: decoded and counted in Postgres;
  - client: arrays fetched into Python.

"Warm" means after one full sequential pass. "PG-cold" means directly after a
PostgreSQL restart (empty shared_buffers) with the OS page cache left warm: this
machine cannot drop the OS cache without root, so true disk-cold latency is not
measured. Latency is client wall time per query over localhost TCP and includes
the round trip. Decode amplification is samples decoded per sample returned.
"""
import json
import os
import platform
import subprocess
import sys
import time

import numpy as np
import psycopg

DATA, OUT = sys.argv[1], sys.argv[2]
DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_readbench")
PGDATA = os.environ.get("PGDATA_DIR", os.path.expanduser("~/.pgrx/data-17"))
PGLOG = os.environ.get("PGLOG", os.path.expanduser("~/.pgrx/17.log"))
PG_CTL = os.environ.get("PG_CTL", os.path.expanduser("~/.pgrx/17.11/pgrx-install/bin/pg_ctl"))
RNG = np.random.default_rng(20261026)
LEGACY = """CREATE TABLE {t} (recording_id text PRIMARY KEY, sample_rate_hz float8 NOT NULL, channel text NOT NULL,
  unit text NOT NULL, metadata jsonb NOT NULL, checkpoint jsonb NOT NULL);
CREATE TABLE {t}_chunks (recording_id text NOT NULL REFERENCES {t} ON DELETE CASCADE, start_sample bigint NOT NULL,
  samples float8[] NOT NULL CHECK(array_ndims(samples)=1 AND array_lower(samples,1)=1 AND public.morlet_valid_chunk(samples)),
  PRIMARY KEY(recording_id,start_sample));
CREATE TABLE {t}_segments (recording_id text NOT NULL REFERENCES {t} ON DELETE CASCADE, start_sample bigint NOT NULL,
  features float8[] NOT NULL, PRIMARY KEY(recording_id,start_sample));"""

x = np.load(DATA).astype(float)
N = len(x) - len(x) % 32768
x = x[:N]
SHORT = RNG.integers(0, N - 512, 1000)
LONG = RNG.integers(0, N - 4096, 300)


def pct(ms):
    a = np.asarray(ms)
    return {"n": len(a), "p50_ms": float(np.percentile(a, 50)), "p95_ms": float(np.percentile(a, 95)),
            "p99_ms": float(np.percentile(a, 99)), "max_ms": float(a.max()), "mean_ms": float(a.mean())}


def amplification(starts, length, chunk):
    touched = [(s + length - 1) // chunk - s // chunk + 1 for s in starts]
    return float(np.mean(touched) * chunk / length)


def timed_reads(conn, t, starts, length):
    out = []
    for s in starts:
        t0 = time.perf_counter()
        n = conn.execute("SELECT cardinality(samples) FROM morlet_read_interval(%s,'r',%s,%s)",
                         (t, int(s), int(s) + length)).fetchone()[0]
        out.append(1000 * (time.perf_counter() - t0))
        assert n == length
    return out


def sequential(conn, t, fetch):
    t0 = time.perf_counter()
    for s in range(0, N, 32768):
        if fetch:
            got = conn.execute("SELECT samples FROM morlet_read_interval(%s,'r',%s,%s)", (t, s, s + 32768)).fetchone()[0]
            assert len(got) == 32768
        else:
            conn.execute("SELECT cardinality(samples) FROM morlet_read_interval(%s,'r',%s,%s)", (t, s, s + 32768)).fetchone()
    secs = time.perf_counter() - t0
    return {"seconds": secs, "msamples_per_s": N / secs / 1e6}


def restart():
    # The restarted postmaster inherits pg_ctl's stdio, so never capture it (it would never reach EOF).
    subprocess.run([PG_CTL, "-D", PGDATA, "restart", "-w", "-m", "fast", "-l", PGLOG], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


report = {"hardware": {"machine": platform.machine(), "cpu": subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                       capture_output=True, text=True).stdout.strip(), "os": platform.platform()},
          "samples": N, "data": "Paderborn development bearings, int16 counts as float8 (quantum 1)", "configs": {}}
conn = psycopg.connect(DSN, autocommit=True)
if True:
    report["postgres"] = {k: conn.execute(f"SHOW {k}").fetchone()[0]
                          for k in ("server_version", "shared_buffers", "effective_cache_size")}
    for layout in ("legacy", "encoded"):
        for chunk in (4096, 32768):
            t = f"rb_{layout}_{chunk}"
            conn.execute(f"DROP TABLE IF EXISTS {t}_chunks, {t}_segments, {t}")
            if layout == "legacy":
                conn.execute(LEGACY.format(t=t))
            else:
                conn.execute("SELECT morlet_create_stream_collection('public',%s)", (t,))
            conn.execute("SELECT morlet_start_recording(%s,'r',64000,'vibration','counts')", (t,))
            per_call = min(64, 262144 // chunk)
            t0 = time.perf_counter()
            for s in range(0, N, chunk * per_call):
                with conn.transaction():
                    for c in range(s, min(N, s + chunk * per_call), chunk):
                        conn.execute("SELECT morlet_append_chunk(%s,'r',%s,%s::float8[])", (t, c, x[c:c + chunk].tolist()))
            ingest = time.perf_counter() - t0
            conn.execute(f"VACUUM ANALYZE {t}_chunks")
            size = conn.execute("SELECT pg_total_relation_size(%s::regclass)", (f"{t}_chunks",)).fetchone()[0]
            cfg = {"ingest_seconds": ingest, "ingest_msamples_per_s": N / ingest / 1e6, "chunk_table_bytes": size,
                   "chunk_table_bits_per_sample": 8 * size / N}
            # Correctness: one random interval per configuration, bitwise.
            got = conn.execute("SELECT samples FROM morlet_read_interval(%s,'r',%s,%s)", (t, 1000003, 1000003 + 20000)).fetchone()[0]
            assert np.array_equal(np.asarray(got), x[1000003:1000003 + 20000])
            conn.close()
            restart()
            conn = psycopg.connect(DSN, autocommit=True)
            cfg["pg_cold_short_512"] = pct(timed_reads(conn, t, SHORT[:300], 512))
            restart()
            conn = psycopg.connect(DSN, autocommit=True)
            cfg["pg_cold_sequential_server"] = sequential(conn, t, False)
            sequential(conn, t, False)  # warm-up pass
            boundaries = np.arange(chunk, N, chunk)
            cross = RNG.choice(boundaries, min(1000, len(boundaries)), replace=False) - 256
            cfg["warm_short_512"] = pct(timed_reads(conn, t, SHORT, 512))
            cfg["warm_long_4096"] = pct(timed_reads(conn, t, LONG, 4096))
            cfg["warm_boundary_512"] = pct(timed_reads(conn, t, cross, 512))
            cfg["warm_sequential_server"] = sequential(conn, t, False)
            cfg["warm_sequential_client"] = sequential(conn, t, True)
            cfg["decode_amplification"] = {"short_512": amplification(SHORT, 512, chunk),
                                           "long_4096": amplification(LONG, 4096, chunk),
                                           "boundary_512": amplification(cross, 512, chunk),
                                           "sequential_32768": amplification(range(0, N, 32768), 32768, chunk)}
            report["configs"][f"{layout}-{chunk}"] = cfg
            print(t, json.dumps({k: (round(v["p50_ms"], 3), round(v["p95_ms"], 3)) if isinstance(v, dict) and "p50_ms" in v
                                 else v for k, v in cfg.items() if k not in ("decode_amplification",)}), flush=True)
json.dump(report, open(OUT, "w"), indent=1)
