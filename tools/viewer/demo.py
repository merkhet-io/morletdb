#!/usr/bin/env python3
"""Create a synthetic demo collection for the viewer (writes to the given database).

    DATABASE_URL=postgresql://... python tools/viewer/demo.py

Eight parts, each one acquisition (15 min apart) with two channels: spindle vibration at
5 kHz and spindle current at 500 Hz from a separately clocked logger (offset 2 ms, +-1 ms).
Each part runs program O100 with blocks N10..N40 at fixed tooth-passing frequencies and
cutting loads. Part 6 has an air cut in N30 (tool moves without engaging: no tooth-passing
vibration, idle current), part 7 chatter in N20 (a growing resonance, a current ripple).
Context is added once per acquisition in acquisition time and mapped to both channels;
segments (trimmed by each channel's clock uncertainty), simple-v1 values, an air-cut label
and vibration envelopes are built through the public SQL API. Synthetic: for showing the viewer, not evidence.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import psycopg

COLL = "demo_spindle"
VIB_RATE, CUR_RATE, CUR_OFFSET_NS = 5000.0, 500.0, 2_000_000
# block, seconds, tooth-passing Hz, vibration amplitude, cutting current (A)
BLOCKS = [("N10", 6.0, 180.0, 1.0, 8.0), ("N20", 5.0, 240.0, 0.6, 6.0), ("N30", 3.0, 300.0, 0.8, 7.0),
          ("N40", 4.0, 150.0, 0.7, 5.0)]
IDLE_CURRENT = 3.0
ORIGIN = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


def vibration(part, rng):
    pieces = []
    for block, seconds, tooth_hz, amp, _ in BLOCKS:
        t = np.arange(int(seconds * VIB_RATE)) / VIB_RATE
        x = amp * np.sin(2 * np.pi * tooth_hz * t) * (1 + 0.3 * np.sin(2 * np.pi * 13.77 * t))
        x += 0.15 * np.sin(2 * np.pi * 13.77 * t) + rng.normal(0, 0.08, len(t))
        if part == 6 and block == "N30":
            x = 0.15 * np.sin(2 * np.pi * 13.77 * t) + rng.normal(0, 0.08, len(t))   # air cut
        if part == 7 and block == "N20":
            x += np.linspace(0, 1.2, len(t)) * np.sin(2 * np.pi * 870.0 * t)          # chatter
        pieces.append(x)
    return np.concatenate(pieces)


def current(part, rng):
    """Sampled on the acquisition time axis: sample n is at CUR_OFFSET_NS + n / CUR_RATE."""
    total = sum(b[1] for b in BLOCKS)
    t = CUR_OFFSET_NS / 1e9 + np.arange(int(total * CUR_RATE)) / CUR_RATE
    edges = np.cumsum([0.0] + [b[1] for b in BLOCKS])
    x = IDLE_CURRENT + 0.05 * np.sin(2 * np.pi * 13.77 * t) + rng.normal(0, 0.04, len(t))
    for (block, _, _, _, load), a, e in zip(BLOCKS, edges[:-1], edges[1:]):
        inside = (t >= a) & (t < e)
        if not (part == 6 and block == "N30"):
            x[inside] += load - IDLE_CURRENT
        if part == 7 and block == "N20":
            x[inside] += np.linspace(0, 0.8, inside.sum()) * np.sin(2 * np.pi * 37.0 * t[inside])  # chatter ripple
    return x


def append(conn, rid, x, quantum):
    q = np.round(x / quantum) * quantum
    for s in range(0, len(q), 32768):
        conn.execute("SELECT morlet_append_chunk(%s,%s,%s,%s::float8[])", (COLL, rid, s, q[s:s + 32768].tolist()))
    return len(q)


def main():
    dsn = os.environ.get("DATABASE_URL") or sys.exit("set DATABASE_URL")
    rng = np.random.default_rng(7)
    with psycopg.connect(dsn, autocommit=True) as conn:
        for t in ("_acq_channels", "_acq", "_envelopes", "_labels", "_ctx_values", "_ctx_segments", "_ctx", "_chunks",
                  "_segments", ""):
            conn.execute(f"DROP TABLE IF EXISTS {COLL}{t} CASCADE")
        conn.execute("SELECT morlet_create_stream_collection('public', %s)", (COLL,))
        for enable in ("morlet_enable_context", "morlet_enable_acquisitions", "morlet_enable_envelopes"):
            conn.execute(f"SELECT {enable}(%s)", (COLL,))
        for part in range(1, 9):
            acq, vib, cur = f"part-{part:03d}", f"part-{part:03d}-vib", f"part-{part:03d}-cur"
            meta = json.dumps({"machine": "M7", "part": part})
            conn.execute("SELECT morlet_create_acquisition(%s,%s,'M7',%s)",
                         (COLL, acq, ORIGIN + timedelta(minutes=15 * part)))
            conn.execute("SELECT morlet_start_recording(%s,%s,%s,'spindle_vib','g',%s,512,256,'zscore','none',0.001)",
                         (COLL, vib, VIB_RATE, meta))
            conn.execute("SELECT morlet_start_recording(%s,%s,%s,'spindle_current','A',%s,512,256,'zscore','none',0.001)",
                         (COLL, cur, CUR_RATE, meta))
            append(conn, vib, vibration(part, rng), 0.001)
            append(conn, cur, current(part, rng), 0.001)
            conn.execute("SELECT morlet_add_acquisition_channel(%s,%s,%s,'vib',0,'daq')", (COLL, acq, vib))
            conn.execute("SELECT morlet_add_acquisition_channel(%s,%s,%s,'current',%s,'wallclock',1000000)",
                         (COLL, acq, cur, CUR_OFFSET_NS))
            total_ns = int(sum(b[1] for b in BLOCKS) * 1e9)
            rows = [{"key": "program", "value": "O100", "t_start_ns": 0, "t_end_ns": total_ns},
                    {"key": "tool", "value": 7, "t_start_ns": 0, "t_end_ns": total_ns},
                    {"key": "part", "value": f"SN-{part:04d}", "t_start_ns": 0, "t_end_ns": total_ns},
                    {"key": "tool_change", "value": 7, "t_start_ns": 0}]
            t0, n30 = 0, None
            for block, seconds, *_ in BLOCKS:
                t1 = t0 + int(seconds * 1e9)
                rows.append({"key": "block", "value": block, "t_start_ns": t0, "t_end_ns": t1})
                if block == "N30":
                    n30 = (t0, t1)
                t0 = t1
            conn.execute("SELECT morlet_add_acquisition_context(%s,%s,'ctl',%s)", (COLL, acq, json.dumps(rows)))
            # Per channel, trimmed by the channel's clock uncertainty (1 ms on the current logger).
            conn.execute("SELECT morlet_build_acquisition_segments(%s,%s,ARRAY['program','tool','block'],ARRAY['part'])",
                         (COLL, acq))
            conn.execute("SELECT morlet_build_envelopes(%s,%s,4096,2048)", (COLL, vib))
            if part == 6:
                for rid, rate, offset in ((vib, VIB_RATE, 0), (cur, CUR_RATE, CUR_OFFSET_NS)):
                    a, e = conn.execute("SELECT start_sample,end_sample FROM morlet_time_to_samples(%s,%s,%s,%s)",
                                        (rate, offset, *n30)).fetchone()
                    conn.execute("SELECT morlet_add_label(%s,%s,%s,%s,'air_cut','synthetic demo')", (COLL, rid, a, e))
    print(f"created {COLL}: 8 acquisitions x 2 channels with context, segments, values, envelopes and an air-cut label")


if __name__ == "__main__":
    main()
