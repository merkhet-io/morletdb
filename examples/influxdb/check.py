#!/usr/bin/env python3
"""End-to-end check of the InfluxDB example: every metric MorletDB's bridge handed to Telegraf is in InfluxDB with
the same values (bit for bit), timestamp, interval and tags, and Grafana's panel query returns them with the fields
the drill-down link needs. Run on the host while `docker compose up` is running:

  python examples/influxdb/check.py            # needs psycopg
"""
import csv
import io
import json
import struct
import sys
import time
import urllib.request

import psycopg

PG = "host=127.0.0.1 port=54333 dbname=plant user=morlet password=influx-demo-only"
INFLUX = "http://127.0.0.1:8086"
TOKEN = "influx-demo-only-token"
GRAFANA = "http://127.0.0.1:3000"
FIELDS = ("minimum", "maximum", "mean", "rms")


def flux(query):
    req = urllib.request.Request(f"{INFLUX}/api/v2/query?org=plant", data=json.dumps(
        {"query": query, "dialect": {"annotations": ["datatype"], "header": True}}).encode(),
        headers={"Authorization": f"Token {TOKEN}", "Content-Type": "application/json", "Accept": "application/csv"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def iso_ns(s):
    from datetime import datetime, timezone
    head, _, frac = s.rstrip("Z").partition(".")
    d = datetime.fromisoformat(head).replace(tzinfo=timezone.utc)
    return int(d.timestamp()) * 10 ** 9 + int(frac.ljust(9, "0") or 0)


def influx_points():
    text = flux('from(bucket: "machines") |> range(start: 0) |> filter(fn: (r) => r._measurement == "vibration")')
    points, types, header = {}, None, None
    for row in csv.reader(io.StringIO(text)):
        if not row or not any(row):
            types = header = None
            continue
        if row[0] == "#datatype":
            types = row
            continue
        if header is None:
            header = row
            continue
        r = dict(zip(header, row))
        kind = types[header.index("_value")]
        v = float(r["_value"]) if kind == "double" else int(r["_value"]) if kind == "long" else r["_value"]
        key = (r["asset"], r["channel"], r["calculation"], r["site"], iso_ns(r["_time"]))
        points.setdefault(key, {})[r["_field"]] = v
    return points


def main():
    with psycopg.connect(PG) as conn:
        for _ in range(60):                 # wait until the history is published and handed over
            pending = conn.execute("""SELECT (SELECT count(*) FROM morlet_bridge.ledger) -
                (SELECT count(*) FROM morlet_bridge.deliveries d JOIN morlet_bridge.sinks s USING (sink_id)
                 WHERE s.name = 'telegraf')""").fetchone()[0]
            n = conn.execute("SELECT count(*) FROM morlet_bridge.ledger").fetchone()[0]
            if n > 1000 and pending <= 4:
                break
            time.sleep(5)
        time.sleep(12)                      # Telegraf's flush interval, twice
        rows = conn.execute("""SELECT l.*, s.asset, s.channel, s.calculation, s.collection, s.recording_id
            FROM morlet_bridge.ledger l JOIN morlet_bridge.sources s USING (source_id)
            JOIN morlet_bridge.deliveries d ON d.ledger_id = l.ledger_id
            JOIN morlet_bridge.sinks k ON k.sink_id = d.sink_id AND k.name = 'telegraf'""").fetchall()
        cols = [c.name for c in conn.cursor().execute("SELECT l.*, s.asset, s.channel, s.calculation, s.collection, "
               "s.recording_id FROM morlet_bridge.ledger l JOIN morlet_bridge.sources s USING (source_id) LIMIT 0").description]
    ledger = [dict(zip(cols, r)) for r in rows]
    points = influx_points()
    missing, different = 0, 0
    for r in ledger:
        p = points.get((r["asset"], r["channel"], r["calculation"], "demo", r["time_ns"]))
        if p is None:
            missing += 1
            continue
        want = {**{k: r[k] for k in FIELDS}, "sample_count": r["sample_count"], "start_sample": r["start_sample"],
                "end_sample": r["end_sample"], "ledger_id": r["ledger_id"], "end_time_ns": r["end_time_ns"],
                "recording_id": r["recording_id"], "collection": r["collection"]}
        for k, v in want.items():
            got = p.get(k)
            same = struct.pack("<d", got) == struct.pack("<d", v) if k in FIELDS else got == v
            different += not same
    print(f"ledger rows handed to Telegraf: {len(ledger)}; InfluxDB points: {len(points)}; "
          f"missing in InfluxDB: {missing}; differing fields: {different}")

    req = urllib.request.Request(f"{GRAFANA}/api/dashboards/uid/morletdb-influxdb",
                                 headers={"Authorization": "Basic YWRtaW46aW5mbHV4LWRlbW8tb25seQ=="})
    with urllib.request.urlopen(req, timeout=30) as r:
        dash = json.loads(r.read())["dashboard"]
    panel = dash["panels"][0]
    body = {"from": "now-30m", "to": "now", "queries": [{**panel["targets"][0], "datasource": panel["datasource"],
                                                         "intervalMs": 1000, "maxDataPoints": 2000}]}
    req = urllib.request.Request(f"{GRAFANA}/api/ds/query", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Basic YWRtaW46aW5mbHV4LWRlbW8tb25seQ==",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        frames = json.loads(r.read())["results"]["A"]["frames"]
    names = [f["name"] for f in frames[0]["schema"]["fields"]]
    assets = sorted({f["schema"]["fields"][1].get("labels", {}).get("asset") for f in frames})
    print(f"Grafana panel query: {len(frames)} frames ({', '.join(map(str, assets))}), fields {names}")
    link = panel["fieldConfig"]["defaults"]["links"][0]["url"]
    ok = (not missing and not different and len(ledger) > 1000 and len(frames) == 2
          and {"rms", "recording_id", "start_sample", "end_sample", "collection"} <= set(names))
    print("data link:", link)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
