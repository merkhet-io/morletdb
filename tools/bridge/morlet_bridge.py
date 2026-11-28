#!/usr/bin/env python3
"""MorletDB metric bridge (#639), experimental: publish interval metrics with exact provenance, then deliver them.

MorletDB keeps the raw samples and a ledger of every published metric. Sinks are copies of the ledger:
- a PostgreSQL table in the same database (an ordinary table or a TimescaleDB hypertable), delivered in the
  ledger's transaction, so exactly once;
- InfluxDB line protocol, for Telegraf (`execd` input) or any InfluxDB-compatible writer, delivered at least once.
  A point is identified by its measurement, tags and timestamp, so a repeated delivery overwrites itself.

All writes use the caller's privileges. A database is one trust domain: sources with row security are refused,
because the ledger and its sinks do not inherit recording policies. The publication core is adapted from the
TimescaleDB prototype in #638.
"""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA_VERSION = 1
CALCULATION = "interval-stats-v1"
STATS = ("minimum", "maximum", "mean", "rms")
MAX_WINDOW = 1048576


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                                     default=str).encode()).hexdigest()


def integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def finite_or_none(x):
    return x if x is not None and math.isfinite(x) else None


class Bridge:
    def __init__(self, connection):
        self.conn = connection
        self._publishing = False

    # ---- helpers
    def one(self, query, params=()):
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchone()

    def all(self, query, params=()):
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    @contextmanager
    def transaction(self):
        """One repeatable-read snapshot for source, provenance, ledger and progress. The caller may append
        samples inside it, so the waveform and its metric commit or roll back together."""
        if self.conn.info.transaction_status != TransactionStatus.IDLE or not self.conn.autocommit:
            raise ValueError("bridge.transaction needs an idle autocommit connection")
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            self.conn.execute("SET LOCAL statement_timeout = 30000")
            self._publishing = True
            try:
                yield self
            finally:
                self._publishing = False

    def _write_context(self):
        if not self._publishing or self.conn.info.transaction_status != TransactionStatus.INTRANS:
            raise ValueError("use bridge.transaction() for publication")
        version = self.one("SELECT schema_version FROM morlet_bridge.config")["schema_version"]
        if version != SCHEMA_VERSION:
            raise ValueError("unsupported bridge schema version")

    def install(self):
        """Fresh installation of the bridge schema. Changes no existing tables or grants."""
        if self.conn.info.transaction_status != TransactionStatus.IDLE:
            raise ValueError("install needs an idle connection")
        with self.conn.transaction():
            self.conn.execute(Path(__file__).with_name("schema.sql").read_text())

    # ---- sources
    def source(self, collection, recording):
        """(qualified collection, specification, time-axis version, interpretation revision, received end)."""
        # Row security on the collection (set by its owner) or on any of its companions `<collection>_*`
        # (morlet_enable_row_security); a table that merely shares the prefix also counts, erring on refusal.
        rel = self.one("""SELECT n.nspname, c.relname, format('%%I.%%I', n.nspname, c.relname) AS qualified,
            c.relrowsecurity OR EXISTS (SELECT 1 FROM pg_class k WHERE k.relnamespace = n.oid AND k.relrowsecurity
              AND left(k.relname, length(c.relname) + 1) = c.relname || '_') AS relrowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.oid = to_regclass(%s) AND c.relkind = 'r'""", (collection,))
        if not rel:
            raise ValueError("source collection unavailable")
        if rel["relrowsecurity"]:
            raise ValueError("sources with row security are unsupported: use one database per trust domain")
        row = self.one(sql.SQL("SELECT * FROM {} WHERE recording_id = %s").format(
            sql.Identifier(rel["nspname"], rel["relname"])), (recording,))
        if not row:
            raise ValueError("source recording unavailable")
        spec = json.loads(json.dumps({k: v for k, v in row.items() if k != "checkpoint"}, default=str))
        axis = self.one("SELECT public.morlet_time_axis_version(%s::regclass, %s) AS v",
                        (rel["qualified"], recording))["v"]
        return rel["qualified"], spec, axis, self._revision(rel["qualified"], recording), int(row["checkpoint"]["end"])

    def _revision(self, collection, recording):
        """The interpretation revision (#600), None when the collection tracks no history. History that exists but
        cannot be read completely (SQL before 0.3.18, missing privileges, other row policies) is refused: a metric
        must not claim a revision it cannot know (#608)."""
        tracked = self.one("""SELECT to_regprocedure('public.morlet_interpretation_relation(regclass)') IS NOT NULL
            AND public.morlet_interpretation_relation(%s::regclass) IS NOT NULL AS t""", (collection,))["t"]
        if not tracked:
            return None
        ok = self.one("""SELECT to_regprocedure('public.morlet_interpretation_history_complete(regclass)') IS NOT NULL
            AND has_function_privilege('public.morlet_interpretation_revisions(regclass,text[])', 'EXECUTE') AS ok""")
        row = self.one("SELECT revision FROM public.morlet_interpretation_revisions(%s::regclass, ARRAY[%s])",
                       (collection, recording)) if ok["ok"] else None
        if row is None:
            raise ValueError("interpretation history is not completely readable; its revision cannot be recorded")
        return row["revision"]

    def register(self, collection, recording, asset, channel, window_samples=4096):
        self._write_context()
        integer(window_samples, "window_samples")
        if not 1 <= window_samples <= MAX_WINDOW:
            raise ValueError(f"window_samples must be in 1..{MAX_WINDOW}")
        for name, v in (("asset", asset), ("channel", channel)):
            if not isinstance(v, str) or not v:
                raise ValueError(f"{name} must be a nonempty string")
        qualified, spec, axis, revision, _ = self.source(collection, recording)
        self.conn.execute("""INSERT INTO morlet_bridge.sources (collection, recording_id, asset, channel,
            window_samples, calculation, specification, time_axis_version, interpretation_revision)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (qualified, recording, asset, channel, window_samples, CALCULATION, Jsonb(spec), axis, revision))
        row = self.one("""SELECT * FROM morlet_bridge.sources
            WHERE collection = %s AND recording_id = %s AND calculation = %s FOR UPDATE""",
            (qualified, recording, CALCULATION))
        if (row["asset"], row["channel"], row["window_samples"], row["specification"], row["time_axis_version"],
                row["interpretation_revision"]) != (asset, channel, window_samples, spec, axis, revision):
            raise ValueError("conflicting source registration")
        return row["source_id"]

    def _registered(self, source_id):
        row = self.one("SELECT * FROM morlet_bridge.sources WHERE source_id = %s", (source_id,))
        if not row:
            raise ValueError("source registration unavailable")
        _, spec, axis, revision, received = self.source(row["collection"], row["recording_id"])
        if spec != row["specification"] or axis != row["time_axis_version"]:
            raise ValueError("source specification or time axis changed; earlier metrics need review")
        if revision != row["interpretation_revision"]:
            raise ValueError("interpretation revision changed (a unit or calibration correction); earlier metrics "
                             "need review")
        return row, received

    def _time(self, row, sample):
        if row["time_axis_version"] == "start+rate":
            origin = row["specification"]["metadata"].get("start_ns")
            integer(origin, "recording metadata.start_ns")
            return self.all("""SELECT public.morlet_sample_time_ns(%s, %s, %s) AS utc_ns,
                NULL::numeric AS uncertainty_ns, 'start+rate' AS basis, 0 AS segment""",
                (row["specification"]["sample_rate_hz"], origin, sample))
        return self.all("SELECT * FROM public.morlet_sample_utc(%s::regclass, %s, %s)",
                        (row["collection"], row["recording_id"], sample))

    # ---- publication
    def publish(self, source_id, start, end):
        """Publish one complete, aligned window from the stored samples; callers cannot supply values."""
        self._write_context()
        integer(start, "start")
        integer(end, "end")
        row, received = self._registered(source_id)
        if start < 0 or end <= start or end > received:
            raise ValueError("interval must lie within the samples received")
        if start % row["window_samples"] or end - start != row["window_samples"]:
            raise ValueError("publication must use one complete, aligned registered window")
        self.conn.execute("SELECT 1 FROM morlet_bridge.sources WHERE source_id = %s FOR UPDATE", (source_id,))
        a, b = self._time(row, start), self._time(row, end)
        if len(a) != 1 or len(b) != 1 or a[0]["segment"] != b[0]["segment"]:
            raise ValueError("missing or ambiguous time, or the interval crosses a clock break")
        t0, t1 = a[0]["utc_ns"], b[0]["utc_ns"]
        if any(v != v.to_integral_value() or not -(2 ** 63) <= v < 2 ** 63 for v in (t0, t1)) or t1 <= t0:
            raise ValueError("interval endpoints must be increasing, exact bigint nanoseconds; nothing is rounded")
        # Line-protocol sinks identify a point by measurement, tags (asset, calculation, channel) and timestamp:
        # two recordings of one asset and channel must never publish at the same time, or one point would
        # overwrite the other downstream. Serialised per asset and channel.
        self.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                          (json.dumps(["morlet-bridge-series", row["asset"], row["channel"], row["calculation"]]),))
        clash = self.one("""SELECT s.recording_id FROM morlet_bridge.ledger l JOIN morlet_bridge.sources s USING (source_id)
            WHERE s.asset = %s AND s.channel = %s AND s.calculation = %s AND l.time_ns = %s AND l.source_id <> %s
            LIMIT 1""", (row["asset"], row["channel"], row["calculation"], int(t0), source_id))
        if clash:
            raise ValueError(f"recording {clash['recording_id']} already published asset {row['asset']!r}, channel "
                             f"{row['channel']!r} at this time; points would overwrite each other downstream")
        args = (row["collection"], row["recording_id"], start, end)
        raw = bytes(self.one("SELECT public.morlet_read_interval_raw(%s::regclass, %s, %s, %s) AS raw", args)["raw"])
        st = self.one("SELECT * FROM public.morlet_interval_stats(%s::regclass, %s, %s, %s)", args)
        if len(raw) != 8 * (end - start) or st["sample_count"] != end - start:
            raise ValueError("incomplete source interval")
        values = {"minimum": st["min"], "maximum": st["max"], "mean": st["mean"], "rms": st["rms"]}
        source_sha = hashlib.sha256(raw).hexdigest()
        provenance = {"start": a[0], "end": b[0], "time_axis_version": row["time_axis_version"],
                      "interpretation_revision": row["interpretation_revision"], "calculation": CALCULATION}
        provenance = json.loads(json.dumps(provenance, default=str))
        payload = digest({"source_sha256": source_sha, "values": {k: repr(v) for k, v in values.items()},
                          "provenance": provenance, "specification": row["specification"]})
        self.conn.execute("""INSERT INTO morlet_bridge.ledger (source_id, start_sample, end_sample, time_ns,
            end_time_ns, sample_count, minimum, maximum, mean, rms, source_sha256, payload_sha256, provenance)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (source_id, start, end, int(t0), int(t1), st["sample_count"], *(values[k] for k in STATS),
             source_sha, payload, Jsonb(provenance)))
        led = self.one("""SELECT * FROM morlet_bridge.ledger
            WHERE source_id = %s AND start_sample = %s AND end_sample = %s FOR UPDATE""", (source_id, start, end))
        expected = {"time_ns": int(t0), "end_time_ns": int(t1), "sample_count": st["sample_count"],
                    "source_sha256": source_sha, "payload_sha256": payload, "provenance": provenance, **values}
        # floats by repr, so -0.0 and 0.0 or NaN payloads count as different; provenance (jsonb) by value
        if any((led[k] != v) if k == "provenance" else (repr(led[k]) != repr(v)) for k, v in expected.items()):
            raise ValueError("conflicting metric retry: the source or the calculated payload changed")
        return led["ledger_id"]

    def drain(self, source_id, max_windows=100):
        """Publish complete windows after the source's progress, one transaction each. Restart-safe and bounded."""
        integer(max_windows, "max_windows")
        if not 1 <= max_windows <= 10000:
            raise ValueError("max_windows must be in 1..10000")
        done = 0
        for _ in range(max_windows):
            with self.transaction():
                row, received = self._registered(source_id)
                start = row["processed_end"]
                end = start + row["window_samples"]
                if end > received:
                    break
                self.publish(source_id, start, end)
                self.conn.execute("UPDATE morlet_bridge.sources SET processed_end = %s WHERE source_id = %s",
                                  (end, source_id))
            done += 1
        return {"published_windows": done, **self.status(source_id)}

    def status(self, source_id):
        row, received = self._registered(source_id)
        complete = received // row["window_samples"] * row["window_samples"]
        return {"source_id": source_id, "received_end": received, "processed_end": row["processed_end"],
                "pending_complete_samples": complete - row["processed_end"],
                "partial_tail_samples": received - complete, "complete": complete == row["processed_end"]}

    # ---- sinks
    def add_sink(self, name, kind, target=None, options=None):
        if kind == "postgres":
            target = target or "morlet_bridge.metrics"
            rel = self.one("SELECT format('%%I.%%I', n.nspname, c.relname) AS q FROM pg_class c "
                           "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = to_regclass(%s)", (target,))
            if not rel:
                raise ValueError("sink target table unavailable")
            target = rel["q"]
        elif kind != "line" or target is not None:
            raise ValueError("kind must be 'postgres' (with a target table) or 'line'")
        self.conn.execute("""INSERT INTO morlet_bridge.sinks (name, kind, target, options) VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO NOTHING""", (name, kind, target, Jsonb(options or {})))
        row = self.one("SELECT * FROM morlet_bridge.sinks WHERE name = %s", (name,))
        if (row["kind"], row["target"], row["options"]) != (kind, target, options or {}):
            raise ValueError("conflicting sink definition")
        return row["sink_id"]

    def _sink(self, name):
        row = self.one("SELECT * FROM morlet_bridge.sinks WHERE name = %s", (name,))
        if not row:
            raise ValueError(f"no sink {name!r}")
        return row

    PENDING = """SELECT l.*, s.asset, s.channel, s.calculation, s.collection, s.recording_id,
        s.interpretation_revision FROM morlet_bridge.ledger l JOIN morlet_bridge.sources s USING (source_id)
        WHERE NOT EXISTS (SELECT 1 FROM morlet_bridge.deliveries d WHERE d.sink_id = %s AND d.ledger_id = l.ledger_id)
        ORDER BY l.ledger_id LIMIT %s"""

    def deliver(self, name, limit=1000):
        """PostgreSQL sink: copy undelivered ledger rows into the target table and record their delivery in the
        same transaction. An existing target row must equal the ledger, so a changed copy is reported, not hidden."""
        sink = self._sink(name)
        if sink["kind"] != "postgres":
            raise ValueError("deliver is for postgres sinks; use lines() for line protocol")
        resolved = self.one("SELECT format('%%I.%%I', n.nspname, c.relname) AS q FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid = c.relnamespace WHERE c.oid = to_regclass(%s)", (sink["target"],))
        if not resolved or resolved["q"] != sink["target"]:
            raise ValueError(f"sink {name}: target table {sink['target']} unavailable")
        target = sql.SQL(resolved["q"])         # quoted by format('%I.%I') above
        cols = ("ledger_id", "time_ns", "end_time_ns", "asset", "channel", "calculation", "collection",
                "recording_id", "start_sample", "end_sample", "sample_count", *STATS, "interpretation_revision")
        try:
            with self.conn.transaction():
                self.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                                  (f"morlet-bridge-sink-{sink['sink_id']}",))
                rows = self.all(self.PENDING, (sink["sink_id"], limit))
                for r in rows:
                    self.conn.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (time_ns, ledger_id) DO NOTHING")
                                      .format(target, sql.SQL(", ").join(map(sql.Identifier, cols)),
                                              sql.SQL(", ").join(sql.Placeholder() * len(cols))),
                                      [r[c] for c in cols])
                    got = self.one(sql.SQL("SELECT {} FROM {} WHERE time_ns = %s AND ledger_id = %s").format(
                        sql.SQL(", ").join(map(sql.Identifier, cols)), target), (r["time_ns"], r["ledger_id"]))
                    if any(repr(got[c]) != repr(r[c]) for c in cols):
                        raise ValueError(f"sink {name}: target row {r['ledger_id']} differs from the ledger")
                    self.conn.execute("INSERT INTO morlet_bridge.deliveries (sink_id, ledger_id) VALUES (%s, %s)",
                                      (sink["sink_id"], r["ledger_id"]))
                if rows:
                    self.conn.execute("UPDATE morlet_bridge.sinks SET last_delivery_at = clock_timestamp() "
                                      "WHERE sink_id = %s", (sink["sink_id"],))
        except (ValueError, psycopg.Error) as exc:
            self._error(sink["sink_id"], exc)
            raise
        return len(rows)

    def _error(self, sink_id, exc):
        if self.conn.info.transaction_status == TransactionStatus.IDLE:
            self.conn.execute("UPDATE morlet_bridge.sinks SET last_error = %s, last_error_at = clock_timestamp() "
                              "WHERE sink_id = %s", (str(exc)[:500], sink_id))

    def lines(self, name, write, limit=1000):
        """Line-protocol sink: write undelivered rows with `write(text)`, then record them as delivered. If the
        process stops in between, the rows are written again on the next call (at least once)."""
        sink = self._sink(name)
        if sink["kind"] != "line":
            raise ValueError("lines is for line sinks")
        measurement = sink["options"].get("measurement", "morlet_metric")
        rows = self.all(self.PENDING, (sink["sink_id"], limit))
        if not rows:
            return 0
        write("".join(line(measurement, r) + "\n" for r in rows))
        with self.conn.transaction():
            for r in rows:
                self.conn.execute("""INSERT INTO morlet_bridge.deliveries (sink_id, ledger_id) VALUES (%s, %s)
                    ON CONFLICT DO NOTHING""", (sink["sink_id"], r["ledger_id"]))
            self.conn.execute("UPDATE morlet_bridge.sinks SET last_delivery_at = clock_timestamp(), last_error = NULL "
                              "WHERE sink_id = %s", (sink["sink_id"],))
        return len(rows)

    def sink_status(self, name):
        sink = self._sink(name)
        r = self.one("""SELECT (SELECT count(*) FROM morlet_bridge.ledger) AS published,
            (SELECT count(*) FROM morlet_bridge.deliveries WHERE sink_id = %s) AS delivered""", (sink["sink_id"],))
        return {"sink": name, "kind": sink["kind"], "published": r["published"], "delivered": r["delivered"],
                "pending": r["published"] - r["delivered"], "last_delivery_at": sink["last_delivery_at"],
                "last_error": sink["last_error"], "last_error_at": sink["last_error_at"]}


