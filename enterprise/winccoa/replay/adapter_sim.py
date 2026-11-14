# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
"""A stand-in for the WinCC OA adapter (phase B builds the real one): the client side of `morlet-winccoa/1`.

It keeps what the adapter must keep: one session ID per process start, a seq per observation, resending until an
observation is acknowledged, and one current request per slot, dropping (and counting) results whose request_id is
no longer the slot's current one.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import uuid
from dataclasses import dataclass, field

SCHEMA = "morlet-winccoa/1"


@dataclass
class Response:
    status: int
    body: dict


class Client:
    def __init__(self, host: str, port: int, token: str, timeout: float = 20.0):
        self.host, self.port, self.token, self.timeout = host, port, token, timeout

    def request(self, method: str, path: str, body: dict | None = None, *, raw: bytes | None = None,
                headers: dict | None = None) -> Response:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
            h = {"Host": f"{self.host}:{self.port}", "Authorization": f"Bearer {self.token}"}
            if data is not None:
                h["Content-Type"] = "application/json"
            h.update(headers or {})
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            payload = r.read()
            try:
                parsed = json.loads(payload) if payload else {}
            except ValueError:
                parsed = {"raw": payload.decode(errors="replace")}
            return Response(r.status, parsed)
        finally:
            conn.close()


def ns(v: int) -> str:
    return str(int(v))


@dataclass
class Adapter:
    client: Client
    session: str = field(default_factory=lambda: f"sim-{uuid.uuid4()}")
    seq: int = 0
    unacked: list[dict] = field(default_factory=list)
    acked_seq: int = -1
    current: dict[str, str] = field(default_factory=dict)       # slot -> request_id
    results: dict[str, dict] = field(default_factory=dict)      # slot -> last accepted result
    late_dropped: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def queue(self, **obs) -> dict:
        o = {"seq": self.seq, **obs}
        self.seq += 1
        self.unacked.append(o)
        return o

    def change(self, acquisition: str, dpe: str, value_text: str, value_type: str, source_ns: int | None,
               observed_ns: int | None = None, quality: dict | None = None, kind: str = "change", **extra) -> dict:
        o = {"kind": kind, "acquisition": acquisition, "dpe": dpe, "value_text": value_text,
             "value_type": value_type, "observed_ns": ns(observed_ns if observed_ns is not None else time.time_ns())}
        if source_ns is not None:
            o["source_ns"] = ns(source_ns)
        if quality is not None:
            o["quality"] = quality
        o.update(extra)
        return self.queue(**o)

    def heartbeat(self, acquisition: str, asset: str, rtt_ns: int | None = None,
                  observed_ns: int | None = None) -> dict:
        o = {"kind": "heartbeat", "acquisition": acquisition, "asset": asset,
             "observed_ns": ns(observed_ns if observed_ns is not None else time.time_ns())}
        if rtt_ns is not None:
            o["rtt_ns"] = ns(rtt_ns)
        return self.queue(**o)

    def gap(self, acquisition: str, asset: str) -> dict:
        return self.queue(kind="gap", acquisition=acquisition, asset=asset, observed_ns=ns(time.time_ns()))

    def flush(self, max_per_message: int = 1000) -> list[Response]:
        """Send everything unacknowledged; keep what was not acknowledged for the next flush."""
        out = []
        while self.unacked:
            part = self.unacked[:max_per_message]
            r = self.client.request("POST", "/v1/observations",
                                    {"schema": SCHEMA, "session": self.session, "observations": part})
            out.append(r)
            if r.status != 200:
                break
            self.acked_seq = r.body["highest_seq"]
            self.unacked = [o for o in self.unacked if o["seq"] > self.acked_seq]
        return out

    # --- investigations ----------------------------------------------------------------------------------------
    def start_investigation(self, slot: str, collection: str, recording_id: str, start: int, end: int,
                            top_k: int | None = None) -> dict:
        body = {"schema": SCHEMA, "request_id": str(uuid.uuid4()), "slot": slot, "collection": collection,
                "recording_id": recording_id, "start_sample": start, "end_sample": end}
        if top_k is not None:
            body["top_k"] = top_k
        with self._lock:
            self.current[slot] = body["request_id"]
        return body

    def send_investigation(self, body: dict) -> Response:
        return self.client.request("POST", "/v1/investigations", body)

    def accept(self, response: Response) -> bool:
        """Write a result into the slot only if its request_id is still the slot's current one."""
        rid, slot = response.body.get("request_id"), response.body.get("slot")
        with self._lock:
            if slot is None or self.current.get(slot) != rid:
                self.late_dropped += 1
                return False
            self.results[slot] = response.body
            return True
