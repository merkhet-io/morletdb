#!/usr/bin/env python3
"""Create a synthetic demo collection for the viewer (writes to the given database).

    DATABASE_URL=postgresql://... python tools/viewer/demo.py

One spindle-vibration recording per part (5 kHz, 8 parts). Each part runs program O100 with
blocks N10..N40 (roughing, finishing, drilling, contouring) at fixed tooth-passing
frequencies. Part 6 contains an air cut in N30 (tool moves without engaging), part 7 chatter
in N20 (a growing resonance). Context, segments, simple-v1 values, an air-cut label and
materialized envelopes are built through the public SQL API. Synthetic: for showing the
viewer, not evidence.
"""
import json
import os
import sys

import numpy as np
import psycopg

RATE, COLL = 5000.0, "demo_spindle"
BLOCKS = [("N10", 6.0, 180.0, 1.0), ("N20", 5.0, 240.0, 0.6), ("N30", 3.0, 300.0, 0.8), ("N40", 4.0, 150.0, 0.7)]


def part_signal(part, rng):
    pieces, context, t0 = [], [], 0
    for block, seconds, tooth_hz, amp in BLOCKS:
        n = int(seconds * RATE)
        t = np.arange(n) / RATE
        x = amp * np.sin(2 * np.pi * tooth_hz * t) * (1 + 0.3 * np.sin(2 * np.pi * 13.77 * t))
        x += 0.15 * np.sin(2 * np.pi * 13.77 * t) + rng.normal(0, 0.08, n)
        if part == 6 and block == "N30":
            x = 0.15 * np.sin(2 * np.pi * 13.77 * t) + rng.normal(0, 0.08, n)  # air cut: no engagement
        if part == 7 and block == "N20":
            x += np.linspace(0, 1.2, n) * np.sin(2 * np.pi * 870.0 * t)          # chatter
        pieces.append(x)
        context.append((block, t0, t0 + n))
        t0 += n
    return np.concatenate(pieces), context


def main():
    dsn = os.environ.get("DATABASE_URL") or sys.exit("set DATABASE_URL")
    rng = np.random.default_rng(7)
    with psycopg.connect(dsn, autocommit=True) as conn:
        for t in ("_envelopes", "_labels", "_ctx_values", "_ctx_segments", "_ctx", "_chunks", "_segments", ""):
            conn.execute(f"DROP TABLE IF EXISTS {COLL}{t} CASCADE")
        conn.execute("SELECT morlet_create_stream_collection('public', %s)", (COLL,))
        conn.execute("SELECT morlet_enable_context(%s)", (COLL,))
        conn.execute("SELECT morlet_enable_envelopes(%s)", (COLL,))
        for part in range(1, 9):
            rid = f"part-{part:03d}"
            x, blocks = part_signal(part, rng)
            q = np.round(x * 1000) / 1000
            conn.execute("SELECT morlet_start_recording(%s,%s,%s,'spindle_vib','g',%s,512,256,'zscore','none',0.001)",
                         (COLL, rid, RATE, json.dumps({"machine": "M7", "part": part})))
            for s in range(0, len(q), 32768):
                conn.execute("SELECT morlet_append_chunk(%s,%s,%s,%s::float8[])", (COLL, rid, s, q[s:s + 32768].tolist()))
            rows = [{"recording_id": rid, "key": "program", "value": "O100", "start_sample": 0, "end_sample": len(q)},
                    {"recording_id": rid, "key": "tool", "value": 7, "start_sample": 0, "end_sample": len(q)},
                    {"recording_id": rid, "key": "part", "value": f"SN-{part:04d}", "start_sample": 0, "end_sample": len(q)}]
            rows += [{"recording_id": rid, "key": "block", "value": b, "start_sample": a, "end_sample": e} for b, a, e in blocks]
            conn.execute("SELECT morlet_add_context(%s,%s,%s)", (COLL, f"ctl-{rid}", json.dumps(rows)))
            conn.execute("SELECT morlet_build_context_segments(%s,%s,ARRAY['program','tool','block'],ARRAY['part'])", (COLL, rid))
            conn.execute("SELECT morlet_build_context_values(%s,%s)", (COLL, rid))
            conn.execute("SELECT morlet_build_envelopes(%s,%s,4096,2048)", (COLL, rid))
            if part == 6:
                a, e = blocks[2][1], blocks[2][2]
                conn.execute("SELECT morlet_add_label(%s,%s,%s,%s,'air_cut','synthetic demo')", (COLL, rid, a, e))
    print(f"created {COLL}: 8 parts with context, segments, values, envelopes and one air-cut label")


if __name__ == "__main__":
    main()
