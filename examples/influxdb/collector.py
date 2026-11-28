#!/usr/bin/env python3
"""Synthetic collector for the InfluxDB example: two pumps, vibration at 4 kHz, stored in MorletDB, and for every
window of 4096 samples its metrics published to the bridge ledger in the same transaction (examples/influxdb).

Ten minutes of history first, then live in real time. Pump B has impact-like bursts in minutes 6 to 7 of the
history, so the RMS and peak trends in Grafana show something worth opening. Synthetic data only.
"""
import math
import os
import random
import sys
import time

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, "/opt/morlet/bridge")
from morlet_bridge import Bridge  # noqa: E402

RATE, WINDOW, HISTORY_S = 4000, 4096, 600
PUMPS = {"pump-A": 50.0, "pump-B": 47.5}              # running speed in Hz


def window(rnd, pump, n0, burst):
    f = PUMPS[pump]
    out = []
    for i in range(WINDOW):
        t = (n0 + i) / RATE
        v = 0.4 * math.sin(2 * math.pi * f * t) + 0.08 * math.sin(2 * math.pi * 3 * f * t) + rnd.gauss(0, 0.02)
        if burst and (n0 + i) % 400 < 12:                # impacts, about 10 per second, decaying
            v += 1.5 * math.exp(-((n0 + i) % 400) / 3) * math.sin(2 * math.pi * 1800 * t)
        out.append(round(v, 4))                          # a 0.1 mg grid, as an ADC with calibration would give
    return out


def setup(conn, b):
    if conn.execute("SELECT to_regclass('public.vibration')").fetchone()[0] is None:
        conn.execute("SELECT morlet_create_stream_collection('public', 'vibration')")
        conn.execute("SELECT morlet_enable_envelopes('public.vibration'::regclass)")
    if conn.execute("SELECT to_regnamespace('morlet_bridge')").fetchone()[0] is None:
        b.install()
    with b.transaction():
        b.add_sink("telegraf", "line", options={"measurement": "vibration", "tags": {"site": "demo"}})


def main():
    rnd = random.Random(7)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        b = Bridge(conn)
        setup(conn, b)
        start_ns = (time.time_ns() - HISTORY_S * 10 ** 9) // 10 ** 9 * 10 ** 9
        sources, ends = {}, {}
        for pump in PUMPS:
            rid = f"{pump}-{start_ns // 10 ** 9}"
            conn.execute("SELECT morlet_start_recording('vibration', %s, %s, 'vibration', 'g', %s, sample_quantum => 0.0001)",
                         (rid, RATE, Jsonb({"start_ns": start_ns, "asset": pump})))
            with b.transaction():
                sources[pump] = (rid, b.register("public.vibration", rid, pump, "vib-x", WINDOW))
            ends[pump] = 0
        print(f"collector: recordings from {start_ns}, history {HISTORY_S} s, then live", flush=True)
        while True:
            for pump, (rid, sid) in sources.items():
                n0 = ends[pump]
                due = start_ns + (n0 + WINDOW) * 10 ** 9 // RATE
                if due > time.time_ns():
                    continue
                burst = pump == "pump-B" and 360 * RATE <= n0 < 420 * RATE
                with b.transaction():          # the waveform and its metrics commit or roll back together
                    conn.execute("SELECT morlet_append_chunk('vibration', %s, %s, %s::float8[])",
                                 (rid, n0, window(rnd, pump, n0, burst)))
                    b.publish(sid, n0, n0 + WINDOW)
                ends[pump] = n0 + WINDOW
            time.sleep(0.05)


if __name__ == "__main__":
    main()
