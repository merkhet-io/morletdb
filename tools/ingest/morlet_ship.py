#!/usr/bin/env python3
"""Ship recordings from an edge MorletDB to a central one, chunk by chunk, byte for byte (#420).

  python tools/ingest/morlet_ship.py --source-dsn ... --target-dsn ... --collection public.line3 \\
      [--target-collection public.plant] [--metadata-filter '{"machine": "M1"}'] [--batch 64]

For every matching recording of the source collection: the target recording is created with the source's
parameters (rate, channel, unit, metadata, quantum, window, stride, normalization, extractor), or, if it exists, must
have exactly these parameters (morlet_ensure_recording; #443). Before anything is written, the target's existing
chunks must equal the source's first chunks byte for byte, on the same chunk boundaries (#444); otherwise the
recording is refused and the target stays unchanged. Then the source's encoded chunks from the target's end up to
the source end read at the start are appended with morlet_append_encoded_chunk, which keeps their bytes (the
target decodes and checks each one). An interrupted run continues without duplicates; an identical resend is a
no-op; a source that keeps recording is shipped up to the end read at the start. At the end the SHA-256 over the
chunk bytes up to that end is compared on both sides. Needs SQL 0.3.9 on both servers.
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


SPEC = ("sample_rate_hz", "channel", "unit", "metadata", "sample_quantum")
CKPT = ("window", "stride", "normalization", "extractor_version")


def ensure_target(tgt, coll, rec):
    """Create the target recording with the source's parameters, or check that an existing one has them; returns
    the target's end. A conflict raises before any sample is written (#443)."""
    rid, rate, channel, unit, meta, quantum, ckpt = rec
    row = tgt.execute(f"SELECT sample_rate_hz, channel, unit, metadata, sample_quantum, checkpoint FROM {coll} "
                      f"WHERE recording_id = %s", (rid,)).fetchone()
    if row is not None:
        want = dict(zip(SPEC, (rate, channel, unit, meta, quantum)))
        have = dict(zip(SPEC, row[:5]))
        diff = [f"{k} {have[k]!r} (source {want[k]!r})" for k in SPEC if have[k] != want[k]]
        diff += [f"{k} {row[5].get(k)!r} (source {ckpt.get(k)!r})" for k in CKPT if row[5].get(k) != ckpt.get(k)]
        if diff:
            raise ValueError(f"{rid}: the target recording exists with other parameters: {'; '.join(diff)}")
    extractor = ckpt.get("extractor_version") or "none"
    tgt.execute("SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)",
                (coll, rid, rate, channel, unit, json.dumps(meta), int(ckpt.get("window", 512)),
                 int(ckpt.get("stride", 256)), ckpt.get("normalization", "zscore"), extractor, quantum))
    return int(tgt.execute(f"SELECT checkpoint->>'end' FROM {coll} WHERE recording_id = %s", (rid,)).fetchone()[0])


def chunks(conn, coll, rid, start=0, upto=None, page=1000):
    """The stored chunks (start, count, bytes) from start on, ending before upto."""
    while upto is None or start < upto:
        rows = conn.execute("SELECT start_sample, sample_count, encoded FROM morlet_encoded_chunks(%s::regclass,%s,%s,%s)",
                            (coll, rid, start, page)).fetchall()
        for s, c, e in rows:
            if upto is not None and s >= upto:
                return
            yield s, c, bytes(e)
        if len(rows) < page:
            return
        start = rows[-1][0] + rows[-1][1]


def same_prefix(src, tgt, src_coll, tgt_coll, rid, end):
    """Whether the target's chunks [0, end) equal the source's, byte for byte and on the same boundaries (#444)."""
    a, b = chunks(src, src_coll, rid, 0, end), chunks(tgt, tgt_coll, rid, 0, end)
    for x, y in zip(a, b):
        if x != y:
            return False
    if next(a, None) is not None or next(b, None) is not None:
        return False                                      # one side has more chunks below end
    return True


def digest(conn, coll, rid, upto):
    """SHA-256 over the chunk bytes of [0, upto), and the number of chunks."""
    h, n = hashlib.sha256(), 0
    for _s, _c, e in chunks(conn, coll, rid, 0, upto):
        h.update(e)
        n += 1
    return h.hexdigest(), n


def ship(src, tgt, src_coll, tgt_coll, flt, batch):
    report = []
    for rec in recordings(src, src_coll, flt):
        rid, src_end = rec[0], int(rec[6]["end"])        # the end shipped to: read once, at the start
        t0 = time.time()
        end = ensure_target(tgt, tgt_coll, rec)
        if end > src_end:
            raise ValueError(f"{rid}: the target holds {end} samples, more than the source's {src_end}")
        if not same_prefix(src, tgt, src_coll, tgt_coll, rid, end):
            raise ValueError(f"{rid}: the target's first {end} samples are not the source's chunks; nothing was written")
        sent = bytes_sent = 0
        while end < src_end:
            rows = src.execute("SELECT start_sample, sample_count, encoded FROM morlet_encoded_chunks(%s::regclass,%s,%s,%s)",
                               (src_coll, rid, end, batch)).fetchall()
            rows = [r for r in rows if r[0] < src_end]      # chunks appended to the source since the start wait
            if not rows:
                raise ValueError(f"{rid}: the source has no chunk at {end} (a gap in the source?)")
            with tgt.transaction():
                for s, c, e in rows:
                    end = tgt.execute("SELECT morlet_append_encoded_chunk(%s::regclass,%s,%s,%s)",
                                      (tgt_coll, rid, s, e)).fetchone()[0]
                    sent += 1
                    bytes_sent += len(e)
        a, b = digest(src, src_coll, rid, end), digest(tgt, tgt_coll, rid, end)
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
