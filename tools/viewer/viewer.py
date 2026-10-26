#!/usr/bin/env python3
"""MorletDB viewer: a local, read-only web page for recordings, context and comparisons.

    DATABASE_URL=postgresql://... python tools/viewer/viewer.py [--port 8765]

Binds to 127.0.0.1 only and answers only requests addressed to 127.0.0.1/localhost on its port
(DNS-rebinding guard). Reads use read-only sessions (default_transaction_read_only) with a
statement timeout. With --allow-labels it also accepts two writes, adding and retracting
labels, on a separate connection; each write needs this run's random token (only the served
page has it, sent as a custom header), a JSON body and, if the browser sends one, this
server's Origin. It uses the caller's role, so table permissions and row-level security apply
unchanged. Collection names are only accepted if they are discovered stream collections; all
values go through query parameters.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

HERE = Path(__file__).resolve().parent
READ_PIECE = 32768          # morlet_read_interval limit per call
MAX_SPAN = 1 << 22          # samples per overview request (about 2.7 min at 25.6 kHz)
MAX_BUCKETS = 4000
ENVELOPE_WINDOWS = (512, 1024, 2048, 4096, 8192, 16384, 32768)
PUBLIC = ("name", "context", "envelopes", "acquisitions")
SCORE_VALUES = ["duration_s", "std", "crest", "kurtosis"]   # evaluated default (C2, C3)
LEVEL_VALUES = ["p75"]                                      # level signals such as current (C1)


def min_max_buckets(samples: np.ndarray, buckets: int):
    """Per-bucket min and max of consecutive samples (the waveform's pixel envelope)."""
    n = len(samples)
    buckets = max(1, min(buckets, n))
    edges = np.linspace(0, n, buckets + 1).astype(int)
    lo = np.minimum.reduceat(samples, edges[:-1])
    hi = np.maximum.reduceat(samples, edges[:-1])
    return edges[:-1].tolist(), lo.tolist(), hi.tolist()


def snap_window(start: int, end: int, recording_end: int):
    """Largest supported envelope window within the selection, centred on it."""
    length = end - start
    fitting = [w for w in ENVELOPE_WINDOWS if w <= length]
    if not fitting:
        return None
    w = fitting[-1]
    s = max(0, min(start + (length - w) // 2, recording_end - w))
    return s, s + w


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def connect(self):
        # Read-only from the first statement on: as session options (applied at connection
        # start) and as psycopg's transaction characteristic. A SET issued after connecting
        # would leave the transaction it opens read-write.
        conn = psycopg.connect(self.dsn, row_factory=dict_row,
                               options="-c default_transaction_read_only=on -c statement_timeout=20s")
        conn.read_only = True
        return conn

    def collections(self, conn):
        rows = conn.execute("""
            SELECT format('%I.%I', n.nspname, c.relname) AS name,
                   to_regclass(format('%I.%I', n.nspname, c.relname || '_ctx')) IS NOT NULL AS context,
                   to_regclass(format('%I.%I', n.nspname, c.relname || '_envelopes')) IS NOT NULL AS envelopes,
                   to_regclass(format('%I.%I', n.nspname, c.relname || '_acq_channels')) IS NOT NULL AS acquisitions,
                   format('%I.%I', n.nspname, c.relname || '_acq') AS acq_rel,
                   format('%I.%I', n.nspname, c.relname || '_acq_channels') AS channels_rel,
                   format('%I.%I', n.nspname, c.relname || '_ctx') AS ctx_rel,
                   format('%I.%I', n.nspname, c.relname || '_ctx_segments') AS segments_rel,
                   format('%I.%I', n.nspname, c.relname || '_labels') AS labels_rel
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
              AND to_regclass(format('%I.%I', n.nspname, c.relname || '_chunks')) IS NOT NULL
              AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'checkpoint'
                          AND NOT a.attisdropped)
              AND has_table_privilege(c.oid, 'SELECT')
            ORDER BY 1""").fetchall()
        return rows

    def collection(self, conn, name: str):
        for c in self.collections(conn):
            if c["name"] == name:
                return c
        raise LookupError("unknown collection")


def api(db: Database, path: str, q: dict):
    def arg(name, cast=str, default=None):
        v = q.get(name, [default])[0]
        if v is None:
            raise ValueError(f"missing parameter {name}")
        return cast(v)

    with db.connect() as conn:
        if path == "/api/collections":
            return {"collections": [{k: c[k] for k in PUBLIC} for c in db.collections(conn)]}
        coll = db.collection(conn, arg("collection"))
        name = coll["name"]
        if path == "/api/recordings":
            rows = conn.execute(f"""
                SELECT recording_id, sample_rate_hz, channel, unit, metadata,
                       (checkpoint->>'end')::bigint AS samples, checkpoint->>'extractor_version' AS extractor
                FROM {name} ORDER BY recording_id LIMIT 5000""").fetchall()
            return {"collection": {k: coll[k] for k in PUBLIC}, "recordings": rows}
        if path == "/api/compare":
            if not coll["context"]:
                raise ValueError("context is not enabled for this collection")
            seg = arg("segment", int)
            labels = []
            if q.get("exclude_labelled", ["1"])[0] == "1":
                labels = [r["label"] for r in conn.execute(
                    f"SELECT DISTINCT label FROM {coll['labels_rel']} WHERE NOT retracted").fetchall()]
            before = q.get("before_only", ["0"])[0] == "1"
            score_values = SCORE_VALUES + (LEVEL_VALUES if q.get("levels", ["0"])[0] == "1" else [])
            summary = conn.execute("""SELECT * FROM morlet_compare_summary(%s, %s, exclude_labels => %s,
                                      before_only => %s, score_values => %s)""",
                                   (name, seg, labels, before, score_values)).fetchone()
            values = conn.execute("SELECT * FROM morlet_compare_to_history(%s, %s, exclude_labels => %s, before_only => %s)",
                                  (name, seg, labels, before)).fetchall()
            channels = []
            if coll["acquisitions"]:
                s = conn.execute(f"""SELECT c.acquisition_id, s.context, s.occurrence FROM {coll['segments_rel']} s
                                     JOIN {coll['channels_rel']} c ON c.recording_id = s.recording_id
                                     WHERE s.segment_id = %s""", (seg,)).fetchone()
                if s:
                    channels = conn.execute("""SELECT * FROM morlet_compare_acquisition(%s, %s, %s, %s,
                                                 exclude_labels => %s, before_only => %s, score_values => %s)""",
                                            (name, s["acquisition_id"], Jsonb(s["context"]),
                                             Jsonb(s["occurrence"]), labels, before,
                                             score_values)).fetchall()
            neighbors = conn.execute("""SELECT * FROM morlet_history_neighbors(%s, %s, exclude_labels => %s,
                                          before_only => %s, score_values => %s, top_k => 1000)""",
                                     (name, seg, labels, before, score_values)).fetchall()
            return {"segment": seg, "summary": summary, "values": values, "channels": channels,
                    "score_values": score_values, "neighbors": neighbors}
        rec = arg("recording")
        meta = conn.execute(f"""SELECT recording_id, sample_rate_hz, channel, unit, metadata,
                                       (checkpoint->>'end')::bigint AS samples FROM {name} WHERE recording_id = %s""",
                            (rec,)).fetchone()
        if meta is None:
            raise LookupError("unknown recording")
        if path == "/api/acquisition":
            if not coll["acquisitions"]:
                return {"acquisition": None, "channels": []}
            acq = conn.execute(f"""SELECT a.* FROM {coll['acq_rel']} a JOIN {coll['channels_rel']} c
                                   ON c.acquisition_id = a.acquisition_id WHERE c.recording_id = %s""", (rec,)).fetchone()
            if acq is None:
                return {"acquisition": None, "channels": []}
            channels = conn.execute(f"""SELECT c.role, c.recording_id, c.offset_ns, c.clock_source, c.offset_uncertainty_ns,
                                               r.sample_rate_hz, r.channel, r.unit, (r.checkpoint->>'end')::bigint AS samples
                                        FROM {coll['channels_rel']} c JOIN {name} r ON r.recording_id = c.recording_id
                                        WHERE c.acquisition_id = %s ORDER BY c.role""", (acq["acquisition_id"],)).fetchall()
            return {"acquisition": acq, "channels": channels}
        if path == "/api/overview":
            start, end = max(0, arg("start", int)), min(meta["samples"], arg("end", int))
            if end <= start:
                raise ValueError("empty interval")
            if end - start > MAX_SPAN:
                raise ValueError(f"interval longer than {MAX_SPAN} samples; zoom in")
            parts = []
            for s in range(start, end, READ_PIECE):
                r = conn.execute("SELECT samples FROM morlet_read_interval(%s, %s, %s, %s)",
                                 (name, rec, s, min(s + READ_PIECE, end)), binary=True).fetchone()
                parts.append(np.asarray(r["samples"], float))
            samples = np.concatenate(parts)
            buckets = min(arg("buckets", int, "1200"), MAX_BUCKETS)
            if end - start <= buckets:
                return {"recording": meta, "start": start, "end": end, "raw": samples.tolist()}
            offsets, lo, hi = min_max_buckets(samples, buckets)
            return {"recording": meta, "start": start, "end": end,
                    "offsets": [start + o for o in offsets], "min": lo, "max": hi}
        if path == "/api/context":
            out = {"recording": meta, "intervals": [], "events": [], "segments": [], "labels": []}
            if not coll["context"]:
                return out
            start, end = arg("start", int), arg("end", int)
            ctx, seg, lab = coll["ctx_rel"], coll["segments_rel"], coll["labels_rel"]
            rows = conn.execute(f"""
                SELECT x.ctx_id, x.kind, x.key, x.value, x.start_sample, x.end_sample FROM {ctx} x
                WHERE x.recording_id = %s AND NOT x.retracted
                  AND NOT EXISTS (SELECT 1 FROM {ctx} y WHERE y.supersedes = x.ctx_id)
                  AND x.start_sample < %s AND coalesce(x.end_sample, x.start_sample + 1) > %s
                ORDER BY x.key, x.start_sample LIMIT 5000""", (rec, end, start)).fetchall()
            out["intervals"] = [r for r in rows if r["kind"] == "interval"]
            out["events"] = [r for r in rows if r["kind"] == "event"]
            out["segments"] = conn.execute(f"""
                SELECT segment_id, start_sample, end_sample, context, occurrence FROM {seg}
                WHERE recording_id = %s AND NOT stale AND start_sample < %s AND end_sample > %s
                ORDER BY start_sample LIMIT 5000""", (rec, end, start)).fetchall()
            out["labels"] = conn.execute(f"""
                SELECT label_id, label, note, author, start_sample, end_sample FROM {lab}
                WHERE recording_id = %s AND NOT retracted AND start_sample < %s AND end_sample > %s
                ORDER BY start_sample LIMIT 5000""", (rec, end, start)).fetchall()
            return out
        if path == "/api/similar":
            if not coll["envelopes"]:
                raise ValueError("envelopes are not enabled for this collection (morlet_enable_envelopes)")
            window = snap_window(arg("start", int), arg("end", int), meta["samples"])
            if window is None:
                raise ValueError("select at least 512 samples")
            hits = conn.execute("SELECT * FROM morlet_search_envelope_interval(%s, %s, %s, %s, '{}', 10)",
                                (name, rec, *window)).fetchall()
            return {"window": window, "hits": hits}
    raise LookupError("unknown endpoint")


def write_label(db: Database, path: str, body: dict):
    """The only writes: morlet_add_label and morlet_retract_label, validated like reads."""
    with db.connect() as rconn:
        coll = db.collection(rconn, str(body.get("collection", "")))
    if not coll["context"]:
        raise ValueError("context is not enabled for this collection")
    with psycopg.connect(db.dsn, row_factory=dict_row, options="-c statement_timeout=20s") as conn:
        if path == "/api/label":
            label = str(body.get("label", "")).strip()
            if not label or len(label) > 100 or len(str(body.get("note", ""))) > 1000:
                raise ValueError("label must have 1..100 characters and the note at most 1000")
            row = conn.execute("SELECT morlet_add_label(%s, %s, %s, %s, %s, %s) AS label_id",
                               (coll["name"], str(body["recording"]), int(body["start"]), int(body["end"]), label,
                                str(body.get("note", "")))).fetchone()
            return {"label_id": row["label_id"]}
        if path == "/api/retract_label":
            conn.execute("SELECT morlet_retract_label(%s, %s)", (coll["name"], int(body["label_id"])))
            return {"retracted": int(body["label_id"])}
    raise LookupError("unknown endpoint")


class Handler(BaseHTTPRequestHandler):
    db: Database = None
    port: int = 0
    token: str = None   # set only with --allow-labels

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
            page = (HERE / "index.html").read_text().replace('<meta name="morlet-write" content="">',
                                                             f'<meta name="morlet-write" content="{self.token or ""}">')
            return self.send(200, page.encode(), "text/html; charset=utf-8")
        if not url.path.startswith("/api/"):
            return self.send(404, b"not found", "text/plain")
        try:
            body = api(self.db, url.path, parse_qs(url.query))
            code = 200
        except (LookupError, ValueError) as e:
            body, code = {"error": str(e)}, 400
        except psycopg.Error as e:
            body, code = {"error": (e.diag.message_primary or str(e)).strip()}, 400
        self.send(code, json.dumps(body, default=str).encode(), "application/json")

    def do_POST(self):
        url = urlparse(self.path)
        origin = self.headers.get("Origin")
        allowed = (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}")
        if (not self.token or not self.host_ok() or (origin is not None and origin not in allowed)
                or not hmac.compare_digest(self.headers.get("X-Morlet-Token", ""), self.token)
                or not (self.headers.get("Content-Type") or "").startswith("application/json")):
            return self.send(403, b'{"error": "writes are disabled or the request is not from this viewer"}',
                             "application/json")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 10_000:
                raise ValueError("request too large")
            body, code = write_label(self.db, url.path, json.loads(self.rfile.read(n) or b"{}")), 200
        except (LookupError, ValueError, KeyError, TypeError) as e:
            body, code = {"error": str(e)}, 400
        except psycopg.Error as e:
            body, code = {"error": (e.diag.message_primary or str(e)).strip()}, 400
        self.send(code, json.dumps(body, default=str).encode(), "application/json")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    p.add_argument("--allow-labels", action="store_true",
                   help="allow adding and retracting labels from the page (the only writes)")
    a = p.parse_args()
    if not a.dsn:
        sys.exit("set DATABASE_URL or pass --dsn")
    Handler.db = Database(a.dsn)
    Handler.port = a.port
    Handler.token = secrets.token_urlsafe(32) if a.allow_labels else None
    with Handler.db.connect() as conn:
        conn.execute("SELECT morlet_version()")
    server = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    mode = "labels can be added and retracted" if a.allow_labels else "read-only"
    print(f"MorletDB viewer on http://127.0.0.1:{a.port} ({mode})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
