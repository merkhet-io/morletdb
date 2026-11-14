# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""Investigations (docs/winccoa.md section 4): a bounded similarity search from one stable source interval.

- A request names a `request_id` (UUID), a `slot` (one per UI panel) and the source interval: collection,
  recording_id and a half-open [start_sample, end_sample) of exactly one window length (512..32,768).
- An identical retry of a `request_id` returns the same result; a different body under it is a conflict (409).
- A new request on a slot supersedes the slot's running one: its query is cancelled (`pg_cancel_backend` on its
  connection) and its result says `superseded_by`. At most `investigations_total` run at once (then 429).
- Search is `morlet_search_similar_evidence` through the SDK (`Client.search(evidence=True)`), as the caller, so
  privileges and row security apply. Readiness comes only from `morlet_coverage`.
- Status: `ok`, `no_matches` (the windows searched hold no match; what was not searched is reported),
  `evidence_unavailable` (the source is not searchable yet, or no window of this size is stored),
  `permission_denied` (the role lacks a privilege) or `service_failure` (database unavailable, timeout, cancelled).
- Matches carry recording IDs, half-open sample ranges, distances, UTC times where the recording has a start time,
  overlapping context and labels, and a viewer link when a loopback viewer is configured. No samples.

Labels on a match are annotations of that stored interval. Similarity is not a diagnosis: a match's labels do not
classify the source.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Callable
from urllib.parse import urlencode

import morletdb
import psycopg
from psycopg import sql

from .config import Config, SCHEMA_VERSION
from .observations import ForbiddenName, ProtocolError

log = logging.getLogger("morlet_winccoa.investigate")
STATUSES = ("ok", "no_matches", "evidence_unavailable", "permission_denied", "service_failure")
_FIELDS = {"schema", "request_id", "slot", "collection", "recording_id", "start_sample", "end_sample", "top_k"}


class Busy(Exception):
    """Too many investigations at once (HTTP 429)."""


class RequestConflict(Exception):
    """A request_id reused with a different body (HTTP 409)."""


@dataclass
class Request:
    request_id: str
    slot: str
    digest: str
    body: dict
    done: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    pid: int | None = None
    superseded_by: str | None = None
    started: float = field(default_factory=time.monotonic)


def parse_request(cfg: Config, body: Any) -> dict:
    if not isinstance(body, dict):
        raise ProtocolError("the body must be a JSON object")
    unknown = set(body) - _FIELDS
    if unknown:
        raise ProtocolError(f"unknown field(s) {', '.join(sorted(unknown))}")
    try:
        rid = str(uuid.UUID(str(body.get("request_id"))))
    except ValueError:
        raise ProtocolError("request_id must be a UUID") from None
    if rid != body.get("request_id"):
        raise ProtocolError("request_id must be a UUID in canonical lower-case form")
    slot = body.get("slot")
    if not isinstance(slot, str) or not 1 <= len(slot) <= 100:
        raise ProtocolError("slot must be a non-empty string (at most 100 characters)")
    coll = body.get("collection")
    if coll not in cfg.collections():
        raise ForbiddenName(f"collection {coll!r} is not configured")
    rec = body.get("recording_id")
    if not isinstance(rec, str) or not rec:
        raise ProtocolError("recording_id is required")
    a, b = body.get("start_sample"), body.get("end_sample")
    for name, v in (("start_sample", a), ("end_sample", b)):
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < (1 << 53):
            raise ProtocolError(f"{name} must be an integer 0..2^53-1")
    lim = cfg.limits
    if not lim.window_min <= b - a <= lim.window_max:
        raise ProtocolError(f"the source interval [start_sample, end_sample) must be one window of "
                            f"{lim.window_min}..{lim.window_max} samples (default {lim.window_default})")
    top_k = body.get("top_k", lim.top_k_default)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= lim.top_k_max:
        raise ProtocolError(f"top_k must be 1..{lim.top_k_max}")
    return {"request_id": rid, "slot": slot, "collection": coll, "recording_id": rec, "start_sample": a,
            "end_sample": b, "top_k": top_k}


def _rel(collection: str, suffix: str) -> str:
    s, t = collection.split(".", 1)
    return f"{s}.{t}{suffix}"


def _overlapping(conn, collection: str, recording_id: str, a: int, b: int) -> tuple[list, list]:
    """Active context rows and labels overlapping [a, b) of a recording, if context is enabled."""
    ctx, labels = [], []
    ctx_rel = conn.execute("SELECT to_regclass(%s)", (_rel(collection, "_ctx"),)).fetchone()[0]
    if ctx_rel is None:
        return ctx, labels
    s, t = collection.split(".", 1)
    rows = conn.execute(sql.SQL(
        "SELECT x.key, x.value, x.start_sample, x.end_sample FROM {c} x WHERE x.recording_id = %s AND NOT x.retracted "
        "AND NOT EXISTS (SELECT 1 FROM {c} y WHERE y.supersedes = x.ctx_id) AND x.start_sample < %s "
        "AND coalesce(x.end_sample, x.start_sample + 1) > %s ORDER BY x.key, x.start_sample").format(
        c=sql.Identifier(s, t + "_ctx")), (recording_id, b, a)).fetchall()
    ctx = [{"key": k, "value": v, "start_sample": int(x), "end_sample": None if y is None else int(y)}
           for k, v, x, y in rows]
    rows = conn.execute(sql.SQL(
        "SELECT label, start_sample, end_sample, note FROM {} WHERE recording_id = %s AND NOT retracted "
        "AND start_sample < %s AND end_sample > %s ORDER BY start_sample, label").format(
        sql.Identifier(s, t + "_labels")), (recording_id, b, a)).fetchall()
    labels = [{"label": lb, "start_sample": int(x), "end_sample": int(y), "note": n} for lb, x, y, n in rows]
    return ctx, labels


