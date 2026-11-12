#!/usr/bin/env python3
"""Ship recordings from an edge MorletDB to a central one, chunk by chunk, byte for byte (#420).

  python tools/ingest/morlet_ship.py --source-dsn ... --target-dsn ... --collection public.line3 \\
      [--target-collection public.plant] [--metadata-filter '{"machine": "M1"}'] [--batch 64]

For every matching recording of the source collection: the target recording is created with the source's
parameters (rate, channel, unit, metadata, quantum, window, stride, normalization, extractor) if it does not exist;
then the source's encoded chunks from the target's current end on are appended with morlet_append_encoded_chunk,
which keeps their bytes (the target decodes and checks each one). The transfer resumes where the target ends, so
an interrupted run continues without duplicates; an identical resend is a no-op. At the end the SHA-256 over all
chunk bytes of each recording is compared on both sides. Needs SQL 0.3.9 on both servers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time


def recordings(conn, coll, flt):
    return conn.execute(f"SELECT recording_id, sample_rate_hz, channel, unit, metadata, sample_quantum, checkpoint "
                        f"FROM {coll} WHERE metadata @> %s::jsonb ORDER BY recording_id", (json.dumps(flt),)).fetchall()


def ensure_target(tgt, coll, rec):
    rid, rate, channel, unit, meta, quantum, ckpt = rec
    row = tgt.execute(f"SELECT checkpoint FROM {coll} WHERE recording_id = %s", (rid,)).fetchone()
    if row is not None:
        return int(row[0]["end"])
    extractor = ckpt.get("extractor_version") or "none"
    tgt.execute("SELECT morlet_start_recording(%s::regclass,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (coll, rid, rate, channel, unit, json.dumps(meta), int(ckpt.get("window", 512)),
                 int(ckpt.get("stride", 256)), ckpt.get("normalization", "zscore"), extractor, quantum))
    return 0


def digest(conn, coll, rid):
    h = hashlib.sha256()
    n = 0
    start = 0
    while True:
        rows = conn.execute("SELECT start_sample, sample_count, encoded FROM morlet_encoded_chunks(%s::regclass,%s,%s,1000)",
                            (coll, rid, start)).fetchall()
        for s, c, e in rows:
            h.update(bytes(e))
            n += 1
        if len(rows) < 1000:
            return h.hexdigest(), n
        start = rows[-1][0] + rows[-1][1]


def ship(src, tgt, src_coll, tgt_coll, flt, batch):
    report = []
    for rec in recordings(src, src_coll, flt):
        rid, src_end = rec[0], int(rec[6]["end"])
        t0 = time.time()
        end = ensure_target(tgt, tgt_coll, rec)
        sent = bytes_sent = 0
        while end < src_end:
            rows = src.execute("SELECT start_sample, sample_count, encoded FROM morlet_encoded_chunks(%s::regclass,%s,%s,%s)",
                               (src_coll, rid, end, batch)).fetchall()
            if not rows:
                raise ValueError(f"{rid}: the source has no chunk at {end} (a gap in the source?)")
            with tgt.transaction():
                for s, c, e in rows:
                    end = tgt.execute("SELECT morlet_append_encoded_chunk(%s::regclass,%s,%s,%s)",
                                      (tgt_coll, rid, s, e)).fetchone()[0]
                    sent += 1
                    bytes_sent += len(e)
        a, b = digest(src, src_coll, rid), digest(tgt, tgt_coll, rid)
        report.append({"recording_id": rid, "chunks_sent": sent, "bytes_sent": bytes_sent, "end": end,
                       "chunks": a[1], "sha256": a[0], "identical": a == b, "seconds": round(time.time() - t0, 2)})
        if a != b:
            raise ValueError(f"{rid}: chunk bytes differ after shipping (source {a}, target {b})")
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source-dsn", required=True)
    p.add_argument("--target-dsn", required=True)
    p.add_argument("--collection", required=True)
    p.add_argument("--target-collection")
    p.add_argument("--metadata-filter", default="{}")
    p.add_argument("--batch", type=int, default=64, help="chunks per target transaction (1..1000)")
    a = p.parse_args(argv)
    import psycopg
    try:
        with psycopg.connect(a.source_dsn, autocommit=True) as src, psycopg.connect(a.target_dsn, autocommit=True) as tgt:
            sc = src.execute("SELECT to_regclass(%s)::text", (a.collection,)).fetchone()[0]
            tc = tgt.execute("SELECT to_regclass(%s)::text", (a.target_collection or a.collection,)).fetchone()[0]
            if sc is None or tc is None:
                raise ValueError("source or target collection does not exist (create the target collection first)")
            rep = ship(src, tgt, sc, tc, json.loads(a.metadata_filter), max(1, min(a.batch, 1000)))
        print(json.dumps({"status": "complete", "recordings": rep,
                          "bytes_sent": sum(r["bytes_sent"] for r in rep)}, indent=2))
        return 0
    except (ValueError, OSError) as e:
        print(json.dumps({"status": "failed", "error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
