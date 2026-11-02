#!/usr/bin/env python3
"""Reference MQTT collector for the morlet-mqtt/1 contract (README.md): sample batches and context into MorletDB.

  python examples/collectors/mqtt/collector.py --host 127.0.0.1 --port 1883 --collection public.mqtt [--create]
      [--client-id morlet-collector-1] [--gap-timeout 5] [--run-seconds 60]

Topics: morlet/<site>/<asset>/<channel>/samples and morlet/<site>/<asset>/<channel>/context, QoS 1, with a
persistent session (fixed client id, clean session off), so the broker keeps messages while the collector is
away. A message is acknowledged only after its effect is committed in the database, so a crash leads to
redelivery, not loss. Exactly-once storage comes from MorletDB's idempotent appends, not from MQTT.

Samples: the batch is appended at its start_sample to the recording <acquisition>.<channel>.<segment start>.
- A batch at the current end is appended; later batches that are already waiting follow.
- A batch before the end is a duplicate: an identical one is accepted unchanged, an altered one is refused and
  reported.
- A batch after the end waits (unacknowledged) for the missing ones. If they have not arrived after
  --gap-timeout seconds, or more than --max-pending batches wait, the missing range is reported as a gap and a
  new recording starts at the first waiting batch. Samples are never invented.
Context: each message becomes a context event of the recording whose samples cover its source time (sample =
(source time - acquisition start_time) * rate), with value, status and source time. Context ahead of the stored
samples waits, unacknowledged, until they arrive; context in a gap is reported and not stored.
"""
import argparse
import json
import os
import queue
import sys
import time
from datetime import datetime

import psycopg
import paho.mqtt.client as mqtt

import contract


def log(**kw):
    print(json.dumps(kw, default=str), flush=True)


class Collector:
    def __init__(self, dsn, collection, gap_timeout, max_pending):
        self.conn = psycopg.connect(dsn)
        self.c, self.gap_timeout, self.max_pending = collection, gap_timeout, max_pending
        self.streams = {}            # (acquisition, channel) -> {"rid", "segment", "end", "rate", "start_time"}
        self.pending = {}            # (acquisition, channel) -> {start_sample: (msg, payload, arrived)}
        self.segments = {}           # acquisition -> [{"rid", "start", "end", "rate", "start_time"}] (absolute samples)
        self.ctx_pending = []        # context ahead of the stored samples: (msg, payload, arrived)
        self.ack = None

    def prepare(self, create):
        if self.conn.execute("SELECT to_regclass(%s)", (self.c,)).fetchone()[0] is None:
            if not create:
                raise SystemExit(f"collection {self.c} does not exist (use --create)")
            self.conn.execute("SELECT morlet_create_stream_collection(%s, %s)", tuple(self.c.split(".", 1)))
        if self.conn.execute("SELECT to_regclass(%s)", (self.c + "_ctx",)).fetchone()[0] is None:
            self.conn.execute("SELECT morlet_enable_context(%s::regclass)", (self.c,))
        self.conn.commit()

    def stream(self, p):
        key = (p["acquisition"], p["channel"])
        if key not in self.streams:                                  # resume the newest segment from the database
            row = self.conn.execute(
                f"SELECT recording_id, (metadata->>'segment_start')::bigint, (checkpoint->>'end')::bigint, sample_rate_hz, "
                f"metadata->>'start_time' FROM {self.c} WHERE metadata @> %s::jsonb ORDER BY 2 DESC LIMIT 1",
                (json.dumps({"source": "mqtt", "acquisition": p["acquisition"], "channel": p["channel"]}),)).fetchone()
            self.conn.commit()
            self.streams[key] = None if row is None else {"rid": row[0], "segment": row[1], "start": row[1],
                                                          "end": row[1] + row[2], "rate": row[3], "start_time": row[4]}
            if row is not None:
                self.segments.setdefault(p["acquisition"], []).append(self.streams[key])
        return key

    def open(self, p, segment):
        rid = f"{p['acquisition']}.{p['channel']}.{segment}"
        meta = {"source": "mqtt", "schema": contract.SCHEMA, "acquisition": p["acquisition"], "channel": p["channel"],
                "segment_start": segment, "start_time": p.get("start_time")}
        if p.get("start_time"):
            t0 = datetime.fromisoformat(p["start_time"].replace("Z", "+00:00"))
            meta["start"] = contract.utc(t0.fromtimestamp(t0.timestamp() + segment / p["sample_rate_hz"], t0.tzinfo))
        self.conn.execute("SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,%s,%s)",
                          (self.c, rid, p["sample_rate_hz"], p["channel"], p["unit"], json.dumps(meta)))
        self.conn.commit()
        s = {"rid": rid, "segment": segment, "start": segment, "end": segment, "rate": p["sample_rate_hz"],
             "start_time": p.get("start_time")}
        self.streams[(p["acquisition"], p["channel"])] = s
        self.segments.setdefault(p["acquisition"], []).append(s)
        log(event="recording", recording_id=rid, segment_start=segment)
        return s

    def append(self, s, p):
        try:
            end = self.conn.execute("SELECT morlet_append_chunk(%s::regclass,%s,%s,%s)",
                                    (self.c, s["rid"], p["start_sample"] - s["segment"], p["samples"])).fetchone()[0]
            self.conn.commit()
            return s["segment"] + end
        except psycopg.Error as exc:
            self.conn.rollback()
            log(event="refused", recording_id=s["rid"], start_sample=p["start_sample"], seq=p["seq"],
                reason=exc.diag.message_primary)
            return None

    def samples(self, msg, p):
        key = self.stream(p)
        s = self.streams[key]
        if s is None:
            s = self.open(p, p["start_sample"])
        if p["start_sample"] < s["end"]:                             # duplicate (or older): idempotent append
            if p["start_sample"] >= s["segment"]:
                self.append(s, p)
            self.ack(msg)
            return
        self.pending.setdefault(key, {})[p["start_sample"]] = (msg, p, time.monotonic())
        self.drain(key)

    def drain(self, key):
        waiting, s = self.pending.get(key, {}), self.streams[key]
        while s["end"] in waiting:
            msg, p, _ = waiting.pop(s["end"])
            end = self.append(s, p)
            if end is None:                                          # refused: do not let it block the stream
                self.ack(msg)
                break
            s["end"] = end
            self.ack(msg)

    def check_gaps(self):
        now = time.monotonic()
        for key, waiting in self.pending.items():
            if not waiting:
                continue
            s, first = self.streams[key], min(waiting)
            if len(waiting) > self.max_pending or now - min(a for _, _, a in waiting.values()) > self.gap_timeout:
                log(event="gap", recording_id=s["rid"], missing_from_sample=s["end"], missing_to_sample=first - 1,
                    missing_samples=first - s["end"])
                self.open(waiting[first][1], first)
                self.drain(key)

    def context(self, msg, p):
        self.ctx_pending.append((msg, p, time.monotonic()))
        self.flush_context()

    def flush_context(self):
        """Store context whose time is covered by stored samples; keep the rest (unacknowledged) until it is, or
        until it falls in a gap or waits longer than the gap timeout."""
        keep = []
        for msg, p, arrived in self.ctx_pending:
            verdict = self.place_context(p)
            if verdict == "wait" and time.monotonic() - arrived <= max(self.gap_timeout * 3, 10):
                keep.append((msg, p, arrived))
                continue
            if verdict not in ("stored",):
                log(event="context_not_stored", acquisition=p["acquisition"], key=p["key"], source_time=p["source_time"],
                    reason="timed out waiting for samples" if verdict == "wait" else verdict)
            self.ack(msg)
        self.ctx_pending = keep

    def place_context(self, p):
        segs = [s for s in self.segments.get(p["acquisition"], []) if s.get("start_time")]
        if not segs:
            return "wait"
        t0 = datetime.fromisoformat(segs[0]["start_time"].replace("Z", "+00:00"))
        t = datetime.fromisoformat(p["source_time"].replace("Z", "+00:00"))
        sample = round((t - t0).total_seconds() * segs[0]["rate"])            # absolute sample of the acquisition
        if sample < 0:
            return "before the acquisition"
        seg = next((s for s in segs if s["start"] <= sample < s["end"]), None)
        if seg is None:
            if sample >= max(s["end"] for s in segs):
                return "wait"                                               # samples not stored yet
            return "in a gap between recordings"
        value = {"value": p["value"], "status": p.get("status", "good"), "source_time": p["source_time"]}
        batch = f"mqtt:{p['acquisition']}:{p['key']}:{p['source_time']}"
        try:
            self.conn.execute("SELECT morlet_add_context(%s::regclass,%s,%s::jsonb)", (self.c, batch, json.dumps(
                [{"recording_id": seg["rid"], "key": p["key"], "value": value, "start_sample": sample - seg["start"]}])))
            self.conn.commit()
            return "stored"
        except psycopg.Error as exc:
            self.conn.rollback()
            return "refused: " + (exc.diag.message_primary or "")


