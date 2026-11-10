#!/usr/bin/env python3
"""Cross-check (#351): the same samples appended through pg_morlet and through morlet-sqlite give the same chunk
bytes, the same summaries, the same reads and the same interval statistics, bit for bit.

  cross_pg.py MORLET_SQLITE_BIN POSTGRES_DSN WORK_DIR
"""
import json
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import psycopg

BIN, DSN, WORK = sys.argv[1], sys.argv[2], Path(sys.argv[3])
N, CHUNK, RATE, Q = 1_200_000, 4096, 64000.0, 100.0 / 32768
rng = np.random.default_rng(351)
i = np.arange(N)
x = np.round(3000 * np.sin(i * 0.031) + 40 * np.sin(i * 1.7) + rng.normal(0, 3, N)) * Q
x[::997] = rng.normal(0, 1, len(x[::997]))          # off-grid values
x[5::1013] = -0.0
WORK.mkdir(parents=True, exist_ok=True)
f, raw = WORK / "x.morlet", WORK / "x.f64"
for p in (f, Path(str(f) + "-wal"), Path(str(f) + "-shm")):
    p.unlink(missing_ok=True)
x.astype("<f8").tofile(raw)

def cli(*a):
    return subprocess.run([BIN, *map(str, a)], check=True, capture_output=True, text=True).stdout

cli("create", f, "r", RATE, "--quantum", repr(Q), "--unit", "V")
print("sqlite append", cli("append", f, "r", 0, raw, "--chunk-size", CHUNK).strip())

with psycopg.connect(DSN, autocommit=True) as c:
    c.execute("DROP TABLE IF EXISTS xc_chunks, xc CASCADE")
    c.execute("SELECT morlet_create_stream_collection('public','xc')")
    c.execute("SELECT morlet_start_recording('xc','r',%s,'x','V','{}',512,256,'zscore','none',%s)", (RATE, Q))
    for s in range(0, N, 262144):
        c.execute("SELECT morlet_append_chunks_raw('xc','r',%s,%s,%s)", (s, x[s:s + 262144].astype("<f8").tobytes(), CHUNK))
    pg = c.execute("SELECT start_sample, sample_count, encoded, summary FROM xc_chunks WHERE recording_id='r' ORDER BY start_sample").fetchall()
    lite = sqlite3.connect(f).execute("SELECT start_sample, sample_count, encoded, summary FROM chunks WHERE recording_id='r' ORDER BY start_sample").fetchall()
    assert len(pg) == len(lite), (len(pg), len(lite))
    diff = sum(tuple(a[:2]) != tuple(b[:2]) or bytes(a[2]) != bytes(b[2]) or bytes(a[3]) != bytes(b[3]) for a, b in zip(pg, lite))
    print(f"chunks: {len(pg)}, differing (start, count, encoded bytes or summary bytes): {diff}")
    assert diff == 0
    bits = lambda v: struct.pack("<d", float(v))
    k, bad_stats, bad_reads = 0, 0, 0
    for t in range(200):
        a = int(rng.integers(0, N - 1))
        b = int(min(N, a + 1 + rng.integers(0, 300_000)))
        p = c.execute("SELECT sample_count, min, max, mean, rms FROM morlet_interval_stats('xc','r',%s,%s)", (a, b)).fetchone()
        s = json.loads(cli("stats", f, "r", a, b))
        if any(bits(u) != bits(v) for u, v in zip(p, (s["sample_count"], s["min"], s["max"], s["mean"], s["rms"]))):
            bad_stats += 1
            print("stats differ", a, b, p, s)
        if t % 10 == 0:
            out = WORK / "read.f64"
            cli("read", f, "r", a, b, out)
            bad_reads += np.fromfile(out, "<f8").tobytes() != x[a:b].astype("<f8").tobytes()
    print(f"interval statistics: 200 intervals, differing from pg_morlet: {bad_stats}; reads: 20, differing from the input: {bad_reads}")
    assert bad_stats == 0 and bad_reads == 0
    c.execute("DROP TABLE IF EXISTS xc_chunks, xc CASCADE")
print("PASS")
