#!/usr/bin/env python3
"""Soak S1 probe: every --every seconds, measures what a user of the live archive would do, and appends one JSON line.

Per round, three stations are chosen in a fixed rotation (by round number), one channel each:
- latest:  morlet_latest(collection, 4096, {station, channel}): the newest 4,096 samples (latency, rows).
- stats:   morlet_interval_stats over the last hour of that channel's current recording (latency); skipped while the
           recording is shorter than an hour.
- search:  morlet_search_similar for the 2,048-sample window that ended an hour ago, over the same station and
           channel (latency, hits, or the error); skipped while the recording is shorter than 70 minutes.
Archive totals: recordings, stored samples, database size, and searchable coverage (stored samples covered by
envelopes). Latencies are wall-clock seconds measured here, including the round trip to the database.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

STATIONS = ["GE.WLF", "GE.STU", "GE.RUE", "GE.IBBN", "GE.PSZ", "GE.MORC",
            "CX.PB01", "CX.PB02", "CX.PB03", "CX.PB06", "CX.PB08", "CX.PB09"]
CHANNELS = ["HHZ", "HHN", "HHE"]


def timed(conn, sql, args):
    t = time.perf_counter()
    try:
        rows = conn.execute(sql, args).fetchall()
        return time.perf_counter() - t, rows, None
    except Exception as exc:  # noqa: BLE001 - recorded, the probe keeps running
        return time.perf_counter() - t, [], f"{type(exc).__name__}: {str(exc).splitlines()[0]}"


def round_(conn, c: str, k: int) -> dict:
    out = {"at": time.time(), "round": k, "checks": []}
    for j in range(3):
        net, sta = STATIONS[(3 * k + j) % len(STATIONS)].split(".")
        cha = CHANNELS[(k + j) % len(CHANNELS)]
        flt = json.dumps({"network": net, "station": sta, "channel": cha})
        chk = {"station": f"{net}.{sta}", "channel": cha}
        dt, rows, err = timed(conn, "SELECT recording_id, sample_count FROM morlet_latest(%s::regclass,4096,%s::jsonb)",
                              (c, flt))
        chk["latest"] = {"s": round(dt, 4), "rows": len(rows), "error": err}
        cur = conn.execute(f"SELECT recording_id, sample_rate_hz, (checkpoint->>'end')::bigint FROM {c} "
                           "WHERE metadata @> %s::jsonb ORDER BY (metadata->>'start_ns')::bigint DESC LIMIT 1",
                           (flt,)).fetchone()
        if cur:
            rid, rate, end = cur[0], cur[1], int(cur[2] or 0)
            hour = int(3600 * rate)
            if end >= hour:
                dt, rows, err = timed(conn, "SELECT * FROM morlet_interval_stats(%s::regclass,%s,%s,%s)",
                                      (c, rid, end - hour, end))
                chk["stats"] = {"s": round(dt, 4), "error": err}
            if end >= hour + int(600 * rate):
                q0 = end - hour - 2048
                dt, rows, err = timed(conn, "SELECT * FROM morlet_search_similar(%s::regclass,%s,%s,%s,%s::jsonb,10)",
                                      (c, rid, q0, q0 + 2048, json.dumps({"station": sta, "channel": cha})))
                chk["search"] = {"s": round(dt, 4), "hits": len(rows), "error": err}
        out["checks"].append(chk)
    tot = conn.execute(f"SELECT count(*), coalesce(sum((checkpoint->>'end')::bigint),0) FROM {c}").fetchone()
    out["recordings"], out["stored_samples"] = int(tot[0]), int(tot[1])
    out["db_bytes"] = int(conn.execute("SELECT pg_database_size(current_database())").fetchone()[0])
    try:
        cov = conn.execute("SELECT state, sum(end_sample - start_sample)::bigint FROM morlet_coverage(%s::regclass) "
                           "GROUP BY state", (c,)).fetchall()
        out["coverage"] = {s: int(n) for s, n in cov}
    except Exception as exc:  # noqa: BLE001
        out["coverage_error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="public.live")
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=float, default=900)
    ap.add_argument("--rounds", type=int, default=0, help="stop after this many rounds (0: run until stopped)")
    a = ap.parse_args(argv)
    import psycopg
    k = 0
    while not a.rounds or k < a.rounds:
        t0 = time.time()
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, application_name="soak-probe") as conn:
                rec = round_(conn, a.collection, k)
        except Exception as exc:  # noqa: BLE001 - a failed round is data too
            rec = {"at": t0, "round": k, "error": f"{type(exc).__name__}: {str(exc).splitlines()[0]}"}
        rec["round_s"] = round(time.time() - t0, 3)
        with open(a.out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        k += 1
        time.sleep(max(0.0, a.every - (time.time() - t0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
