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
import psycopg.rows

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


LEDGER = """SELECT l.*, s.asset, s.channel, s.calculation, s.collection, s.recording_id
    FROM morlet_bridge.ledger l JOIN morlet_bridge.sources s USING (source_id)"""


def key(r):
    return (r["asset"], r["channel"], r["calculation"], "demo", r["time_ns"])


def main():
    with psycopg.connect(PG, row_factory=psycopg.rows.dict_row) as conn:
        for _ in range(60):                 # wait until the history is published and handed to Telegraf
            n = conn.execute("SELECT count(*) AS n FROM morlet_bridge.deliveries").fetchone()["n"]
            if n > 1000:
                break
            time.sleep(5)
        # the set to check is fixed now: every row handed to Telegraf so far
        handed = conn.execute(LEDGER + """ JOIN morlet_bridge.deliveries d ON d.ledger_id = l.ledger_id
            JOIN morlet_bridge.sinks k ON k.sink_id = d.sink_id AND k.name = 'telegraf'""").fetchall()
    complete = {*FIELDS, "sample_count", "start_sample", "end_sample", "ledger_id", "end_time_ns", "recording_id",
                "collection"}
    for _ in range(30):                     # until all of them are in InfluxDB with every field (each field is its
        points = influx_points()            # own series there, so a query during a write can see a part of a point)
        if all(set(points.get(key(r), ())) == complete for r in handed):
            break
        time.sleep(2)
    with psycopg.connect(PG, row_factory=psycopg.rows.dict_row) as conn:
        ledger_keys = {key(r) for r in conn.execute(LEDGER).fetchall()}
    missing = sum(key(r) not in points for r in handed)
    extra = sum(k not in ledger_keys for k in points)  # a point no ledger row accounts for
    different = 0
    for r in handed:
        p = points.get(key(r))
        if p is None:
            continue
        want = {**{k: r[k] for k in FIELDS}, "sample_count": r["sample_count"], "start_sample": r["start_sample"],
                "end_sample": r["end_sample"], "ledger_id": r["ledger_id"], "end_time_ns": r["end_time_ns"],
                "recording_id": r["recording_id"], "collection": r["collection"]}
        if set(p) != set(want):
            different += 1
            continue
        for k, v in want.items():
            same = struct.pack("<d", p[k]) == struct.pack("<d", v) if k in FIELDS else p[k] == v
            different += not same
    print(f"rows handed to Telegraf (fixed set): {len(handed)}; InfluxDB points: {len(points)}; missing: {missing}; "
          f"not in any ledger row: {extra}; differing fields or field sets: {different}")

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
    link_ok = link.startswith("${viewer}/?") and all(f"{q}=${{__data.fields.{f}}}" in link for q, f in (
        ("collection", "collection"), ("recording", "recording_id"), ("start", "start_sample"), ("end", "end_sample")))
    ok = (not missing and not extra and not different and len(handed) > 1000 and len(frames) == 2 and link_ok
          and {"rms", "recording_id", "start_sample", "end_sample", "collection"} <= set(names))
    print("data link:", link, "ok" if link_ok else "MALFORMED")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
