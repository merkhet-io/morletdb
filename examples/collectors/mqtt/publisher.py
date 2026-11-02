#!/usr/bin/env python3
"""Test publisher for morlet-mqtt/1: deterministic sample batches and context, with injected faults.

  python examples/collectors/mqtt/publisher.py --acquisition A1 --batches 40 [--duplicate 5,6] [--alter 7]
      [--swap 10] [--drop 20] [--first-batch 0]

Batch b holds samples b*batch_size .. (b+1)*batch_size-1 of contract.signal. --duplicate sends those batches
twice, --alter sends a second, altered copy, --swap sends batch b+1 before b, --drop never sends b. A context
message (speed, state) follows every 4th batch, stamped with the time of its first sample.
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

import contract


def ints(text):
    return {int(x) for x in text.split(",") if x.strip()} if text else set()


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=1883)
    a.add_argument("--acquisition", default="A1")
    a.add_argument("--topic", default="morlet/site1/press4/vib")
    a.add_argument("--batches", type=int, default=40)
    a.add_argument("--first-batch", type=int, default=0)
    a.add_argument("--batch-size", type=int, default=512)
    a.add_argument("--rate", type=float, default=5120.0)
    a.add_argument("--start-time", default="2026-01-01T00:00:00Z")
    a.add_argument("--duplicate", default="")
    a.add_argument("--alter", default="")
    a.add_argument("--swap", default="")
    a.add_argument("--drop", default="")
    a.add_argument("--interval", type=float, default=0.0, help="seconds between batches")
    o = a.parse_args()
    dup, alter, swap, drop = ints(o.duplicate), ints(o.alter), ints(o.swap), ints(o.drop)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"morlet-test-publisher-{o.acquisition}")
    client.connect(o.host, o.port)
    client.loop_start()
    t0 = datetime.fromisoformat(o.start_time.replace("Z", "+00:00"))

    def batch(b, altered=False):
        start = b * o.batch_size
        x = contract.signal(start, o.batch_size, o.rate).tolist()
        if altered:
            x[0] += 0.001
        return {"schema": contract.SCHEMA, "acquisition": o.acquisition, "channel": "vib", "unit": "g",
                "sample_rate_hz": o.rate, "seq": b, "start_sample": start, "start_time": o.start_time, "samples": x}

    order = []
    b = o.first_batch
    while b < o.first_batch + o.batches:
        if b in swap and b + 1 < o.first_batch + o.batches:
            order += [b + 1, b]
            b += 2
            continue
        order.append(b)
        b += 1
    sent = 0
    for b in order:
        if b in drop:
            continue
        msgs = [batch(b)] + ([batch(b)] if b in dup else []) + ([batch(b, True)] if b in alter else [])
        for m in msgs:
            client.publish(f"{o.topic}/samples", json.dumps(m), qos=1).wait_for_publish()
            sent += 1
        if o.interval:
            time.sleep(o.interval)
        if b % 4 == 0:
            t = t0 + timedelta(seconds=b * o.batch_size / o.rate)
            for key, value in (("speed", 1500 + (b % 7)), ("state", "Running")):
                client.publish(f"{o.topic}/context", json.dumps({"schema": contract.SCHEMA, "acquisition": o.acquisition,
                    "key": key, "value": value, "status": "good", "source_time": contract.utc(t)}), qos=1).wait_for_publish()
    client.loop_stop()
    client.disconnect()
    print(json.dumps({"published_sample_messages": sent, "order": order[:12] + (["..."] if len(order) > 12 else []),
                      "dropped": sorted(drop), "duplicated": sorted(dup), "altered": sorted(alter)}))


if __name__ == "__main__":
    main()
