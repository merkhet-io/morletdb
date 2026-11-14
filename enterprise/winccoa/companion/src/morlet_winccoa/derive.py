# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
"""From observations to context rows (docs/winccoa.md sections 2 and 3).

A state holds from its change until the next change, as a half-open interval in acquisition time
(t = source_ns - origin_ns). The server maps it to every channel's samples (`morlet_add_acquisition_context`, which
uses `morlet_time_to_samples`, ceil rule in exact numeric); the companion never rounds.

- Only closed intervals become context: closed by the next change of the key, a `gap`, a value of bad or unknown
  quality, or the end of the acquisition (`acquisition_end`, written by the waveform writer). An open state is
  recorded as `open` and reported, never written as context.
- Batch IDs are `winccoa:<acquisition>:<key>:<sha256 of the closed interval and value>`: deriving again, after a
  restart or from another process, adds nothing (`morlet_add_context` returns 0 for an identical batch).
- A derivation is never overwritten. If a later pass computes something else for an observation already derived
  (for example after a channel with an inconsistent start was added), the stored outcome stays and the pass counts a
  mismatch; corrections go through `morlet_revise_context`.
- Gaps: on a `gap`, open states end at the last committed contact of the acquisition before it (a heartbeat or any
  other observation); the span up to the next snapshot has no context, so it reads as missing evidence.
- Clock: a heartbeat gives |received_ns - observed_ns| + rtt_ns/2 (+ the declared companion-DAQ bound). When that
  exceeds the configured bound, or no heartbeat has been seen yet, intervals opened or closed then are not derived
  (`clock_exceeded`). No offset correction is applied.
- Start time: every channel needs start_ns = origin_ns + offset_ns, or nothing is derived (`inconsistent_start`).
- Late channels: when the acquisition's channels change, existing batches are added again; the server adds them only
  to the new channel (per-channel batches `<batch>@<acquisition>/<role>`).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql

from .config import Config

log = logging.getLogger("morlet_winccoa.derive")


@dataclass
class Outcome:
    obs_id: int
    key: str
    time_basis: str
    batch_id: str | None = None
    reason: str | None = None
    t_start_ns: int | None = None
    t_end_ns: int | None = None
    closed_by: int | None = None
    detail: str = ""


@dataclass
class Summary:
    collection: str
    acquisition_id: str
    added_batches: int = 0
    rows_added: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    mismatches: int = 0
    inconsistent_start: str | None = None
    clock_ok: bool | None = None
    clock_estimate_ns: int | None = None


@dataclass
class _State:
    obs_id: int
    t_abs: int
    value_type: str
    value_text: str
    clock_ok: bool
    basis: str


def batch_id(acquisition_id: str, key: str, t_start: int, t_end: int, value_type: str, value_text: str) -> str:
    h = hashlib.sha256(json.dumps([t_start, t_end, value_type, value_text], separators=(",", ":"),
                                  ensure_ascii=False).encode()).hexdigest()
    return f"winccoa:{acquisition_id}:{key}:{h}"


def _rel(collection: str, suffix: str = "") -> sql.Identifier:
    s, t = collection.split(".", 1)
    return sql.Identifier(s, t + suffix)


def acquisition_info(conn: psycopg.Connection, collection: str, acquisition_id: str):
    """(origin_ns or None, channels [(role, recording_id, offset_ns, rate, start_ns)]) as visible to the caller."""
    row = conn.execute(sql.SQL(
        "SELECT (extract(epoch FROM origin_utc) * 1000000000)::bigint FROM {} WHERE acquisition_id = %s").format(
        _rel(collection, "_acq")), (acquisition_id,)).fetchone()
    if row is None:
        return None, []
    chans = conn.execute(sql.SQL(
        "SELECT c.role, c.recording_id, c.offset_ns, r.sample_rate_hz, "
        "CASE WHEN jsonb_typeof(r.metadata->'start_ns') = 'number' THEN (r.metadata->>'start_ns')::numeric END "
        "FROM {} c JOIN {} r ON r.recording_id = c.recording_id WHERE c.acquisition_id = %s ORDER BY c.role").format(
        _rel(collection, "_acq_channels"), _rel(collection)), (acquisition_id,)).fetchall()
    return row[0], chans


def _maps_to_samples(conn, chans, t_start: int, t_end: int) -> bool:
    for _, _, offset, rate, _ in chans:
        a, b = conn.execute("SELECT * FROM morlet_time_to_samples(%s, %s, %s, %s)",
                            (rate, offset, t_start, t_end)).fetchone()
        if b > a:
            return True
    return False


def compute(cfg: Config, observations: list[tuple], origin_ns: int, end_ns: int | None) -> list[Outcome]:
    """The outcome of every change and snapshot, in obs_id order. Pure: the same stored rows give the same outcomes.

    `observations`: (obs_id, kind, asset, key, dpe, value_json_is_null, value_type, value_text, source_ns,
    quality_status, observed_ns, received_ns, rtt_ns) ordered by obs_id."""
    bound = int(cfg.clock.bound_ms * 1e6)
    daq = int(cfg.clock.daq_bound_ms * 1e6)
    clock_ok = False
    last_contact: int | None = None
    current: dict[str, _State] = {}
    out: list[Outcome] = []
    basis_of = {d.dpe: d for d in cfg.datapoints}
    end_abs = None if end_ns is None else origin_ns + end_ns

    def close(state: _State, key: str, basis: str, end_abs_t: int | None, closed_by: int | None,
              reason: str | None = None, detail: str = "") -> None:
        o = Outcome(state.obs_id, key, basis, t_start_ns=state.t_abs - origin_ns, closed_by=closed_by)
        if reason:
            o.reason, o.detail = reason, detail
        elif end_abs_t is None:
            o.reason = "open"
        else:
            if end_abs is not None and end_abs_t > end_abs:
                end_abs_t = end_abs        # clipped to the end of the finished acquisition
            o.t_end_ns = end_abs_t - origin_ns
            if not state.clock_ok or (closed_by is not None and not clock_ok):
                o.reason, o.detail = "clock_exceeded", "clock bound exceeded, or no heartbeat yet, at open or close"
            elif o.t_end_ns <= o.t_start_ns:
                o.reason, o.detail = "outside_recording", "empty interval"
            else:
                o.batch_id = "pending"     # mapped to samples and named by derive()
        out.append(o)

    for (obs_id, kind, asset, key, dpe, value_null, value_type, value_text, source_ns, quality_status, observed_ns,
         received_ns, rtt_ns) in observations:
        if kind == "heartbeat":
            est = abs(received_ns - observed_ns) + (rtt_ns or 0) // 2 + daq
            clock_ok = est <= bound
            last_contact = observed_ns if last_contact is None else max(last_contact, observed_ns)
            continue
        if kind == "gap":
            keys = [key] if key else sorted(k for k in current if k in cfg.keys_of(asset))
            for k in keys:
                st = current.pop(k, None)
                if st is not None:
                    gap_end = last_contact if last_contact is not None else st.t_abs
                    close(st, k, st.basis, gap_end, obs_id)
            last_contact = observed_ns if last_contact is None else max(last_contact, observed_ns)
            continue
        dp = basis_of.get(dpe)
        basis = dp.time_basis if dp else "source"
        if basis == "observed":
            t = observed_ns
        elif source_ns is None:
            t = None
        else:
            t = max(source_ns, observed_ns) if kind == "snapshot" else source_ns
        st = current.get(key)
        usable_quality = quality_status == "good" or (quality_status == "missing" and dp is not None
                                                       and dp.accept_missing_quality)
        if t is None:
            # No time: the previous state's end is unknown, so it is not derived either.
            if st is not None:
                close(current.pop(key), key, basis, None, obs_id, "untimed", "ended by a value without source time")
            out.append(Outcome(obs_id, key, basis, reason="untimed", detail="no source time"))
        elif st is not None and t < st.t_abs:
            out.append(Outcome(obs_id, key, basis, reason="out_of_order", t_start_ns=t - origin_ns,
                               detail="earlier than the state it would end"))
        elif not usable_quality:
            if st is not None:
                close(current.pop(key), key, basis, t, obs_id)
            out.append(Outcome(obs_id, key, basis, reason="bad_quality", t_start_ns=t - origin_ns,
                               detail=f"quality {quality_status}"))
        elif value_null:
            if st is not None:
                close(current.pop(key), key, basis, t, obs_id)
            out.append(Outcome(obs_id, key, basis, reason="unrepresentable", t_start_ns=t - origin_ns,
                               detail="value has no JSON form (kept as text)"))
        elif st is not None and st.value_type == value_type and st.value_text == value_text:
            out.append(Outcome(obs_id, key, basis, reason="unchanged", t_start_ns=t - origin_ns))
        else:
            if st is not None:
                close(current.pop(key), key, basis, t, obs_id)
            current[key] = _State(obs_id, t, value_type, value_text, clock_ok, basis)
        last_contact = observed_ns if last_contact is None else max(last_contact, observed_ns)
    for k in sorted(current):
        close(current[k], k, current[k].basis, end_abs, None)
    out.sort(key=lambda o: o.obs_id)
    return out


def _store_outcome(conn, cfg: Config, collection: str, acquisition_id: str, o: Outcome, channels: list[str],
                   rows_added: int, update: bool) -> None:
    table = sql.Identifier(cfg.schema, "derivation")
    values = (o.batch_id, o.reason, o.time_basis, o.t_start_ns, o.t_end_ns, o.closed_by, channels, rows_added,
              o.detail)
    if update:
        conn.execute(sql.SQL("UPDATE {} SET batch_id = %s, reason = %s, time_basis = %s, t_start_ns = %s, "
                             "t_end_ns = %s, closed_by = %s, channels = %s, rows_added = rows_added + %s, detail = %s, "
                             "derived_at = now() WHERE obs_id = %s").format(table), values + (o.obs_id,))
    else:
        conn.execute(sql.SQL("INSERT INTO {} (obs_id, collection, acquisition_id, key, batch_id, reason, time_basis, "
                             "t_start_ns, t_end_ns, closed_by, channels, rows_added, detail) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)").format(table),
                     (o.obs_id, collection, acquisition_id, o.key) + values)


def _add(conn, cfg: Config, collection: str, acquisition_id: str, o: Outcome) -> int:
    """Add the batch of a closed interval, its value read from the stored observation (no client-side conversion)."""
    return conn.execute(sql.SQL(
        "SELECT morlet_add_acquisition_context(%s::regclass, %s, %s, jsonb_build_array(jsonb_build_object("
        "'key', o.key, 'value', o.value_json, 't_start_ns', %s::bigint, 't_end_ns', %s::bigint))) "
        "FROM {} o WHERE o.obs_id = %s").format(sql.Identifier(cfg.schema, "observation")),
        (collection, acquisition_id, o.batch_id, o.t_start_ns, o.t_end_ns, o.obs_id)).fetchone()[0]


def derive(conn: psycopg.Connection, cfg: Config, collection: str, acquisition_id: str) -> Summary:
    """Derive context for one acquisition in one transaction (commits; `conn` must be in autocommit mode)."""
    summary = Summary(collection, acquisition_id)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('morlet-winccoa-derive/' || %s || '/' || %s, 0))",
                     (collection, acquisition_id))
        origin_ns, chans = acquisition_info(conn, collection, acquisition_id)
        obs_t = sql.Identifier(cfg.schema, "observation")
        rows = conn.execute(sql.SQL(
            "SELECT obs_id, kind, asset, key, dpe, value_json IS NULL, value_type, value_text, source_ns, "
            "quality_status, observed_ns, received_ns, rtt_ns FROM {} WHERE collection = %s AND acquisition_id = %s "
            "ORDER BY obs_id").format(obs_t), (collection, acquisition_id)).fetchall()
        existing = {r[0]: r for r in conn.execute(sql.SQL(
            "SELECT obs_id, batch_id, reason, channels FROM {} WHERE collection = %s AND acquisition_id = %s").format(
            sql.Identifier(cfg.schema, "derivation")), (collection, acquisition_id)).fetchall()}
        end = conn.execute(sql.SQL("SELECT end_ns FROM {} WHERE collection = %s AND acquisition_id = %s").format(
            sql.Identifier(cfg.schema, "acquisition_end")), (collection, acquisition_id)).fetchone()
        heartbeats = [r for r in rows if r[1] == "heartbeat"]
        if heartbeats:
            h = heartbeats[-1]
            summary.clock_estimate_ns = abs(h[11] - h[10]) + (h[12] or 0) // 2 + int(cfg.clock.daq_bound_ms * 1e6)
            summary.clock_ok = summary.clock_estimate_ns <= int(cfg.clock.bound_ms * 1e6)
        problem = None
        if origin_ns is None:
            problem = "the acquisition has no origin_utc"
        elif not chans:
            problem = "the acquisition has no visible channel"
        else:
            bad = [c[0] for c in chans if c[4] is None or int(c[4]) != origin_ns + c[2]]
            if bad:
                problem = f"start_ns differs from origin_ns + offset_ns on channel(s) {', '.join(bad)}"
        if problem:
            summary.inconsistent_start = problem
            for r in rows:
                if r[1] in ("change", "snapshot") and r[0] not in existing:
                    _store_outcome(conn, cfg, collection, acquisition_id,
                                   Outcome(r[0], r[3], "source", reason="inconsistent_start", detail=problem), [], 0,
                                   False)
                    summary.reasons["inconsistent_start"] = summary.reasons.get("inconsistent_start", 0) + 1
            return summary
        roles = [c[0] for c in chans]
        texts = {r[0]: (r[6], r[7]) for r in rows}
        for o in compute(cfg, rows, origin_ns, None if end is None else end[0]):
            if o.batch_id == "pending":
                if _maps_to_samples(conn, chans, o.t_start_ns, o.t_end_ns):
                    vt, vx = texts[o.obs_id]
                    o.batch_id = batch_id(acquisition_id, o.key, o.t_start_ns, o.t_end_ns, vt, vx)
                else:
                    o.batch_id, o.reason, o.detail = None, "outside_recording", "maps to no sample of any channel"
            prev = existing.get(o.obs_id)
            if prev is not None and prev[2] != "open":
                if (prev[1], prev[2]) != (o.batch_id, o.reason):
                    summary.mismatches += 1
                    log.warning("derivation of observation %s in %s/%s would now be %s; the stored %s stays",
                                o.obs_id, collection, acquisition_id, o.batch_id or o.reason, prev[1] or prev[2])
                elif prev[1] is not None and list(prev[3]) != roles:
                    n = _add(conn, cfg, collection, acquisition_id, o)    # a late channel: only it gets rows
                    _store_outcome(conn, cfg, collection, acquisition_id, o, roles, n, True)
                    summary.rows_added += n
                continue
            if prev is not None and o.reason == "open":
                continue
            n = _add(conn, cfg, collection, acquisition_id, o) if o.batch_id else 0
            _store_outcome(conn, cfg, collection, acquisition_id, o, roles if o.batch_id else [], n, prev is not None)
            if o.batch_id:
                summary.added_batches += 1
                summary.rows_added += n
            else:
                summary.reasons[o.reason] = summary.reasons.get(o.reason, 0) + 1
    return summary


def pending_acquisitions(conn: psycopg.Connection, cfg: Config) -> list[tuple[str, str]]:
    """Acquisitions with observations whose derivation is missing or still open, or with a batch that has not reached
    every current channel (a late channel)."""
    found = {tuple(r) for r in conn.execute(sql.SQL(
        "SELECT DISTINCT o.collection, o.acquisition_id FROM {o} o LEFT JOIN {d} d ON d.obs_id = o.obs_id "
        "WHERE o.kind IN ('change','snapshot') AND (d.obs_id IS NULL OR d.reason = 'open')").format(
        o=sql.Identifier(cfg.schema, "observation"), d=sql.Identifier(cfg.schema, "derivation"))).fetchall()}
    for coll in cfg.collections():
        found |= {tuple(r) for r in conn.execute(sql.SQL(
            "SELECT DISTINCT d.collection, d.acquisition_id FROM {d} d WHERE d.collection = %s AND d.batch_id IS NOT NULL "
            "AND d.channels IS DISTINCT FROM ARRAY(SELECT c.role FROM {ch} c WHERE c.acquisition_id = d.acquisition_id "
            "ORDER BY c.role)").format(d=sql.Identifier(cfg.schema, "derivation"), ch=_rel(coll, "_acq_channels")),
            (coll,)).fetchall()}
    return sorted(found)


def derive_all(conn: psycopg.Connection, cfg: Config, acquisitions: list[tuple[str, str]] | None = None,
               ) -> list[Summary]:
    out = []
    for coll, acq in (pending_acquisitions(conn, cfg) if acquisitions is None else acquisitions):
        if coll not in cfg.collections():
            continue
        out.append(derive(conn, cfg, coll, acq))
    return out


def open_states(conn: psycopg.Connection, cfg: Config, collection: str, acquisition_id: str) -> list[dict[str, Any]]:
    """The open state of each key ("open since t"), from the observation it started with."""
    rows = conn.execute(sql.SQL(
        "SELECT d.key, o.value_text, o.value_type, d.t_start_ns, o.obs_id FROM {d} d JOIN {o} o ON o.obs_id = d.obs_id "
        "WHERE d.collection = %s AND d.acquisition_id = %s AND d.reason = 'open' ORDER BY d.key").format(
        d=sql.Identifier(cfg.schema, "derivation"), o=sql.Identifier(cfg.schema, "observation")),
        (collection, acquisition_id)).fetchall()
    return [{"key": k, "value_text": vt, "value_type": ty, "open_since_ns": str(t), "obs_id": i}
            for k, vt, ty, t, i in rows]
