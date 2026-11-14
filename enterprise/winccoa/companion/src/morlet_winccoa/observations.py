# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
"""Observations from the adapter: validation of `POST /v1/observations` bodies and their storage.

A message is stored in one transaction. The response (and so the acknowledgement) is sent only after COMMIT.
`(session, seq)` is the idempotency key: an identical retry is a no-op, a different body under the same pair is a
conflict and nothing of the message is stored.

Times are integer nanoseconds since the Unix epoch, sent as decimal strings: a JavaScript number is exact only up to
2^53, and epoch nanoseconds are about 1.8e18.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .config import Config

KINDS = ("change", "snapshot", "heartbeat", "gap")
VALUE_TYPES = ("bool", "int", "float", "string", "json")
_INT = re.compile(r"^-?(0|[1-9][0-9]*)$")
_FLOAT_SPECIAL = ("NaN", "Infinity", "-Infinity")
INT64 = 1 << 63
_FIELDS = {"seq", "kind", "acquisition", "asset", "dpe", "value_text", "value_type", "source_ns", "source_status",
           "quality", "quality_status", "observed_ns", "rtt_ns"}


class ProtocolError(ValueError):
    """A malformed request (HTTP 400)."""


class ForbiddenName(ProtocolError):
    """A collection, asset or datapoint that is not configured (HTTP 403)."""


class Conflict(Exception):
    """A different body under an already stored (session, seq) (HTTP 409)."""

    def __init__(self, seqs: list[int]):
        super().__init__(f"conflicting retry for seq {', '.join(map(str, seqs))}")
        self.seqs = seqs


class UnknownAcquisition(Exception):
    """The acquisition does not exist in the asset's collection, is not visible, or belongs to another machine
    (HTTP 422). Nothing of the message is stored."""


def ns(value: Any, name: str, *, optional: bool = False) -> int | None:
    if value is None:
        if optional:
            return None
        raise ProtocolError(f"{name} is required")
    if not isinstance(value, str) or not _INT.match(value):
        raise ProtocolError(f"{name} must be integer nanoseconds as a decimal string")
    v = int(value)
    if not -INT64 <= v < INT64:
        raise ProtocolError(f"{name} is out of range (64-bit signed nanoseconds)")
    return v


def parse_value(value_text: Any, value_type: Any) -> tuple[Any, bool]:
    """(the JSON value, representable) of an adapter value. The text is kept as sent; NaN and infinities have no
    JSON form and are stored as text only."""
    if not isinstance(value_text, str) or value_type not in VALUE_TYPES:
        raise ProtocolError(f"value_text must be a string and value_type one of {', '.join(VALUE_TYPES)}")
    if value_type == "float" and value_text in _FLOAT_SPECIAL:
        return None, False
    try:
        v = json.loads(value_text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except ValueError:
        raise ProtocolError(f"value_text is not valid JSON for type {value_type}") from None
    ok = {"bool": isinstance(v, bool), "int": isinstance(v, int) and not isinstance(v, bool) and _INT.match(value_text.strip()),
          "float": isinstance(v, (int, float)) and not isinstance(v, bool), "string": isinstance(v, str),
          "json": True}[value_type]
    if not ok:
        raise ProtocolError(f"value_text {value_text!r} is not a {value_type}")
    return v, True


@dataclass
class Observation:
    seq: int
    kind: str
    collection: str
    asset: str
    acquisition_id: str
    dpe: str | None
    key: str | None
    value_text: str | None
    value_type: str | None
    value_json: Any
    source_ns: int | None
    source_status: str
    quality: dict | None
    quality_status: str
    observed_ns: int
    rtt_ns: int | None
    digest: str


def _quality(raw: dict) -> tuple[dict | None, str]:
    q = raw.get("quality")
    status = raw.get("quality_status")
    if status is not None and status not in ("unreadable",):
        raise ProtocolError("quality_status may only be sent as 'unreadable'; other states follow from quality")
    if q is None:
        return None, status or "missing"
    if not isinstance(q, dict):
        raise ProtocolError("quality must be an object of the raw attributes, or absent")
    if status:
        raise ProtocolError("quality_status 'unreadable' comes without quality attributes")
    good = q.get("good")
    if good is not None and not isinstance(good, bool):
        raise ProtocolError("quality.good must be true or false")
    return q, ("good" if good is True else "bad" if good is False else "uncertain")


def parse_observation(cfg: Config, raw: Any) -> Observation:
    if not isinstance(raw, dict):
        raise ProtocolError("every observation must be an object")
    unknown = set(raw) - _FIELDS
    if unknown:
        raise ProtocolError(f"unknown observation field(s) {', '.join(sorted(unknown))}")
    seq = raw.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq < (1 << 53):
        raise ProtocolError("seq must be an integer 0..2^53-1")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ProtocolError(f"kind must be one of {', '.join(KINDS)}")
    acq = raw.get("acquisition")
    if not isinstance(acq, str) or not acq:
        raise ProtocolError("acquisition is required")
    dpe, key, asset = raw.get("dpe"), None, raw.get("asset")
    if dpe is not None:
        dp = cfg.datapoint(dpe) if isinstance(dpe, str) else None
        if dp is None:
            raise ForbiddenName(f"datapoint {dpe!r} is not configured")
        if asset is not None and asset != dp.asset:
            raise ProtocolError(f"datapoint {dpe!r} belongs to asset {dp.asset!r}, not {asset!r}")
        asset, key = dp.asset, dp.key
    elif kind in ("change", "snapshot"):
        raise ProtocolError(f"a {kind} needs dpe")
    elif kind == "heartbeat" or kind == "gap":
        if not isinstance(asset, str):
            raise ProtocolError(f"a {kind} without dpe needs asset")
    if kind == "heartbeat" and dpe is not None:
        raise ProtocolError("a heartbeat names an asset, not a datapoint")
    a = cfg.asset(asset)
    if a is None:
        raise ForbiddenName(f"asset {asset!r} is not configured")
    value_json, value_text, value_type = None, None, None
    if kind in ("change", "snapshot"):
        value_text, value_type = raw.get("value_text"), raw.get("value_type")
        value_json, _ = parse_value(value_text, value_type)
    elif "value_text" in raw or "value_type" in raw:
        raise ProtocolError(f"a {kind} carries no value")
    source = ns(raw.get("source_ns"), "source_ns", optional=True)
    status = raw.get("source_status")
    if source is not None:
        if status not in (None, "ok"):
            raise ProtocolError("source_status must be 'ok' (or absent) when source_ns is sent")
        status = "ok"
    else:
        if status not in (None, "missing", "unreadable"):
            raise ProtocolError("source_status without source_ns must be 'missing' or 'unreadable'")
        status = status or "missing"
    quality, quality_status = _quality(raw) if kind in ("change", "snapshot") else (None, "missing")
    if kind not in ("change", "snapshot") and ("quality" in raw or "quality_status" in raw):
        raise ProtocolError(f"a {kind} carries no quality")
    rtt = ns(raw.get("rtt_ns"), "rtt_ns", optional=True)
    if rtt is not None and (kind != "heartbeat" or rtt < 0):
        raise ProtocolError("rtt_ns is a nonnegative heartbeat field")
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return Observation(seq, kind, a.collection, a.name, acq, dpe, key, value_text, value_type, value_json, source,
                       status, quality, quality_status, ns(raw.get("observed_ns"), "observed_ns"), rtt,
                       hashlib.sha256(canonical.encode()).hexdigest())


def parse_message(cfg: Config, body: Any) -> tuple[str, list[Observation]]:
    if not isinstance(body, dict):
        raise ProtocolError("the body must be a JSON object")
    unknown = set(body) - {"schema", "session", "observations"}
    if unknown:
        raise ProtocolError(f"unknown field(s) {', '.join(sorted(unknown))}")
    session = body.get("session")
    if not isinstance(session, str) or not 1 <= len(session) <= 200:
        raise ProtocolError("session must be a non-empty string (at most 200 characters)")
    obs = body.get("observations")
    if not isinstance(obs, list) or not obs:
        raise ProtocolError("observations must be a non-empty array")
    if len(obs) > cfg.limits.observations_per_message:
        raise ProtocolError(f"at most {cfg.limits.observations_per_message} observations per message")
    parsed = [parse_observation(cfg, o) for o in obs]
    seqs = [o.seq for o in parsed]
    if len(set(seqs)) != len(seqs):
        raise ProtocolError("a seq appears twice in one message")
    return session, parsed


@dataclass
class StoreResult:
    stored: int
    duplicates: int
    highest_seq: int


def store(conn: psycopg.Connection, cfg: Config, session: str, observations: list[Observation]) -> StoreResult:
    """Store a message in one transaction and commit it. Raises Conflict or UnknownAcquisition (rolled back)."""
    table = sql.Identifier(cfg.schema, "observation")
    received = time.time_ns()
    stored = dup = 0
    conflicts = []
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('morlet-winccoa-session/' || %s, 0))",
                         (session,))
            checked = set()
            for o in observations:
                if (o.collection, o.acquisition_id) not in checked:
                    s, t = o.collection.split(".", 1)
                    row = conn.execute(sql.SQL("SELECT machine FROM {} WHERE acquisition_id = %s").format(
                        sql.Identifier(s, t + "_acq")), (o.acquisition_id,)).fetchone()
                    if row is None or row[0] != cfg.asset(o.asset).machine:
                        raise UnknownAcquisition(f"acquisition {o.acquisition_id!r} does not exist in "
                                                 f"{o.collection}, is not visible, or is not machine "
                                                 f"{cfg.asset(o.asset).machine!r}")
                    checked.add((o.collection, o.acquisition_id))
                prev = conn.execute(sql.SQL("SELECT digest FROM {} WHERE adapter_session = %s AND seq = %s").format(
                    table), (session, o.seq)).fetchone()
                if prev is not None:
                    if prev[0] == o.digest:
                        dup += 1
                    else:
                        conflicts.append(o.seq)
                    continue
                conn.execute(sql.SQL(
                    "INSERT INTO {} (collection, acquisition_id, asset, dpe, key, value_json, value_text, value_type, "
                    "source_ns, source_status, quality, quality_status, observed_ns, received_ns, rtt_ns, kind, "
                    "adapter_session, seq, digest) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                ).format(table), (o.collection, o.acquisition_id, o.asset, o.dpe, o.key,
                                  None if o.value_json is None else Jsonb(o.value_json), o.value_text, o.value_type,
                                  o.source_ns, o.source_status, None if o.quality is None else Jsonb(o.quality),
                                  o.quality_status, o.observed_ns, received, o.rtt_ns, o.kind, session, o.seq,
                                  o.digest))
                stored += 1
            if conflicts:
                raise Conflict(conflicts)
            highest = conn.execute(sql.SQL("SELECT max(seq) FROM {} WHERE adapter_session = %s").format(table),
                                   (session,)).fetchone()[0]
    except psycopg.errors.UniqueViolation:
        # The pair exists but is not visible to this role (another acquisition's row): never overwritten.
        raise Conflict([o.seq for o in observations]) from None
    return StoreResult(stored, dup, int(highest))


def mark_finished(conn: psycopg.Connection, cfg: Config, collection: str, acquisition_id: str, end_ns: int) -> bool:
    """The writer marks an acquisition finished at acquisition time `end_ns` (ns from its origin); open states are then
    closed there, and intervals are clipped to it. Idempotent; a different end is an error. Runs in the caller's
    transaction."""
    table = sql.Identifier(cfg.schema, "acquisition_end")
    row = conn.execute(sql.SQL("SELECT end_ns FROM {} WHERE collection = %s AND acquisition_id = %s").format(table),
                       (collection, acquisition_id)).fetchone()
    if row is not None:
        if row[0] != end_ns:
            raise ValueError(f"acquisition {acquisition_id} is already finished at {row[0]} ns, not {end_ns}")
        return False
    conn.execute(sql.SQL("INSERT INTO {} (collection, acquisition_id, end_ns) VALUES (%s, %s, %s)").format(table),
                 (collection, acquisition_id, int(end_ns)))
    return True
