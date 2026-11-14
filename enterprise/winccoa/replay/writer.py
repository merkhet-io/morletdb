# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
"""The waveform writer of the replay: the DAQ side. Samples go through the MorletDB SDK; acquisitions, historical
context and labels through documented SQL functions (the SDK has none for them yet; docs/winccoa.md core change 1).

- Acquisition identity, channel, unit, rate, offsets and start time come from the writer, never from WinCC OA:
  `morlet_create_acquisition` (origin_utc, microseconds) and `morlet_add_acquisition_channel` (offset_ns), and the SDK
  stores start_ns = origin_ns + offset_ns in each recording's metadata.
- Samples are written in bounded batches (at most `batch_samples` per channel and transaction); each transaction
  also builds the search windows the batch completes, so samples become searchable at the commit that stores them.
- Resumable: a restarted writer continues from each recording's stored end; an identical replay of a stored batch is
  a no-op, and different samples at stored offsets fail (the SDK's ConflictError).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import morletdb
import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from machine import CHANNELS, MACHINE, Plan, synthesize

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def origin_utc(origin_ns: int) -> datetime:
    if origin_ns % 1000:
        raise ValueError("origin_utc has microsecond precision: origin_ns must be a multiple of 1000")
    return EPOCH + timedelta(microseconds=origin_ns // 1000)


def recording_id(plan: Plan, role: str) -> str:
    return f"{plan.acquisition_id}-{role}"


def setup_collection(conn: psycopg.Connection, collection: str) -> None:
    """A searchable collection with context and acquisitions (idempotent; commits)."""
    db = morletdb.Client(conn)
    db.create_collection(collection)
    s, t = collection.split(".", 1)
    for suffix, fn in (("_ctx", "morlet_enable_context"), ("_acq", "morlet_enable_acquisitions")):
        if conn.execute("SELECT to_regclass(%s)", (f"{s}.{t}{suffix}",)).fetchone()[0] is None:
            conn.execute(f"SELECT {fn}(%s::regclass)", (collection,))
    conn.commit()


def write_acquisition(conn: psycopg.Connection, collection: str, plan: Plan, origin_ns: int, *,
                      batch_samples: int = 65_536, metadata: dict | None = None,
                      stop_after_batches: int | None = None,
                      on_batch: Callable[[int, dict[str, int]], None] | None = None) -> dict[str, np.ndarray]:
    """Write (or resume) an acquisition. Returns the samples of each role. `stop_after_batches` stops early, as an
    interrupted writer would; `on_batch(i, ends)` runs after each committed batch with the stored end of each role."""
    db = morletdb.Client(conn)
    data = synthesize(plan)
    meta = {"machine": MACHINE, "acquisition": plan.acquisition_id, **(metadata or {})}
    roles = sorted(CHANNELS)
    # Batch 0 creates the acquisition and its channels in one transaction (under row security, a non-owner adds an
    # acquisition's first channel in the transaction that creates it).
    with conn.transaction():
        conn.execute("SELECT morlet_create_acquisition(%s::regclass, %s, %s, %s, %s)",
                     (collection, plan.acquisition_id, MACHINE, origin_utc(origin_ns), Jsonb(meta)))
        for role in roles:
            rate, channel, unit, offset, clock, unc = CHANNELS[role]
            rid = recording_id(plan, role)
            exists = db.recordings(collection, recording_id=rid)
            if not exists:
                db.ingest(collection, rid, data[role][:batch_samples], sample_rate_hz=rate, channel=channel, unit=unit,
                          start_time=origin_ns + offset, metadata={**meta, "role": role})
            conn.execute("SELECT morlet_add_acquisition_channel(%s::regclass, %s, %s, %s, %s, %s, %s)",
                         (collection, plan.acquisition_id, rid, role, offset, clock, unc))
    if on_batch:
        on_batch(0, {r: db.recording(collection, recording_id(plan, r)).samples for r in roles})
    conn.commit()
    i = 1
    while True:
        if stop_after_batches is not None and i >= stop_after_batches:
            break
        ends = {r: db.recording(collection, recording_id(plan, r)).samples for r in roles}
        if all(ends[r] >= data[r].size for r in roles):
            break
        with conn.transaction():
            for role in roles:
                x = data[role]
                a = ends[role]
                if a >= x.size:
                    continue
                db.append(collection, recording_id(plan, role), a, x[a:a + batch_samples])
                db.build_search_index(collection, recording_id(plan, role))
        conn.commit()
        if on_batch:
            on_batch(i, {r: db.recording(collection, recording_id(plan, r)).samples for r in roles})
        i += 1
    return data


def write_history_context(conn: psycopg.Connection, collection: str, plan: Plan) -> None:
    """Historical context and labels, written directly as a historical import (not through WinCC OA)."""
    rows = []
    for key in ("speed", "load"):
        for c in plan.contexts:      # one interval per state: consecutive contexts with the same value are merged
            v = getattr(c, key)
            if rows and rows[-1]["key"] == key and rows[-1]["value"] == v and rows[-1]["t_end_ns"] == c.t_start_ns:
                rows[-1]["t_end_ns"] = c.t_end_ns
            else:
                rows.append({"key": key, "value": v, "t_start_ns": c.t_start_ns, "t_end_ns": c.t_end_ns})
    with conn.transaction():
        conn.execute("SELECT morlet_add_acquisition_context(%s::regclass, %s, %s, %s)",
                     (collection, plan.acquisition_id, f"replay-history:{plan.acquisition_id}", Jsonb(rows)))
        add_labels(conn, collection, plan)
    conn.commit()


def add_labels(conn: psycopg.Connection, collection: str, plan: Plan) -> None:
    """The synthetic event labels on the vib recording (once: labels have no batch id, so they are checked first)."""
    s, t = collection.split(".", 1)
    rid = recording_id(plan, "vib")
    for e in plan.events:
        found = conn.execute(f'SELECT 1 FROM "{s}"."{t}_labels" WHERE recording_id = %s AND start_sample = %s '
                             "AND end_sample = %s AND label = %s AND NOT retracted",
                             (rid, e.vib_start, e.vib_start + e.length, e.label)).fetchone()
        if found is None:
            conn.execute("SELECT morlet_add_label(%s::regclass, %s, %s, %s, %s, %s)",
                         (collection, rid, e.vib_start, e.vib_start + e.length, e.label,
                          "synthetic event (replay); an annotation, not a diagnosis"))
