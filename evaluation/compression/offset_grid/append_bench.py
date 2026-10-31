#!/usr/bin/env python3
"""End-to-end append throughput for two Sleep-EDF nights with the installed pg_morlet.

Reads the stored samples of SC4002 (asymmetric physical range: offset grid) and SC4001
(symmetric: plain grid) from the sleep demo database, appends them into a fresh raw-only
collection in a scratch database (4096-sample chunks, 16 chunks per transaction, as the
loader does), and reports wall-clock samples/s and the stored chunk-table bits/sample.

  .venv-seismic/bin/python -I evaluation/compression/offset_grid/append_bench.py OUT.json
"""
import json
import sys
import time

import numpy as np
import psycopg

SRC = "host=localhost port=28817 dbname=morlet_sleep_demo"
DST = "host=localhost port=28817 dbname=morlet_append_bench"


def main():
    out = sys.argv[1]
    with psycopg.connect("host=localhost port=28817 dbname=postgres", autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS morlet_append_bench")
        c.execute("CREATE DATABASE morlet_append_bench")
    res = {}
    with psycopg.connect(SRC) as s, psycopg.connect(DST, autocommit=True) as d:
        d.execute("CREATE EXTENSION pg_morlet")
        d.execute("SELECT morlet_create_stream_collection('public','bench')")
        for rid in ("SC4002", "SC4001"):
            rate, q, end = s.execute("SELECT sample_rate_hz, sample_quantum, (checkpoint->>'end')::bigint FROM sleep_eeg"
                                     " WHERE recording_id=%s", (rid,)).fetchone()
            x = np.concatenate([np.asarray(s.execute("SELECT samples FROM morlet_read_interval('sleep_eeg',%s,%s,%s)",
                                                     (rid, a, min(a + 32768, end))).fetchone()[0])
                                for a in range(0, end, 32768)])
            d.execute("SELECT morlet_start_recording('bench',%s,%s,'EEG Fpz-Cz','uV','{}',512,256,'zscore','none',%s)",
                      (rid, rate, q))
            chunks = [x[i:i + 4096].tolist() for i in range(0, len(x), 4096)]
            t0 = time.perf_counter()
            for i in range(0, len(chunks), 16):
                with d.transaction():
                    for j in range(i, min(i + 16, len(chunks))):
                        d.execute("SELECT morlet_append_chunk('bench',%s,%s,%s::float8[])", (rid, 4096 * j, chunks[j]))
            secs = time.perf_counter() - t0
            st = d.execute("SELECT grid_chunks, float_chunks, raw_chunks, stored_value_bits_per_sample"
                           " FROM morlet_recording_storage('bench',%s)", (rid,)).fetchone()
            res[rid] = {"samples": len(x), "append_s": secs, "samples_per_s": len(x) / secs,
                        "grid_float_raw": list(st[:3]), "stored_value_bits_per_sample": st[3]}
            print(rid, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res[rid].items()}, flush=True)
    json.dump(res, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
