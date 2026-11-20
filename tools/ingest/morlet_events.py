#!/usr/bin/env python3
"""Import alarm, PLC and operator messages as context events on the recordings they happened in (#421).

  python tools/ingest/morlet_events.py alarms.csv --collection public.line3 --time-column time \\
      --key alarm --value-columns code,text [--match machine] [--metadata-filter '{"line": 3}'] [--dry-run]

A message log is a CSV with a header: one time column (ISO 8601, UTC when no offset is given) and the columns that
describe the message. Every message becomes one context event (kind 'event': a sample, no end) under --key on each
recording whose time range contains it, at the sample its time maps to through the recording's start time and rate.
--match <field> restricts a message to recordings whose metadata <field> equals the message's column <field>
(e.g. the machine). When the recording's import measured how its own timestamps departed from start + rate
(metadata time_axis, #393), the event carries that as its time uncertainty. Recordings with time anchors (SQL
0.3.13; the importer writes them from the source's timestamps) are placed by the anchors instead, exactly at every
anchor, with time_basis "anchors", the bound on the error, and the candidate samples when a clock step makes the
time occur twice (the event goes on the first). Messages outside every recording are
counted, not stored. One batch per recording and file: an identical re-import is a no-op.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def start_time(meta: dict) -> datetime | None:
    """As the MCP server reads it: start_ns (integer UTC ns), else start, starttime, start_time (ISO 8601)."""
    ns = meta.get("start_ns")
    if isinstance(ns, int) and not isinstance(ns, bool):
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ns // 1000)
    for key in ("start", "starttime", "start_time"):
        if meta.get(key):
            try:
                return parse_time(str(meta[key])).astimezone(timezone.utc)
            except ValueError:
                return None
    return None


def read_messages(path: Path, time_column: str, value_columns: list[str], match: str | None) -> list[dict]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{path.name}: no messages")
    missing = [c for c in [time_column, *value_columns, *([match] if match else [])] if c not in rows[0]]
    if missing:
        raise ValueError(f"{path.name}: no column(s) {', '.join(missing)}")
    out = []
    for i, r in enumerate(rows):
        try:
            t = parse_time(r[time_column])
        except ValueError as e:
            raise ValueError(f"{path.name} row {i + 2}: time {r[time_column]!r} is not ISO 8601") from e
        out.append({"time": t, "value": {c: r[c] for c in value_columns}, "match": r.get(match) if match else None})
    return out


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


RESERVED_VALUE_KEYS = {"time_utc", "time_basis", "time_uncertainty_s", "time_candidate_samples"}


def utc_ns(t: datetime) -> int:
    return (t - EPOCH) // timedelta(microseconds=1) * 1000


def anchor_locator(conn, collection: str, times: list[datetime]):
    """For recordings with time anchors (SQL 0.3.13, #393): (set of anchored recording ids, locate), where
    locate(recording_id, time, end) -> (sample, uncertainty_s, candidates) is the sample whose period contains the
    time by the server's exact mapping, or None outside the recording. All message times of a recording are mapped
    in one query, on its first use."""
    if conn.execute("SELECT to_regproc('public.morlet_utc_to_sample') IS NULL").fetchone()[0]:
        return set(), None
    rel = conn.execute("SELECT to_regclass(format('%%I.%%I', n.nspname, c.relname || '_time_anchors'))::text "
                       "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = %s::regclass",
                       (collection,)).fetchone()[0]
    if rel is None:
        return set(), None
    anchored = {r[0] for r in conn.execute(
        f"SELECT DISTINCT x.recording_id FROM {rel} x WHERE NOT x.retracted AND NOT EXISTS "
        f"(SELECT 1 FROM {rel} y WHERE y.supersedes = x.anchor_id)").fetchall()}
    wanted = sorted({utc_ns(t) for t in times})
    cache: dict[str, dict] = {}

    def mapped(rid: str) -> dict:
        """time ns -> [(sample, uncertainty_s)] for the period containing it, every candidate."""
        if rid not in cache:
            rows = conn.execute(
                "SELECT t.ns, s.sample, s.utc_ns, s.gap, s.before_start FROM unnest(%s::numeric[]) t(ns), "
                "LATERAL morlet_utc_to_sample(%s::regclass, %s, t.ns) s ORDER BY t.ns, s.sample",
                (wanted, collection, rid)).fetchall()
            # the period containing the time: the last sample at or before it (also inside a clock jump)
            picks = [(int(tn), sample if utc == tn else sample - 1) for tn, sample, utc, _gap, before in rows
                     if not before]
            samples = sorted({n for _, n in picks if n >= 0})
            unc = dict(conn.execute("SELECT n, s.uncertainty_ns FROM unnest(%s::bigint[]) n, "
                                    "LATERAL morlet_sample_utc(%s::regclass, %s, n) s",
                                    (samples, collection, rid)).fetchall()) if samples else {}
            out: dict[int, list] = {}
            for tn, n in picks:
                if n >= 0:
                    u = unc.get(n)
                    out.setdefault(tn, []).append((n, None if u is None else float(u) / 1e9))
            cache[rid] = out
        return cache[rid]

    def locate(rid: str, t: datetime, end: int):
        found = [(n, u) for n, u in mapped(rid).get(utc_ns(t), []) if n < end]
        if not found:
            return None
        return found[0][0], found[0][1], [f[0] for f in found]
    return anchored, locate


def place(messages: list[dict], recordings: list[dict], key: str, match: str | None,
          locate=None) -> tuple[dict, int]:
    """Context event rows per recording, and the number of messages that fell into no recording. Recordings with
    "anchored": True are placed by locate (anchor_locator); the others by start + rate."""
    per_rec: dict[str, list] = {}
    outside = 0
    for msg in messages:
        hit = False
        for r in recordings:
            if match and str((r["metadata"] or {}).get(match)) != str(msg["match"]):
                continue
            extra = {}
            if r.get("anchored"):
                found = locate(r["recording_id"], msg["time"], r["samples"])
                if found is None:
                    continue
                sample, unc, candidates = found
                extra["time_basis"] = "anchors"
                if unc is not None:
                    extra["time_uncertainty_s"] = unc
                if len(candidates) > 1:
                    extra["time_candidate_samples"] = candidates      # the clock stepped back: the time occurs twice
            else:
                dt = (msg["time"] - r["t0"]).total_seconds()
                sample = math.floor(dt * r["rate"])
                ax = (r["metadata"] or {}).get("time_axis") or {}
                if isinstance(ax.get("max_offset_s"), (int, float)):
                    extra["time_uncertainty_s"] = ax["max_offset_s"]
            if 0 <= sample < r["samples"]:
                value = dict(msg["value"])
                value["time_utc"] = msg["time"].isoformat().replace("+00:00", "Z")
                value.update(extra)
                per_rec.setdefault(r["recording_id"], []).append(
                    {"recording_id": r["recording_id"], "key": key, "value": value, "start_sample": sample})
                hit = True
        outside += not hit
    return per_rec, outside


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("file", type=Path)
    p.add_argument("--collection", required=True)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--time-column", required=True)
    p.add_argument("--key", required=True, help="context key of the events, e.g. alarm or plc_message")
    p.add_argument("--value-columns", required=True, help="comma-separated columns stored as the event's value")
    p.add_argument("--match", help="metadata field the message's column of the same name must equal (e.g. machine)")
    p.add_argument("--metadata-filter", default="{}", help="JSON object: only recordings whose metadata contains it")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    try:
        cols = [c.strip() for c in a.value_columns.split(",") if c.strip()]
        reserved = sorted(set(cols) & RESERVED_VALUE_KEYS)
        if reserved:
            raise ValueError(f"value columns {', '.join(reserved)} are reserved for the event's time information; "
                             "rename them")
        msgs = read_messages(a.file, a.time_column, cols, a.match)
        import psycopg
        with psycopg.connect(a.dsn, autocommit=True) as conn:
            coll = conn.execute("SELECT to_regclass(%s)::text", (a.collection,)).fetchone()[0]
            if coll is None:
                raise ValueError(f"collection {a.collection} does not exist")
            rows = conn.execute(f"SELECT recording_id, sample_rate_hz, metadata, (checkpoint->>'end')::bigint "
                                f"FROM {coll} WHERE metadata @> %s::jsonb",
                                (json.dumps(json.loads(a.metadata_filter)),)).fetchall()
            anchored, locate = anchor_locator(conn, coll, [m["time"] for m in msgs])
            recs = [{"recording_id": rid, "rate": rate, "metadata": meta, "samples": n or 0, "t0": start_time(meta or {}),
                     "anchored": rid in anchored} for rid, rate, meta, n in rows]
            recs = [r for r in recs if (r["t0"] is not None or r["anchored"]) and r["samples"] > 0]
            per_rec, outside = place(msgs, recs, a.key, a.match, locate)
            digest = hashlib.sha256(a.file.read_bytes()).hexdigest()[:16]
            report = {"messages": len(msgs), "recordings_with_events": len(per_rec),
                      "events": sum(len(v) for v in per_rec.values()), "messages_outside_recordings": outside}
            if a.dry_run:
                print(json.dumps({"status": "dry-run", **report}, indent=2))
                return 0
            if conn.execute("SELECT to_regclass(%s)", (a.collection + "_ctx",)).fetchone()[0] is None:
                conn.execute("SELECT morlet_enable_context(%s::regclass)", (a.collection,))
            added = 0
            for rid, ev in sorted(per_rec.items()):
                added += conn.execute("SELECT morlet_add_context(%s::regclass, %s, %s::jsonb)",
                                      (a.collection, f"events-{a.key}-{digest}-{rid}", json.dumps(ev))).fetchone()[0]
            print(json.dumps({"status": "complete", **report, "context_rows_added": added}, indent=2))
            return 0
    except (ValueError, OSError) as e:
        print(json.dumps({"status": "failed", "error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
