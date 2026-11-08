#!/usr/bin/env python3
"""Power-cut study P1 (#190), the writer on the box whose power is cut. It starts at boot (container with a restart
policy), resumes where the database says the recording ends, and appends forever:

- **One transaction per batch:** `--batch` samples appended to recording `pc` (chunk summaries, envelopes and
  roll-ups enabled on the collection) and one context row `{"key": "batch", "value": <n>}` covering exactly those
  samples, added with the batch's own id. Both are durable together or not at all.
- **A rolled-back transaction every 10th batch:** the same kind of write to recording `rb`, then ROLLBACK. `rb` must
  never contain a sample or a context row.
- **Acknowledgement off the box:** after each COMMIT returns, one line `{"rid","end","batch","t"}` goes over TCP to
  the controller. Everything the controller received was acknowledged as durable before the power went.

Sample i of a recording is `values(seed, i)`: integers, so the checker can regenerate any range bit for bit.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time

import numpy as np

SEED = {"pc": 190, "rb": 191}


def values(seed: int, start: int, n: int) -> np.ndarray:
    """Sample start..start+n of the recording with this seed: integer counts in [-10000, 10000], as float64."""
    i = np.arange(start, start + n, dtype=np.uint64)
    h = (i * np.uint64(2654435761) + np.uint64(seed) * np.uint64(97)) % np.uint64(4294967296)
    return (h % np.uint64(20001)).astype(np.int64).astype(np.float64) - 10000.0


def setup(conn, c: str) -> None:
    with conn.transaction():
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        if conn.execute("SELECT to_regclass(%s)", (c,)).fetchone()[0] is None:
            conn.execute("SELECT morlet_create_stream_collection('public',%s)", (c,))
            conn.execute("SELECT morlet_enable_envelopes(%s::regclass)", (c,))
            conn.execute("SELECT morlet_enable_rollups(%s::regclass)", (c,))
            conn.execute("SELECT morlet_enable_context(%s::regclass)", (c,))
        for rid in ("pc", "rb"):
            conn.execute("SELECT morlet_ensure_recording(%s::regclass,%s,1000,'x','counts','{}',2048,512,'zscore',"
                         "'morlet-energy-v1')", (c, rid))


def stored_end(conn, c: str, rid: str) -> int:
    r = conn.execute(f"SELECT (checkpoint->>'end')::bigint FROM {c} WHERE recording_id=%s", (rid,)).fetchone()
    return int(r[0] or 0) if r else 0


def write_batch(conn, c: str, rid: str, start: int, n: int, chunk: int) -> None:
    raw = values(SEED[rid], start, n).astype("<f8").tobytes()
    conn.execute("SELECT morlet_append_chunks_raw(%s::regclass,%s,%s,%b,%s)", (c, rid, start, raw, chunk))
    batch = start // n
    conn.execute("SELECT morlet_add_context(%s::regclass,%s,%s::jsonb)",
                 (c, f"{rid}-{start}", json.dumps([{"recording_id": rid, "key": "batch", "value": batch,
                                                     "start_sample": start, "end_sample": start + n}])))


class Acks:
    """Line-oriented TCP to the controller; reconnects quietly. A lost ack only makes the check weaker, never wrong."""

    def __init__(self, addr: str):
        host, port = addr.rsplit(":", 1)
        self.addr, self.sock, self.next_try = (host, int(port)), None, 0.0

    def send(self, msg: dict) -> None:
        if self.sock is None and time.time() >= self.next_try:
            try:
                self.sock = socket.create_connection(self.addr, timeout=2)
            except OSError:
                self.next_try = time.time() + 5
        if self.sock is not None:
            try:
                self.sock.sendall((json.dumps(msg) + "\n").encode())
            except OSError:
                self.sock.close()
                self.sock = None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="pc_study")
    ap.add_argument("--ack", required=True, help="controller host:port")
    ap.add_argument("--batch", type=int, default=4096, help="samples per transaction")
    ap.add_argument("--chunk", type=int, default=4096)
    ap.add_argument("--pause", type=float, default=0.25, help="seconds between batches")
    a = ap.parse_args(argv)
    import psycopg
    while True:   # the database may still be recovering after a power cut
        try:
            conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, application_name="pc-writer")
            break
        except psycopg.OperationalError:
            time.sleep(2)
    setup(conn, a.collection)
    acks = Acks(a.ack)
    end = stored_end(conn, a.collection, "pc")
    acks.send({"event": "start", "rid": "pc", "end": end, "t": time.time()})
    k = 0
    while True:
        with conn.transaction():
            write_batch(conn, a.collection, "pc", end, a.batch, a.chunk)
        end += a.batch
        acks.send({"rid": "pc", "end": end, "batch": end // a.batch, "t": time.time()})
        k += 1
        if k % 10 == 0:
            try:
                with conn.transaction():
                    write_batch(conn, a.collection, "rb", stored_end(conn, a.collection, "rb"), a.batch, a.chunk)
                    raise _Rollback
            except _Rollback:
                pass
        time.sleep(a.pause)


class _Rollback(Exception):
    pass


if __name__ == "__main__":
    sys.exit(main())
