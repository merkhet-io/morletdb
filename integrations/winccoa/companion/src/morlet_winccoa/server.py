# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The loopback HTTP/JSON service, protocol `morlet-winccoa/1` (docs/winccoa.md section 4).

    POST /v1/observations             store a message; 200 only after COMMIT, with the session's highest seq
    POST /v1/investigations           start (or retry) an investigation; waits up to the HTTP deadline
    GET  /v1/investigations/{id}      its result, or "running", with its correlation state
    GET  /v1/health                   database, clock, acquisitions, open states and derivation

Every request needs `Authorization: Bearer <token>` and a Host header naming the loopback address and port (a
defence against DNS rebinding). Every body carries "schema": "morlet-winccoa/1"; another schema gets 400 with the
supported versions.

HTTP codes: 400 malformed, 401 unauthorized, 403 not configured or not permitted, 404 unknown path or request,
409 conflicting retry, 413 body too large, 415 not JSON, 421 wrong Host, 422 unknown acquisition,
429 busy or authentication back-off, 503 database unavailable.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import psycopg
from psycopg import sql

from . import derive as derive_mod
from . import observations as obs_mod
from .auth import Authenticator
from .config import SCHEMA_VERSION, SUPPORTED_SCHEMAS, Config
from .investigate import Busy, Investigations, RequestConflict, parse_request

log = logging.getLogger("morlet_winccoa.server")
_INVESTIGATION = re.compile(r"^/v1/investigations/([0-9a-f-]{36})$")