def _coverage(conn, collection: str, recording_id: str, a: int, b: int, window: int) -> dict:
    rows = conn.execute("SELECT recording_id, start_sample, end_sample, state, windows "
                        "FROM morlet_coverage(%s::regclass, NULL, %s)", (collection, window)).fetchall()
    source_states = sorted({st for r, x, y, st, _ in rows if r == recording_id and x < b and y > a})
    covered = sum(min(y, b) - max(x, a) for r, x, y, st, _ in rows
                  if r == recording_id and st == "searchable" and x < b and y > a)
    not_searched: dict[str, int] = {}
    for _, x, y, st, _ in rows:
        if st != "searchable":
            not_searched[st] = not_searched.get(st, 0) + int(y - x)
    return {"window": window,
            "source_searchable": covered == b - a,
            "source_states": source_states,
            "searchable_windows": int(sum(w or 0 for *_, st, w in rows if st == "searchable")),
            "searchable_recordings": len({r for r, *_, st, _ in rows if st == "searchable"}),
            "not_searched_samples": not_searched}


def _viewer(cfg: Config, collection: str, recording_id: str, a: int, b: int) -> str | None:
    if not cfg.viewer_base_url:
        return None
    q = urlencode({"collection": collection, "recording": recording_id, "start": a, "end": b})
    return cfg.viewer_base_url.rstrip("/") + "/?" + q


def _fit(cfg: Config, result: dict) -> dict:
    """Keep the response within the size limit: drop context, then labels, then overviews, then trailing matches,
    and say what was dropped in `truncated`."""
    def size(r):
        return len(json.dumps(r, default=str).encode())
    dropped = []
    for part in ("context", "labels", "overview"):
        if size(result) <= cfg.limits.response_bytes:
            break
        for m in result.get("matches", []):
            m.pop(part, None)
        dropped.append(part)
    while size(result) > cfg.limits.response_bytes and result.get("matches"):
        result["matches"].pop()
        if "matches" not in dropped:
            dropped.append("matches")
    if dropped:
        result["truncated"] = dropped
    return result


def run_search(conn: psycopg.Connection, cfg: Config, req: dict) -> dict:
    """The search of one request on an open connection (rolled back afterwards; nothing is written)."""
    db = morletdb.Client(conn)
    coll, rec_id, a, b = req["collection"], req["recording_id"], req["start_sample"], req["end_sample"]
    base = {"source": {"collection": coll, "recording_id": rec_id, "start_sample": a, "end_sample": b}}
    try:
        rec = db.recording(coll, rec_id)
    except morletdb.NotFoundError:
        return {**base, "status": "evidence_unavailable",
                "detail": "the source recording does not exist or is not visible to this role"}
    if b > rec.samples:
        return {**base, "status": "evidence_unavailable",
                "detail": f"the source interval ends at {b}; {rec.samples} samples are stored"}
    readiness = _coverage(conn, coll, rec_id, a, b, b - a)
    base["readiness"] = readiness
    if readiness["searchable_windows"] == 0:
        return {**base, "status": "evidence_unavailable", "detail": f"no search window of {b - a} samples is stored"}
    if not readiness["source_searchable"]:
        return {**base, "status": "evidence_unavailable",
                "detail": "the source interval is not inside built search windows yet"}
    result = db.search(coll, rec_id, a, b, top_k=req["top_k"], max_candidates=cfg.limits.max_candidates,
                       evidence=True, evidence_parts=cfg.limits.overview_buckets)
    base["search_mode"] = result.mode
    src_ctx, src_labels = _overlapping(conn, coll, rec_id, a, b)
    base["source"].update({"context": src_ctx, "start_utc": _utc(rec, a)})
    matches = []
    for m in result.matches:
        ctx, labels = _overlapping(conn, coll, m.ref.recording_id, m.ref.start_sample, m.ref.end_sample)
        ev = m.evidence
        matches.append({
            "rank": m.rank, "recording_id": m.ref.recording_id, "start_sample": m.ref.start_sample,
            "end_sample": m.ref.end_sample, "distance": m.distance,
            "start_utc": None if ev is None or ev.start_utc is None else ev.start_utc.isoformat(),
            "end_utc": None if ev is None or ev.end_utc is None else ev.end_utc.isoformat(),
            "overview": [] if ev is None else [[p.start_sample, p.end_sample, p.min, p.max, p.rms] for p in ev.overview],
            "context": ctx, "labels": labels,
            "viewer_url": _viewer(cfg, coll, m.ref.recording_id, m.ref.start_sample, m.ref.end_sample)})
    return {**base, "status": "ok" if matches else "no_matches", "matches": matches,
            "note": "distances are envelope distances (0 identical .. 2); labels annotate matched intervals and "
                    "are not a diagnosis of the source"}