def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=1883)
    a.add_argument("--collection", required=True)
    a.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    a.add_argument("--create", action="store_true")
    a.add_argument("--client-id", default="morlet-collector-1")
    a.add_argument("--gap-timeout", type=float, default=5.0)
    a.add_argument("--max-pending", type=int, default=64)
    a.add_argument("--run-seconds", type=float, default=0)
    o = a.parse_args()
    col = Collector(o.dsn, o.collection, o.gap_timeout, o.max_pending)
    col.prepare(o.create)
    inbox = queue.Queue()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=o.client_id, clean_session=False,
                         protocol=mqtt.MQTTv311, manual_ack=True)
    col.ack = lambda msg: client.ack(msg.mid, msg.qos)
    client.on_connect = lambda c, u, f, rc, p: (log(event="connected", reason=str(rc)),
                                                c.subscribe([("morlet/+/+/+/samples", 1), ("morlet/+/+/+/context", 1)]))
    client.on_disconnect = lambda c, u, f, rc, p: log(event="disconnected", reason=str(rc))
    client.on_message = lambda c, u, m: inbox.put(m)
    client.reconnect_delay_set(1, 30)
    client.connect_async(o.host, o.port, keepalive=30)
    client.loop_start()
    stop = time.monotonic() + o.run_seconds if o.run_seconds else None
    try:
        while stop is None or time.monotonic() < stop:
            try:
                msg = inbox.get(timeout=0.2)
            except queue.Empty:
                col.check_gaps()
                col.flush_context()
                continue
            try:
                p = json.loads(msg.payload)
                if msg.topic.endswith("/samples"):
                    col.samples(msg, contract.check_samples(p))
                else:
                    col.context(msg, contract.check_context(p))
            except (ValueError, KeyError, TypeError) as exc:
                log(event="rejected", topic=msg.topic, reason=str(exc))
                col.ack(msg)                                         # malformed: redelivery would not help
            col.check_gaps()
            col.flush_context()
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