class Companion:
    """The service state shared by request handlers: configuration, authentication, investigations and the
    background deriver."""

    def __init__(self, cfg: Config, connect: Callable[[], psycopg.Connection] | None = None):
        self.cfg = cfg
        self.connect = connect or self._connect
        self.auth = Authenticator(cfg.token_file, cfg.limits.auth_failures_per_minute, cfg.limits.auth_backoff_s)
        self.investigations = Investigations(cfg, self.connect)
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._deriver: threading.Thread | None = None
        self.last_derivation: dict[str, Any] = {}

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.cfg.dsn, autocommit=True, connect_timeout=self.cfg.limits.connect_timeout_s,
                               application_name="morlet-winccoa-companion")

    # --- background derivation ---------------------------------------------------------------------------------
    def start_deriver(self) -> None:
        self._deriver = threading.Thread(target=self._derive_loop, daemon=True, name="deriver")
        self._deriver.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._deriver is not None:
            self._deriver.join(timeout=10)

    def request_derivation(self, acquisitions: set[tuple[str, str]]) -> None:
        self._queue.put(acquisitions)

    def _derive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=self.cfg.derive_interval_s)
            except queue.Empty:
                item = "all"
            if item is None:
                break
            try:
                with self.connect() as conn:
                    targets = None if item == "all" else sorted(item)
                    summaries = derive_mod.derive_all(conn, self.cfg, targets)
                    self.last_derivation = {"at": time.time(), "acquisitions": len(summaries),
                                            "batches_added": sum(s.added_batches for s in summaries),
                                            "mismatches": sum(s.mismatches for s in summaries), "error": None}
            except Exception as exc:     # the database may be down; the next pass retries
                self.last_derivation = {"at": time.time(), "error": f"{type(exc).__name__}: {exc}"}
                log.warning("derivation pass failed: %s", exc)

    # --- health ------------------------------------------------------------------------------------------------
    def health(self) -> tuple[int, dict]:
        out: dict[str, Any] = {"schema": SCHEMA_VERSION, "status": "ok", "database": "ok", "assets": [],
                               "investigations": self.investigations.stats(), "derivation": self.last_derivation,
                               "limits": {"observations_per_message": self.cfg.limits.observations_per_message,
                                          "body_bytes": self.cfg.limits.body_bytes,
                                          "top_k_max": self.cfg.limits.top_k_max,
                                          "window_min": self.cfg.limits.window_min,
                                          "window_max": self.cfg.limits.window_max,
                                          "heartbeat_interval_s": self.cfg.clock.heartbeat_interval_s}}
        try:
            with self.connect() as conn:
                now = time.time_ns()
                for a in self.cfg.assets:
                    s, t = a.collection.split(".", 1)
                    acq = conn.execute(sql.SQL(
                        "SELECT a.acquisition_id FROM {acq} a LEFT JOIN {end} e ON e.collection = %s AND "
                        "e.acquisition_id = a.acquisition_id WHERE a.machine = %s AND e.acquisition_id IS NULL "
                        "ORDER BY a.origin_utc DESC NULLS LAST, a.acquisition_id DESC LIMIT 1").format(
                        acq=sql.Identifier(s, t + "_acq"), end=sql.Identifier(self.cfg.schema, "acquisition_end")),
                        (a.collection, a.machine)).fetchone()
                    entry: dict[str, Any] = {"asset": a.name, "collection": a.collection,
                                             "current_acquisition": acq[0] if acq else None}
                    if acq:
                        hb = conn.execute(sql.SQL(
                            "SELECT observed_ns, received_ns, rtt_ns FROM {} WHERE collection = %s AND "
                            "acquisition_id = %s AND kind = 'heartbeat' ORDER BY obs_id DESC LIMIT 1").format(
                            sql.Identifier(self.cfg.schema, "observation")), (a.collection, acq[0])).fetchone()
                        if hb is None:
                            entry["clock"] = {"state": "unknown", "detail": "no heartbeat yet"}
                            out["status"] = "degraded"
                        else:
                            est = abs(hb[1] - hb[0]) + (hb[2] or 0) // 2 + int(self.cfg.clock.daq_bound_ms * 1e6)
                            ok = est <= int(self.cfg.clock.bound_ms * 1e6)
                            age = (now - hb[1]) / 1e9
                            stale = age > 3 * self.cfg.clock.heartbeat_interval_s
                            entry["clock"] = {"state": "ok" if ok else "exceeded", "estimate_ns": str(est),
                                              "bound_ns": str(int(self.cfg.clock.bound_ms * 1e6)),
                                              "last_heartbeat_age_s": round(age, 3), "adapter_stale": stale}
                            if not ok or stale:
                                out["status"] = "degraded"
                        entry["open_states"] = derive_mod.open_states(conn, self.cfg, a.collection, acq[0])
                    out["assets"].append(entry)
                reasons = conn.execute(sql.SQL(
                    "SELECT coalesce(reason, 'context'), count(*) FROM {} GROUP BY 1 ORDER BY 1").format(
                    sql.Identifier(self.cfg.schema, "derivation"))).fetchall()
                out["derivations"] = {r: n for r, n in reasons}
        except psycopg.errors.InsufficientPrivilege as exc:
            out.update(status="service_failure", database="permission_denied",
                       detail=exc.diag.message_primary or str(exc))
            return 503, out
        except psycopg.Error as exc:
            out.update(status="service_failure", database="unavailable", detail=type(exc).__name__)
            return 503, out
        if self.last_derivation.get("error") or self.last_derivation.get("mismatches"):
            out["status"] = "degraded"
        return 200, out

    # --- observations ------------------------------------------------------------------------------------------
    def observations(self, body: Any) -> tuple[int, dict]:
        session, parsed = obs_mod.parse_message(self.cfg, body)
        try:
            with self.connect() as conn:
                res = obs_mod.store(conn, self.cfg, session, parsed)
        except psycopg.errors.InsufficientPrivilege as exc:
            return 403, {"error": "permission_denied", "detail": exc.diag.message_primary or str(exc)}
        except psycopg.OperationalError:
            return 503, {"error": "service_unavailable", "detail": "database unavailable; nothing was stored"}
        # Committed: acknowledge, then derive in the background.
        self.request_derivation({(o.collection, o.acquisition_id) for o in parsed})
        return 200, {"schema": SCHEMA_VERSION, "session": session, "highest_seq": res.highest_seq,
                     "stored": res.stored, "duplicates": res.duplicates}

    # --- investigations ----------------------------------------------------------------------------------------
    def investigate(self, body: Any) -> tuple[int, dict]:
        req = parse_request(self.cfg, body)
        r = self.investigations.submit(req)
        if not r.done.wait(self.cfg.limits.http_deadline_ms / 1000):
            return 202, self.investigations.view(r)
        view = self.investigations.view(r)
        return (503 if view.get("status") == "service_failure" and view.get("detail") == "database unavailable"
                else 200), view