def _utc(rec, sample: int) -> str | None:
    """UTC time of a sample (ns, floor of the exact rational time), ISO 8601."""
    if rec.start_ns is None:
        return None
    rate = Fraction(rec.sample_rate_hz)
    t = rec.start_ns + int(Fraction(sample) * 1_000_000_000 / rate)
    sec, frac = divmod(t, 1_000_000_000)
    base = datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{frac:09d}Z"


class Investigations:
    def __init__(self, cfg: Config, connect: Callable[[], psycopg.Connection]):
        self.cfg = cfg
        self.connect = connect
        self.lock = threading.Lock()
        self.requests: OrderedDict[str, Request] = OrderedDict()
        self.current: dict[str, str] = {}
        self.running: set[str] = set()
        self.superseded = 0

    def _digest(self, req: dict) -> str:
        return hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()

    def submit(self, req: dict) -> Request:
        digest = self._digest(req)
        with self.lock:
            old = self.requests.get(req["request_id"])
            if old is not None:
                if old.digest != digest:
                    raise RequestConflict(f"request_id {req['request_id']} was used with a different body")
                return old
            prev_id = self.current.get(req["slot"])
            prev = self.requests.get(prev_id) if prev_id else None
            replacing = prev is not None and prev_id in self.running
            if len(self.running) - (1 if replacing else 0) >= self.cfg.limits.investigations_total:
                raise Busy(f"{len(self.running)} investigations are running (limit "
                           f"{self.cfg.limits.investigations_total})")
            r = Request(req["request_id"], req["slot"], digest, req)
            self.requests[r.request_id] = r
            self.current[r.slot] = r.request_id
            self.running.add(r.request_id)
            if prev is not None:
                prev.superseded_by = r.request_id
                self.superseded += 1
            self._evict()
        threading.Thread(target=self._run, args=(r, prev if replacing else None), daemon=True,
                         name=f"investigation-{r.request_id[:8]}").start()
        return r

    def _evict(self) -> None:
        finished = [k for k, v in self.requests.items() if v.done.is_set()]
        while len(finished) > self.cfg.limits.finished_requests_kept:
            k = finished.pop(0)
            del self.requests[k]

    def _finish(self, r: Request, result: dict) -> None:
        r.result = {"schema": SCHEMA_VERSION, "request_id": r.request_id, "slot": r.slot, **result}
        r.result = _fit(self.cfg, r.result)
        with self.lock:
            self.running.discard(r.request_id)
            r.done.set()

    def _run(self, r: Request, prev: Request | None) -> None:
        conn = None
        try:
            conn = self.connect()
            r.pid = conn.info.backend_pid
            conn.execute(f"SET statement_timeout = {int(self.cfg.limits.statement_timeout_ms)}")
            conn.commit()
            if prev is not None:
                # Cancel the superseded query; repeated briefly, since it may be between statements.
                deadline = time.monotonic() + 1.0
                while not prev.done.is_set() and time.monotonic() < deadline:
                    if prev.pid is not None:
                        conn.execute("SELECT pg_cancel_backend(%s)", (prev.pid,))
                        conn.commit()
                    prev.done.wait(0.05)
            if r.superseded_by is not None:
                return self._finish(r, {"status": "service_failure", "detail": "superseded before it started"})
            result = run_search(conn, self.cfg, r.body)
            conn.rollback()
            self._finish(r, result)
        except psycopg.errors.QueryCanceled:
            detail = "superseded and cancelled" if r.superseded_by else "statement timeout exceeded"
            self._finish(r, {"status": "service_failure", "detail": detail})
        except psycopg.errors.InsufficientPrivilege as exc:
            self._finish(r, {"status": "permission_denied", "detail": exc.diag.message_primary or str(exc)})
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            log.error("investigation %s: database unavailable: %s", r.request_id, exc)
            self._finish(r, {"status": "service_failure", "detail": "database unavailable"})
        except Exception as exc:     # never leave a request without a result
            log.exception("investigation %s failed", r.request_id)
            self._finish(r, {"status": "service_failure", "detail": f"{type(exc).__name__}: {exc}"})
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def view(self, r: Request) -> dict:
        """The stored result with its correlation state, or the running state."""
        with self.lock:
            current = self.current.get(r.slot) == r.request_id
        if not r.done.is_set():
            return {"schema": SCHEMA_VERSION, "request_id": r.request_id, "slot": r.slot, "state": "running",
                    "current": current, "superseded_by": r.superseded_by}
        return {**r.result, "state": "done", "current": current, "superseded_by": r.superseded_by}

    def get(self, request_id: str) -> Request | None:
        with self.lock:
            return self.requests.get(request_id)

    def stats(self) -> dict:
        with self.lock:
            return {"running": len(self.running), "kept": len(self.requests), "superseded": self.superseded}
