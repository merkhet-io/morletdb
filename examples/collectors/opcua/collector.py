#!/usr/bin/env python3
"""Reference OPC UA collector: a waveform delivered in numbered blocks, plus operating context, into MorletDB.

  python examples/collectors/opcua/collector.py opc.tcp://127.0.0.1:4840/morletdb/ --collection public.machine \
      [--create] [--prefix machine1.vibration] [--run-seconds 60]

It reads the information model of examples/collectors/opcua/server.py (see README.md); other servers need the
same model or an adapted mapping. Credentials: DATABASE_URL; the OPC UA connection uses no security, which is
only for local tests.

Waveform:
- block i holds samples i*BlockSize .. (i+1)*BlockSize-1 of one continuous signal; it is appended to the current
  recording at sample offset (i - first_block) * BlockSize with morlet_append_chunk, one transaction per block;
- the recording id is <prefix>.<first_block>; its metadata records the endpoint, node ids, first block, block
  size and the start time of its first block (the source timestamp of the samples);
- after a restart or a lost connection, the collector resumes from the recording's stored progress;
- a block delivered twice is an identical replay, which MorletDB accepts; a conflicting one is refused;
- blocks the server no longer holds (below OldestBlock) are a gap: it is reported, and a new recording starts at
  the oldest available block; samples are never invented to bridge it.

Context: Speed, Load and State are scalar tags with irregular updates. Each change becomes a context event in the
current recording's sample coordinates: it holds from the first sample at or after its source time
(context_rows.py). The value is the tag's value alone, so the same state in two cycles has the same segment context;
a status other than Good is its own event, "<key>.status", at the same sample. A change without a source timestamp
is not placed (the server time is not a stand-in). They are not resampled into waveforms. The batch id is derived
from the node and the change, so a change delivered twice is stored once.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import psycopg
from asyncua import Client, ua

from context_rows import context_rows, first_sample

NAMESPACE = "urn:morletdb:example-machine"
TAGS = {"Machine.Speed": "speed", "Machine.Load": "load", "Machine.State": "state"}


def log(**kw):
    print(json.dumps(kw, default=str), flush=True)


class Store:
    def __init__(self, dsn, collection, prefix, quantum):
        self.conn = psycopg.connect(dsn)
        self.c, self.prefix, self.quantum = collection, prefix, quantum

    def prepare(self, create):
        if self.conn.execute("SELECT to_regclass(%s)", (self.c,)).fetchone()[0] is None:
            if not create:
                raise SystemExit(f"collection {self.c} does not exist (use --create)")
            schema, name = self.c.split(".", 1)
            self.conn.execute("SELECT morlet_create_stream_collection(%s, %s)", (schema, name))
        if self.conn.execute("SELECT to_regclass(%s)", (self.c + "_ctx",)).fetchone()[0] is None:
            self.conn.execute("SELECT morlet_enable_context(%s::regclass)", (self.c,))
        self.conn.commit()

    def resume(self, endpoint):
        """The newest recording from this endpoint and prefix: (recording_id, first_block, start time, next block)."""
        row = self.conn.execute(
            f"SELECT recording_id, (metadata->>'first_block')::bigint, metadata->>'start', (checkpoint->>'end')::bigint, "
            f"(metadata->>'block_size')::bigint FROM {self.c} WHERE metadata @> %s::jsonb "
            f"ORDER BY (metadata->>'first_block')::bigint DESC LIMIT 1",
            (json.dumps({"source": "opcua", "endpoint": endpoint, "prefix": self.prefix}),)).fetchone()
        self.conn.commit()
        if row is None:
            return None
        rid, first, start, end, bs = row
        return {"rid": rid, "first": first, "start": start, "next": first + end // bs}

    def open(self, endpoint, first, start, rate, unit, block_size):
        rid = f"{self.prefix}.{first}"
        meta = {"source": "opcua", "endpoint": endpoint, "prefix": self.prefix, "namespace": NAMESPACE,
                "node": "Machine.Vibration", "first_block": first, "block_size": block_size,
                "start": start.isoformat().replace("+00:00", "Z")}
        self.conn.execute("SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,%s,%s,512,256,'zscore','morlet-energy-v1',%s)",
                          (self.c, rid, rate, "vibration", unit, json.dumps(meta), self.quantum))
        self.conn.commit()
        return {"rid": rid, "first": first, "start": meta["start"], "next": first}

    def append(self, rec, index, samples, block_size):
        end = self.conn.execute("SELECT morlet_append_chunk(%s::regclass,%s,%s,%s)",
                                (self.c, rec["rid"], (index - rec["first"]) * block_size, list(samples))).fetchone()[0]
        self.conn.commit()
        return end

    def context(self, rec, rate, key, node, dv):
        src = dv.SourceTimestamp      # never the server time in its place: without a source time, nothing is placed
        if src is None:
            return "no source timestamp"
        src = src.replace(tzinfo=timezone.utc) if src.tzinfo is None else src
        start = datetime.fromisoformat(rec["start"].replace("Z", "+00:00"))
        offset = first_sample(src, start, rate)
        if offset < 0:
            return "before the recording"
        value = dv.Value.Value
        status = dv.StatusCode.name if dv.StatusCode else "Good"
        source_time = src.isoformat().replace("+00:00", "Z")
        batch = "opcua:" + hashlib.sha256(json.dumps([rec["rid"], node, source_time, value, status],
                                                     default=str).encode()).hexdigest()[:32]
        self.conn.execute("SELECT morlet_add_context(%s::regclass,%s,%s::jsonb)", (self.c, batch, json.dumps(
            context_rows(rec["rid"], key, value, status, offset), default=str)))
        self.conn.commit()
        return None


class Changes:
    def __init__(self):
        self.queue = asyncio.Queue()

    def datachange_notification(self, node, val, data):
        self.queue.put_nowait((node, data.monitored_item.Value))


async def run(endpoint, store, poll, until):
    async with Client(endpoint, timeout=10) as client:
        ns = await client.get_namespace_index(NAMESPACE)
        node = lambda s: client.get_node(ua.NodeId(s, ns))
        rate = float(await node("Machine.Vibration.SampleRate").read_value())
        block_size = int(await node("Machine.Vibration.BlockSize").read_value())
        unit = await node("Machine.Vibration.Unit").read_value()
        latest_node, oldest_node = node("Machine.Vibration.LatestBlock"), node("Machine.Vibration.OldestBlock")
        vib = node("Machine.Vibration")
        method = ua.NodeId("Machine.Vibration.ReadBlock", ns)
        changes = Changes()
        sub = await client.create_subscription(200, changes)
        tag_nodes = {node(n): (TAGS[n], n) for n in TAGS}
        await sub.subscribe_data_change(list(tag_nodes))
        rec = store.resume(endpoint)
        log(event="connected", endpoint=endpoint, rate=rate, block_size=block_size, resume=rec and rec["rid"])
        while until is None or asyncio.get_running_loop().time() < until:
            oldest = int(await oldest_node.read_value())
            latest = int(await latest_node.read_value())
            if rec is None or rec["next"] > latest + 1:
                first = latest                                     # start with the newest complete block
                samples, start = await vib.call_method(method, ua.Variant(first, ua.VariantType.UInt64))
                rec = store.open(endpoint, first, start.replace(tzinfo=timezone.utc), rate, unit, block_size)
                log(event="recording", recording_id=rec["rid"], first_block=first)
            if rec["next"] < oldest:                              # the server no longer holds the next block
                log(event="gap", recording_id=rec["rid"], missing_from_block=rec["next"], missing_to_block=oldest - 1,
                    missing_samples=(oldest - rec["next"]) * block_size)
                samples, start = await vib.call_method(method, ua.Variant(oldest, ua.VariantType.UInt64))
                rec = store.open(endpoint, oldest, start.replace(tzinfo=timezone.utc), rate, unit, block_size)
                log(event="recording", recording_id=rec["rid"], first_block=oldest)
            while rec["next"] <= latest:
                i = rec["next"]
                samples, start = await vib.call_method(method, ua.Variant(i, ua.VariantType.UInt64))
                store.append(rec, i, samples, block_size)
                rec["next"] = i + 1
            while not changes.queue.empty():
                n, dv = changes.queue.get_nowait()
                key, name = tag_nodes.get(n, (None, None))
                if key:
                    why = store.context(rec, rate, key, name, dv)
                    if why:
                        log(event="context_not_stored", key=key, reason=why)
            await asyncio.sleep(poll)


async def main_async(a):
    store = Store(a.dsn, a.collection, a.prefix, a.sample_quantum)
    store.prepare(a.create)
    loop = asyncio.get_running_loop()
    until = loop.time() + a.run_seconds if a.run_seconds else None
    delay = 1.0
    while until is None or loop.time() < until:
        try:
            await run(a.endpoint, store, a.poll, until)
            return 0
        except (OSError, asyncio.TimeoutError, ua.UaError, ConnectionError) as exc:
            store.conn.rollback()
            log(event="disconnected", error=type(exc).__name__, detail=str(exc)[:200], retry_in_s=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("endpoint")
    p.add_argument("--collection", required=True)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--create", action="store_true")
    p.add_argument("--prefix", default="machine1.vibration", help="recording id prefix")
    p.add_argument("--sample-quantum", type=float, default=1.0, help="storage hint for the sample grid, e.g. 0.001")
    p.add_argument("--poll", type=float, default=0.2, help="seconds between checks for new blocks")
    p.add_argument("--run-seconds", type=float, default=0, help="stop after this long (0: run until stopped)")
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