class Handler(BaseHTTPRequestHandler):
    companion: Companion = None
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    def _send(self, code: int, body: dict, headers: dict | None = None) -> None:
        data = json.dumps(body, default=str, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _gate(self) -> bool:
        c = self.companion
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._send(421, {"error": "misdirected_request"})
            return False
        verdict = c.auth.check(self.headers.get("Authorization"), self.client_address[0])
        if verdict == "backoff":
            self._send(429, {"error": "authentication_backoff"}, {"Retry-After": str(c.cfg.limits.auth_backoff_s)})
            return False
        if verdict != "ok":
            self._send(401, {"error": "unauthorized"}, {"WWW-Authenticate": "Bearer"})
            return False
        return True

    def _body(self) -> Any:
        lim = self.companion.cfg.limits.body_bytes
        if not (self.headers.get("Content-Type") or "").split(";")[0].strip() == "application/json":
            raise _HTTPError(415, {"error": "unsupported_media_type", "detail": "send application/json"})
        try:
            n = int(self.headers.get("Content-Length") or "")
        except ValueError:
            raise _HTTPError(411, {"error": "length_required"}) from None
        if n < 0 or n > lim:
            raise _HTTPError(413, {"error": "body_too_large", "limit": lim})
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw, parse_constant=_no_constants)
        except (ValueError, UnicodeDecodeError) as exc:
            raise _HTTPError(400, {"error": "malformed", "detail": f"not JSON: {exc}"}) from None
        if not isinstance(body, dict) or body.get("schema") not in SUPPORTED_SCHEMAS:
            raise _HTTPError(400, {"error": "unsupported_schema", "supported": list(SUPPORTED_SCHEMAS)})
        body = dict(body)
        body.pop("schema")
        return body

    def do_POST(self):
        if not self._gate():
            return self._drain()
        try:
            if self.path == "/v1/observations":
                code, out = self.companion.observations(self._body())
            elif self.path == "/v1/investigations":
                code, out = self.companion.investigate(self._body())
            else:
                code, out = 404, {"error": "not_found"}
        except _HTTPError as exc:
            code, out = exc.code, exc.body
        except obs_mod.ForbiddenName as exc:
            code, out = 403, {"error": "not_configured", "detail": str(exc)}
        except obs_mod.ProtocolError as exc:
            code, out = 400, {"error": "malformed", "detail": str(exc)}
        except obs_mod.Conflict as exc:
            code, out = 409, {"error": "conflict", "detail": str(exc), "seqs": exc.seqs}
        except RequestConflict as exc:
            code, out = 409, {"error": "conflict", "detail": str(exc)}
        except obs_mod.UnknownAcquisition as exc:
            code, out = 422, {"error": "unknown_acquisition", "detail": str(exc)}
        except Busy as exc:
            code, out = 429, {"error": "busy", "detail": str(exc)}
        except Exception as exc:
            log.exception("request failed")
            code, out = 500, {"error": "internal", "detail": type(exc).__name__}
        log.info("%s POST %s -> %s", self.client_address[0], self.path, code)
        self._send(code, out)

    def do_GET(self):
        if not self._gate():
            return
        m = _INVESTIGATION.match(self.path)
        if self.path == "/v1/health":
            code, out = self.companion.health()
        elif m:
            r = self.companion.investigations.get(m.group(1))
            code, out = (404, {"error": "unknown_request"}) if r is None else (200, self.companion.investigations.view(r))
        else:
            code, out = 404, {"error": "not_found"}
        self._send(code, out)

    def _drain(self):
        # Refused before reading: close the connection rather than parse an unauthenticated body.
        self.close_connection = True

    def do_PUT(self):
        self._send(405, {"error": "method_not_allowed"})
        self.close_connection = True

    do_DELETE = do_PATCH = do_PUT


class _HTTPError(Exception):
    def __init__(self, code: int, body: dict):
        super().__init__(body.get("error"))
        self.code, self.body = code, body


def _no_constants(name):
    raise ValueError(f"{name} is not JSON")


def make_server(companion: Companion) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"companion": companion})
    srv = ThreadingHTTPServer((companion.cfg.listen_host, companion.cfg.listen_port), handler)
    srv.daemon_threads = True
    return srv