# ---- InfluxDB line protocol (https://docs.influxdata.com/influxdb/v2/reference/syntax/line-protocol/)
def _escape_key(s):
    return s.replace("\\", "\\\\").replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ").replace("\n", "\\n")


def _escape_measurement(s):
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("\n", "\\n")


def _string(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def line(measurement, r):
    """One point per ledger row. Tags (indexed) are asset, channel and calculation only, so the series count does
    not grow with recordings; the recording, its interval and the ledger ID are fields, enough to open the
    original samples. Floats are written in Python's shortest round-trip form, so they parse back to the same
    float64; NaN and infinities have no line-protocol form and are left out and named in `nonfinite`."""
    tags = ",".join(f"{k}={_escape_key(str(r[k]))}" for k in ("asset", "calculation", "channel"))
    fields, nonfinite = [], []
    for k in STATS:
        v = r[k]
        if v is None or not math.isfinite(v):
            nonfinite.append(k)
        else:
            fields.append(f"{k}={float(v)!r}")
    fields += [f"sample_count={int(r['sample_count'])}i", f"start_sample={int(r['start_sample'])}i",
               f"end_sample={int(r['end_sample'])}i", f"ledger_id={int(r['ledger_id'])}i",
               f"end_time_ns={int(r['end_time_ns'])}i", f"recording_id={_string(r['recording_id'])}",
               f"collection={_string(r['collection'])}"]
    if r.get("interpretation_revision") is not None:
        fields.append(f"interpretation_revision={int(r['interpretation_revision'])}i")
    if nonfinite:
        fields.append(f"nonfinite={_string(','.join(nonfinite))}")
    return f"{_escape_measurement(measurement)},{tags} {','.join(fields)} {int(r['time_ns'])}"
