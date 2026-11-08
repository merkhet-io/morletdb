#!/usr/bin/env python3
"""Power-cut study P1 (#190), the controller on a second machine. Per cycle: power on, wait for the database, check
everything the writer acknowledged before the last cut, let the writer run for a random 60-240 s, cut the power,
wait, repeat. One JSON line per cycle in --out. The switch is a Shelly plug (Gen2+ RPC Switch.Set) or, for a dry run,
a person (--switch manual).

Checks after each restart (all must hold; any failure is recorded with its details and the run continues):
- recovered: the database accepts a query within 10 minutes of power-on (time measured);
- acknowledged: recording `pc` ends at or after the last end the writer acknowledged before the cut;
- bit-exact: the last three acknowledged batches and one seeded random older batch read back equal to the generator;
- atomic: exactly one `batch` context row per stored batch, contiguous, none beyond the stored end;
- rolled back: recording `rb` has no sample and no context row;
- chunks: the reads above decode every chunk they touch (structure and CRC-32C); a full morlet_verify every 25th
  cycle;
- derived: no envelope beyond the stored end, every envelope on the 512 grid, no hole in the envelope sequence;
  interval statistics over the checked ranges (roll-ups and summaries where present) equal min/max/count of the
  generator;
- extension: the extension version is unchanged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import socketserver
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from writer import SEED, values  # noqa: E402

ACK = {"pc": 0, "t": 0.0, "lines": 0}
LOCK = threading.Lock()


class AckHandler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            with LOCK:
                ACK["lines"] += 1
                if m.get("rid") == "pc" and "event" not in m:
                    ACK["pc"] = max(ACK["pc"], int(m["end"]))
                    ACK["t"] = float(m["t"])


class Switch:
    def __init__(self, spec: str):
        self.spec = spec

    def set(self, on: bool) -> None:
        if self.spec == "manual":
            input(f"\n>>> power {'ON' if on else 'OFF'} now, then press Enter ")
            return
        if self.spec.startswith("cmd:"):   # cmd:<off command>;;<on command>, for a simulated crash (no power cut)
            off, on_cmd = self.spec[4:].split(";;", 1)
            import subprocess
            subprocess.run(on_cmd if on else off, shell=True, check=True)
            return
        host = self.spec.split(":", 1)[1]
        url = f"http://{host}/rpc/Switch.Set?id=0&on={'true' if on else 'false'}"
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    r.read()
                return
            except OSError:
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"switch did not answer: {url}")


def connect(dsn: str, timeout: float):
    import psycopg
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5, application_name="pc-controller")
            conn.execute("SELECT 1")
            return conn, time.time() - t0
        except psycopg.Error:
            time.sleep(2)
    return None, time.time() - t0


def check(conn, c: str, acked: int, batch: int, rng: random.Random, full: bool, version0: str | None) -> dict:
    """All queries in one read-only REPEATABLE READ transaction: the writer resumes after the restart and keeps
    committing while the checks run, so they must all see the same snapshot."""
    conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    try:
        return _check(conn, c, acked, batch, rng, full, version0)
    finally:
        conn.execute("ROLLBACK")


def _check(conn, c: str, acked: int, batch: int, rng: random.Random, full: bool, version0: str | None) -> dict:
    import numpy as np
    out: dict = {"problems": []}
    bad = out["problems"].append
    if conn.execute("SELECT to_regclass(%s)", (c,)).fetchone()[0] is None:
        if acked:
            bad(f"collection {c} missing although {acked} samples were acknowledged")
        else:
            out["note"] = "collection not created yet (first start)"
        return out
    end = int(conn.execute(f"SELECT coalesce((checkpoint->>'end')::bigint,0) FROM {c} WHERE recording_id='pc'")
              .fetchone()[0])
    out["stored_end"], out["acked_end"] = end, acked
    if end < acked:
        bad(f"lost acknowledged samples: stored {end} < acknowledged {acked}")
    if end % batch:
        bad(f"stored end {end} is not on a batch boundary")
    nb = end // batch
    ranges = [(max(0, acked - k * batch), max(0, acked - (k - 1) * batch)) for k in (3, 2, 1) if acked - k * batch >= 0]
    if nb > 4:
        b = rng.randrange(0, nb - 3)
        ranges.append((b * batch, (b + 1) * batch))
    for s0, s1 in ranges:
        if s1 <= s0:
            continue
        try:
            raw = conn.execute("SELECT morlet_read_interval_raw(%s::regclass,'pc',%s,%s)", (c, s0, s1)).fetchone()[0]
        except Exception as exc:  # noqa: BLE001 - a missing or unreadable range is a finding, not a crash
            bad(f"samples {s0}..{s1} unreadable: {str(exc).splitlines()[0]}")
            continue
        got = np.frombuffer(raw, dtype="<f8")
        want = values(SEED["pc"], s0, s1 - s0)
        if got.shape != want.shape or not np.array_equal(got, want):
            bad(f"samples {s0}..{s1} differ from the generator")
        st = conn.execute("SELECT sample_count, min, max FROM morlet_interval_stats(%s::regclass,'pc',%s,%s)",
                          (c, s0, s1)).fetchone()
        if (int(st[0]), st[1], st[2]) != (s1 - s0, float(want.min()), float(want.max())):
            bad(f"interval statistics {s0}..{s1} differ: {st}")
    ctx = conn.execute(f"SELECT count(*), coalesce(min(start_sample),0), coalesce(max(end_sample),0), "
                       f"count(DISTINCT start_sample) FROM {c}_ctx WHERE recording_id='pc' AND key='batch'").fetchone()
    if (int(ctx[0]), int(ctx[3])) != (nb, nb) or (nb and (int(ctx[1]), int(ctx[2])) != (0, end)):
        bad(f"context rows {tuple(ctx)} do not match {nb} stored batches ending at {end}")
    rb = conn.execute(f"SELECT coalesce((checkpoint->>'end')::bigint,0) FROM {c} WHERE recording_id='rb'").fetchone()
    rbctx = conn.execute(f"SELECT count(*) FROM {c}_ctx WHERE recording_id='rb'").fetchone()[0]
    if (rb and int(rb[0])) or rbctx:
        bad(f"rolled-back writes visible: rb end {rb and rb[0]}, context rows {rbctx}")
    env = conn.execute(f"SELECT count(*), coalesce(max(start_sample),-1), "
                       f"count(*) FILTER (WHERE start_sample %% 512 <> 0), "
                       f"count(*) FILTER (WHERE start_sample + window_size > %s) "
                       f"FROM {c}_envelopes WHERE recording_id='pc' AND window_size=2048", (end,)).fetchone()
    n, mx, off, beyond = (int(x) for x in env)
    out["envelopes"] = n
    if off or beyond or (n and n != mx // 512 + 1):
        bad(f"envelopes inconsistent: {n} rows, last at {mx}, {off} off the grid, {beyond} beyond the end")
    v = conn.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]
    out["extension"] = v
    if version0 and v != version0:
        bad(f"extension version changed from {version0} to {v}")
    if full:
        t = time.time()
        rows = conn.execute("SELECT recording_id, chunks, problems FROM morlet_verify(%s::regclass)", (c,)).fetchall()
        out["verify_s"], out["verify_chunks"] = round(time.time() - t, 1), sum(int(r[1] or 0) for r in rows)
        for rid, _, problems in rows:
            if problems:
                bad(f"morlet_verify {rid}: {problems[:3]}")
    return out


def quiet_hour(now: dt.datetime) -> bool:
    """02:45-03:45 local: the paperless backup mounts a disk on the box; no cut while it runs."""
    t = now.hour * 60 + now.minute
    return 165 <= t < 225


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", required=True, help="shelly:<host>, manual, or cmd:<off>;;<on> (simulated crash)")
    ap.add_argument("--dsn", default=os.environ.get("PC_DSN"), help="the box's database (or PC_DSN)")
    ap.add_argument("--collection", default="pc_study")
    ap.add_argument("--cycles", type=int, default=300)
    ap.add_argument("--listen", type=int, default=7190)
    ap.add_argument("--batch", type=int, default=4096)
    ap.add_argument("--run-min", type=float, default=60)
    ap.add_argument("--run-max", type=float, default=240)
    ap.add_argument("--off-s", type=float, default=15)
    ap.add_argument("--seed", type=int, default=190)
    ap.add_argument("--verify-every", type=int, default=25, help="full morlet_verify every n-th cycle")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    srv = socketserver.ThreadingTCPServer(("0.0.0.0", a.listen), AckHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sw, rng = Switch(a.switch), random.Random(a.seed)
    version0, acked_at_cut, first, last_cut = None, 0, True, 0.0
    for k in range(a.cycles):
        while quiet_hour(dt.datetime.now()):
            time.sleep(60)
        rec = {"cycle": k, "on_at": time.time()}
        sw.set(True)
        conn, rec["recovered_s"] = connect(a.dsn, 600)
        if last_cut:
            rec["since_cut_s"] = round(time.time() - last_cut, 1)   # cut to database answering (includes boot)
        if conn is None:
            rec["problems"] = ["database not reachable 10 minutes after power-on"]
        else:
            try:
                res = check(conn, a.collection, 0 if first else acked_at_cut, a.batch, rng,
                            k % a.verify_every == a.verify_every - 1, version0)
            except Exception as exc:  # noqa: BLE001 - recorded; the next cycle checks again
                res = {"problems": [f"check failed: {type(exc).__name__}: {str(exc).splitlines()[0]}"]}
            version0 = version0 or res.get("extension")
            rec.update(res)
            conn.close()
        first = False
        run = rng.uniform(a.run_min, a.run_max)
        time.sleep(run)
        with LOCK:
            acked_at_cut, rec["ack_lines"] = ACK["pc"], ACK["lines"]
            rec["last_ack_age_s"] = round(time.time() - ACK["t"], 2) if ACK["t"] else None
        rec["run_s"], rec["cut_at"], rec["acked_at_cut"] = round(run, 1), time.time(), acked_at_cut
        last_cut = rec["cut_at"]
        sw.set(False)
        with open(a.out, "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"cycle {k}: recovered {rec.get('recovered_s', 0):.0f}s, stored {rec.get('stored_end')}, "
              f"problems {len(rec.get('problems', []))}", flush=True)
        time.sleep(a.off_s)
    sw.set(True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
