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

Time anchors (#564): a recording's time axis is part of it. The source's active anchors, read at the start, are
shipped with it: added to the target in one transaction before the chunks, under a batch id derived from the
source's time-axis version, so a resend is a no-op. Before anything is written, a target recording whose active
anchors differ from the source's (including anchors the source does not have) is refused, and so is a target
server without time anchors (SQL 0.3.13) for an anchored source. At the end morlet_time_axis_version must be equal
on both sides. Only the current anchors are shipped, not their revision history.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
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


ANCHOR_BATCH = 10000


def has_anchor_support(conn):
    return conn.execute("SELECT to_regproc('public.morlet_time_axis_version') IS NOT NULL").fetchone()[0]


def anchor_table(conn, coll):
    return conn.execute("SELECT to_regclass(%s)::text", (coll + "_time_anchors",)).fetchone()[0]


def active_anchors(conn, coll, rid):
    """The recording's active time anchors (neither retracted nor superseded), by sample, as morlet_add_time_anchors
    rows; [] without an anchor table."""
    table = anchor_table(conn, coll) if has_anchor_support(conn) else None
    if table is None:
        return []
    return [{"recording_id": rid, "sample_offset": int(n), "utc_ns": str(t), "uncertainty_ns": int(u), "source": src,
             "break_before": bool(b)} for n, t, u, src, b in conn.execute(
        f"SELECT x.sample_offset, x.utc_ns, x.uncertainty_ns, x.source, x.break_before FROM {table} x "
        f"WHERE x.recording_id = %s AND NOT x.retracted "
        f"AND NOT EXISTS (SELECT 1 FROM {table} y WHERE y.supersedes = x.anchor_id) ORDER BY x.sample_offset",
        (rid,)).fetchall()]


def time_axis(conn, coll, rid):
    if not has_anchor_support(conn):
        return "start+rate"
    return conn.execute("SELECT morlet_time_axis_version(%s::regclass, %s)", (coll, rid)).fetchone()[0]


def check_anchors(tgt, tgt_coll, rid, anchors, target_exists):
    """Refuse, before anything is written, a target whose anchors would not be the source's (#564)."""
    if anchors and not has_anchor_support(tgt):
        raise ValueError(f"{rid}: the source has time anchors and the target server has none (SQL 0.3.13 needed); "
                         f"nothing was written")
    have = active_anchors(tgt, tgt_coll, rid) if target_exists else []
    if have and have != anchors:
        raise ValueError(f"{rid}: the target's time anchors differ from the source's ({len(have)} there, "
                         f"{len(anchors)} in the source); revise or retract them with morlet_revise_time_anchor "
                         f"on one side first; nothing was written")
    return have


def ship_anchors(tgt, tgt_coll, rid, anchors, version):
    """Add the source's anchors to a target recording that has none, in one transaction; the number written."""
    n = 0
    with tgt.transaction():
        if anchor_table(tgt, tgt_coll) is None:
            tgt.execute("SELECT morlet_enable_time_anchors(%s::regclass)", (tgt_coll,))
        for k in range(0, len(anchors), ANCHOR_BATCH):
            n += tgt.execute("SELECT morlet_add_time_anchors(%s::regclass, %s, %s::jsonb)",
                             (tgt_coll, f"ship:{rid}:{version}:{k // ANCHOR_BATCH}",
                              json.dumps(anchors[k:k + ANCHOR_BATCH]))).fetchone()[0]
    return n


def same_prefix(src, tgt, src_coll, tgt_coll, rid, end):
    """Whether the target's chunks [0, end) equal the source's, byte for byte and on the same boundaries (#444)."""
    a, b = chunks(src, src_coll, rid, 0, end), chunks(tgt, tgt_coll, rid, 0, end)
    missing = object()                    # zip() would drop a's unmatched last chunk when b ends first (#495)
    return all(x == y for x, y in itertools.zip_longest(a, b, fillvalue=missing))


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
        anchors = active_anchors(src, src_coll, rid)      # the time axis shipped: read once, at the start
        version = time_axis(src, src_coll, rid)
        exists = tgt.execute(f"SELECT 1 FROM {tgt_coll} WHERE recording_id = %s", (rid,)).fetchone() is not None
        have = check_anchors(tgt, tgt_coll, rid, anchors, exists)
        end = ensure_target(tgt, tgt_coll, rec)
        if end > src_end:
            raise ValueError(f"{rid}: the target holds {end} samples, more than the source's {src_end}")
        if not same_prefix(src, tgt, src_coll, tgt_coll, rid, end):
            raise ValueError(f"{rid}: the target's first {end} samples are not the source's chunks; nothing was written")
        # the time axis before the samples: an interrupted run never leaves chunks on the wrong axis
        anchors_sent = ship_anchors(tgt, tgt_coll, rid, anchors, version.split(":")[-1]) if anchors and not have else 0
        sent = bytes_sent = 0
        while end < src_end:
            rows = src.execute("SELECT start_sample, sample_count, encoded FROM morlet_encoded_chunks(%s::regclass,%s,%s,%s)",
                               (src_coll, rid, end, batch)).fetchall()
            rows = [r for r in rows if r[0] < src_end]      # chunks appended to the source since the start wait
            at = end
            for s, c, _e in rows:                           # checked before this batch's transaction opens
                if s != at:
                    raise ValueError(f"{rid}: the source has no chunk at {at} (a gap in the source?)")
                at = s + c
            if not rows:
                raise ValueError(f"{rid}: the source has no chunk at {end} (a gap in the source?)")
            with tgt.transaction():
                for s, c, e in rows:
                    end = tgt.execute("SELECT morlet_append_encoded_chunk(%s::regclass,%s,%s,%s)",
                                      (tgt_coll, rid, s, e)).fetchone()[0]
                    sent += 1
                    bytes_sent += len(e)
        a, b = digest(src, src_coll, rid, end), digest(tgt, tgt_coll, rid, end)
        axis_src, axis_tgt = time_axis(src, src_coll, rid), time_axis(tgt, tgt_coll, rid)
        report.append({"recording_id": rid, "chunks_sent": sent, "bytes_sent": bytes_sent, "end": end,
                       "chunks": a[1], "sha256": a[0], "anchors_sent": anchors_sent, "time_axis": axis_src,
                       "identical": a == b and axis_src == axis_tgt, "seconds": round(time.time() - t0, 2)})
        if a != b:
            raise ValueError(f"{rid}: chunk bytes differ after shipping (source {a}, target {b})")
        if axis_src != axis_tgt:
            why = "the source's time anchors changed while shipping; run again" if axis_src != version else \
                "the time axes differ after shipping"
            raise ValueError(f"{rid}: {why} (source {axis_src}, target {axis_tgt})")
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
