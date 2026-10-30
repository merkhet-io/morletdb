#!/usr/bin/env python3
"""Sleep retrieval demo: stage an EEG epoch by its most similar expert-scored epochs.

    MORLET_DSN="host=localhost port=28817 dbname=morlet_sleep_demo" \
        .venv-seismic/bin/python -I examples/sleep/app.py [--port 8766]

Needs a database prepared by load.py. A held-out subject's epoch is compared with the
development subjects' epochs (other people only) through the pgvector index on pg_morlet
features. The 40 nearest vote, as in S1's M-KNN arm. Every neighbour is returned as a
recording and sample interval and its waveform is read back from MorletDB.

Binds to 127.0.0.1 only, answers only requests addressed to 127.0.0.1/localhost on its port,
uses read-only sessions with a statement timeout, and the page loads nothing from the
network. A research demo, not a medical device.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "evaluation" / "sleep"))
import s1  # noqa: E402  (vote rule and kappa as frozen in S1)

COLLECTION = "sleep_eeg"
K = 40                       # S1's selected k for M-KNN
EF_SEARCH = 200
THUMB_BUCKETS = 150
STAGES = list(s1.STAGES)

NEIGHBOURS = f"""
    SELECT g.recording_id, g.epoch, g.subject, g.night, g.stage, g.start_sample,
           g.emb <-> q.emb AS distance
    FROM sleep_epochs q, LATERAL (
        SELECT * FROM sleep_epochs g WHERE g.split = 'development'
        ORDER BY g.emb <-> q.emb LIMIT {K}) g
    WHERE q.recording_id = %s AND q.epoch = %s
    ORDER BY distance, g.recording_id, g.epoch"""

NIGHT = f"""
    SELECT q.epoch, q.stage, n.stages
    FROM sleep_epochs q, LATERAL (
        SELECT array_agg(g.stage ORDER BY g.d) AS stages FROM (
            SELECT g.stage, g.emb <-> q.emb AS d FROM sleep_epochs g WHERE g.split = 'development'
            ORDER BY g.emb <-> q.emb LIMIT {K}) g) n
    WHERE q.recording_id = %s
    ORDER BY q.epoch"""


def min_max_buckets(x: np.ndarray, buckets: int):
    edges = np.linspace(0, len(x), buckets + 1).astype(int)
    return (np.round(np.minimum.reduceat(x, edges[:-1]), 2).tolist(),
            np.round(np.maximum.reduceat(x, edges[:-1]), 2).tolist())


def clock(first_clock: str, offset_epochs: int) -> str:
    hh, mm, ss = (int(v) for v in first_clock.split(":"))
    t = (hh * 3600 + mm * 60 + ss + offset_epochs * 30) % 86400
    return f"{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d}"


class Demo:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.nights_cache: dict[str, dict] = {}
        self.lock = threading.Lock()
        with self.connect() as con:
            rows = con.execute(f"SELECT recording_id, metadata FROM {COLLECTION} ORDER BY recording_id").fetchall()
            self.recordings = {r["recording_id"]: r["metadata"] for r in rows}
            counts = con.execute("SELECT recording_id, count(*) AS n FROM sleep_epochs GROUP BY 1").fetchall()
            self.counts = {r["recording_id"]: r["n"] for r in counts}
        if not self.recordings:
            raise SystemExit("no recordings; run load.py first")

    def connect(self):
        con = psycopg.connect(self.dsn, row_factory=dict_row,
                              options="-c default_transaction_read_only=on -c statement_timeout=60s "
                                      f"-c hnsw.ef_search={EF_SEARCH} -c hnsw.iterative_scan=strict_order")
        con.read_only = True
        return con

    def recording(self, rid: str) -> dict:
        if rid not in self.recordings:
            raise LookupError(f"unknown recording {rid!r}")
        return self.recordings[rid]

    def describe(self, rid: str, epoch: int) -> dict:
        m = self.recording(rid)
        return {"recording_id": rid, "subject": m["subject"], "night": m["night"], "epoch": epoch,
                "clock": clock(m["first_clock"], epoch - m["first_epoch"])}

    def nights(self) -> dict:
        out = [{"recording_id": rid, "subject": m["subject"], "night": m["night"], "epochs": self.counts.get(rid, 0),
                "first_clock": m["first_clock"]}
               for rid, m in self.recordings.items() if m["split"] == "held-out"]
        gallery = [rid for rid, m in self.recordings.items() if m["split"] == "development"]
        return {"nights": out, "gallery": {"recordings": len(gallery),
                                           "subjects": len({self.recordings[r]["subject"] for r in gallery}),
                                           "epochs": sum(self.counts.get(r, 0) for r in gallery)},
                "k": K, "stages": STAGES}

    def night(self, rid: str) -> dict:
        m = self.recording(rid)
        if m["split"] != "held-out":
            raise LookupError("only held-out nights are queries; development nights are the gallery")
        with self.lock:
            if rid in self.nights_cache:
                return self.nights_cache[rid]
        with self.connect() as con:
            rows = con.execute(NIGHT, (rid,)).fetchall()
        labels = np.array([r["stages"] for r in rows])
        pred = s1.vote(labels)
        y = np.array([r["stage"] for r in rows])
        out = {**self.describe(rid, rows[0]["epoch"]), "first_epoch": m["first_epoch"],
               "epochs": [r["epoch"] for r in rows], "expert": y.tolist(), "retrieved": pred.tolist(),
               "agreement": np.round(np.mean(labels == pred[:, None], 1), 3).tolist(),
               "kappa": round(float(s1.kappa(y, pred)), 3), "accuracy": round(float(np.mean(y == pred)), 3)}
        with self.lock:
            self.nights_cache[rid] = out
        return out

    def read(self, con, rid: str, start: int) -> np.ndarray:
        row = con.execute("SELECT samples FROM morlet_read_interval(%s, %s, %s, %s)",
                          (COLLECTION, rid, start, start + s1.EPOCH)).fetchone()
        return np.asarray(row["samples"], float)

    def epoch(self, rid: str, epoch: int) -> dict:
        if self.recording(rid)["split"] != "held-out":
            raise LookupError("only held-out epochs are queries")
        with self.connect() as con:
            q = con.execute("SELECT stage, start_sample, emb::text AS emb FROM sleep_epochs "
                            "WHERE recording_id = %s AND epoch = %s", (rid, epoch)).fetchone()
            if q is None:
                raise LookupError(f"{rid} has no scored epoch {epoch}")
            nb = con.execute(NEIGHBOURS, (rid, epoch)).fetchall()
            x = self.read(con, rid, q["start_sample"])
            neighbours = []
            for r in nb:
                w = self.read(con, r["recording_id"], r["start_sample"])
                lo, hi = min_max_buckets(w, THUMB_BUCKETS)
                neighbours.append({**self.describe(r["recording_id"], r["epoch"]), "stage": r["stage"],
                                   "distance": round(float(r["distance"]), 4), "start_sample": r["start_sample"],
                                   "end_sample": r["start_sample"] + s1.EPOCH, "lo": lo, "hi": hi})
        labels = np.array([[r["stage"] for r in nb]])
        pred = int(s1.vote(labels)[0])
        return {**self.describe(rid, epoch), "stage": q["stage"], "retrieved": pred,
                "votes": np.bincount(labels[0], minlength=len(STAGES)).tolist(),
                "start_sample": q["start_sample"], "end_sample": q["start_sample"] + s1.EPOCH,
                "samples": np.round(x, 2).tolist(), "neighbours": neighbours}

    def interval(self, rid: str, epoch: int) -> dict:
        with self.connect() as con:
            q = con.execute("SELECT stage, start_sample FROM sleep_epochs WHERE recording_id = %s AND epoch = %s",
                            (rid, epoch)).fetchone()
            if q is None:
                raise LookupError(f"{rid} has no scored epoch {epoch}")
            x = self.read(con, rid, q["start_sample"])
        return {**self.describe(rid, epoch), "stage": q["stage"], "start_sample": q["start_sample"],
                "end_sample": q["start_sample"] + s1.EPOCH, "samples": np.round(x, 2).tolist()}


def api(demo: Demo, path: str, q: dict):
    def arg(name, cast=str):
        if name not in q:
            raise ValueError(f"missing {name}")
        return cast(q[name][0])

    if path == "/api/nights":
        return demo.nights()
    if path == "/api/night":
        return demo.night(arg("recording"))
    if path == "/api/epoch":
        return demo.epoch(arg("recording"), arg("epoch", int))
    if path == "/api/interval":
        return demo.interval(arg("recording"), arg("epoch", int))
    raise LookupError(f"unknown endpoint {path}")


class Handler(BaseHTTPRequestHandler):
    demo: Demo = None
    port: int = 0

    def host_ok(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def log_message(self, fmt, *args):  # quiet; errors are returned to the page
        pass

    def send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.host_ok():
            return self.send(421, b"misdirected request", "text/plain")
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if not url.path.startswith("/api/"):
            return self.send(404, b"not found", "text/plain")
        try:
            body, code = api(self.demo, url.path, parse_qs(url.query)), 200
        except (LookupError, ValueError) as e:
            body, code = {"error": str(e)}, 400
        except psycopg.Error as e:
            body, code = {"error": (e.diag.message_primary or str(e)).strip()}, 400
        self.send(code, json.dumps(body).encode(), "application/json")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--dsn", default=os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_sleep_demo"))
    a = p.parse_args()
    Handler.demo = Demo(a.dsn)
    Handler.port = a.port
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Sleep retrieval demo on http://127.0.0.1:{a.port} (read-only)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
