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
shipped with it, in one transaction before the chunks: added when the target recording has none, or replacing the
anchors an earlier run shipped when the source's changed since (a revision at the edge). Before anything is
written, a target recording with anchors of its own that differ from the source's is refused, and so is a target
server without time anchors (SQL 0.3.13) for an anchored source. The target's time axis must equal the source's
before the first chunk is sent, and morlet_time_axis_version is compared again at the end. Only the current
anchors are shipped, not their revision history.

Interpretation revisions (#600, #609): a recording with a corrected unit or calibration cannot be shipped with its
history yet, so a selection that includes one is refused before anything is written, as is a source whose history
this role cannot read.
"""
from __future__ import annotations

import argparse
import contextlib
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
    """The collection's time-anchor table as a quoted name, or None (from the catalog, so quoted or mixed-case
    collection names work)."""
    return conn.execute("SELECT to_regclass(format('%%I.%%I', n.nspname, c.relname || '_time_anchors'))::text "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.oid = %s::regclass",
                        (coll,)).fetchone()[0]


def active_anchors(conn, coll, rid):
    """(the recording's active time anchors by sample, as morlet_add_time_anchors rows with their batch_id and
    anchor_id, its morlet_time_axis_version), read in one statement, so from one snapshot: ([], "start+rate") without
    anchors."""
    table = anchor_table(conn, coll) if has_anchor_support(conn) else None
    if table is None:
        return [], "start+rate"
    version, rows = conn.execute(
        f"SELECT morlet_time_axis_version(%s::regclass, %s), (SELECT coalesce(jsonb_agg(jsonb_build_array("
        f"x.sample_offset, x.utc_ns::text, x.uncertainty_ns, x.source, x.break_before, x.batch_id, x.anchor_id) "
        f"ORDER BY x.sample_offset), '[]') FROM {table} x WHERE x.recording_id = %s AND NOT x.retracted "
        f"AND NOT EXISTS (SELECT 1 FROM {table} y WHERE y.supersedes = x.anchor_id))", (coll, rid, rid)).fetchone()
    return [{"recording_id": rid, "sample_offset": int(n), "utc_ns": t, "uncertainty_ns": int(u), "source": src,
             "break_before": bool(br), "batch_id": batch, "anchor_id": aid}
            for n, t, u, src, br, batch, aid in rows], version


def axis_rows(anchors):
    """The anchors as morlet_add_time_anchors rows, without where they are stored (batch_id, anchor_id)."""
    return [{k: v for k, v in a.items() if k not in ("batch_id", "anchor_id")} for a in anchors]


def time_axis(conn, coll, rid):
    return active_anchors(conn, coll, rid)[1] if has_anchor_support(conn) else "start+rate"


def plan_anchors(tgt, tgt_coll, rid, anchors, target_exists):
    """What the target's anchors need, decided before anything is written (#564): "same" (nothing), "add" (the
    target has none), "replace" (every active target anchor was written by the shipper, and the source's changed
    since: they are retracted and the source's added). Anchors the target got otherwise, and that differ from the
    source's, are refused."""
    if anchors and not has_anchor_support(tgt):
        raise ValueError(f"{rid}: the source has time anchors and the target server has none (SQL 0.3.13 needed); "
                         f"nothing was written")
    have = active_anchors(tgt, tgt_coll, rid)[0] if target_exists else []
    if axis_rows(have) == axis_rows(anchors):
        return "same", have
    if not have:
        return "add", have
    if shipped_by_us(tgt, tgt_coll, rid, have):
        return "replace", have
    raise ValueError(f"{rid}: the target has time anchors of its own that differ from the source's ({len(have)} "
                     f"there, {len(anchors)} in the source); revise or retract them with morlet_revise_time_anchor "
                     f"first; nothing was written")


def shipped_by_us(tgt, tgt_coll, rid, have):
    """Whether the target's active anchors are exactly one earlier shipment, unchanged: all in batches of one
    shipment (ship:<rid>:<md5>-<n>:<k>) and the target's time-axis version equal to the <md5> that shipment records.
    The name alone proves nothing, since anyone may choose a batch id; a set that hashes to the recorded version
    is a copy of a source state, so replacing it loses nothing written on the target (review of #564)."""
    prefix = f"ship:{rid}:"
    shipments = set()
    for a in have:
        if not a["batch_id"].startswith(prefix):
            return False
        shipment, _, k = a["batch_id"][len(prefix):].rpartition(":")
        if not k.isdigit() or "-" not in shipment:
            return False
        shipments.add(shipment)
    if len(shipments) != 1:
        return False
    md5 = shipments.pop().rpartition("-")[0]
    return time_axis(tgt, tgt_coll, rid) == f"anchors:{md5}"


@contextlib.contextmanager
def anchor_lock(tgt, tgt_coll, rid):
    """Hold the target's time-anchor lock of the recording for the whole shipment (#564 review): a session-level
    advisory lock on the key morlet_lock_time_anchors takes per transaction, so other sessions' anchor additions
    and revisions of this recording wait until the chunks are on the axis that was checked; the shipper's own
    anchor writes take the same lock in its session and pass. Without anchor support there is nothing to lock."""
    key = "SELECT hashtextextended('morlet-anchor-rec/' || %s::regclass::oid || '/' || %s, 0)"
    if not has_anchor_support(tgt):
        yield
        return
    k = tgt.execute(key, (tgt_coll, rid)).fetchone()[0]
    tgt.execute("SELECT pg_advisory_lock(%s)", (k,))
    try:
        yield
    finally:
        tgt.execute("SELECT pg_advisory_unlock(%s)", (k,))


def ship_anchors(tgt, tgt_coll, rid, anchors, version, have):
    """Retract the target's shipped anchors (have) and add the source's, in one transaction; the number written.
    The batch id names the source's time-axis version and how many times this recording's anchors were shipped,
    so a resend after an interruption repeats an identical batch, and anchors shipped again after a retraction or
    a source revision get a new one."""
    n = 0
    with tgt.transaction():
        if anchor_table(tgt, tgt_coll) is None:
            tgt.execute("SELECT morlet_enable_time_anchors(%s::regclass)", (tgt_coll,))
        for a in have:
            tgt.execute("SELECT morlet_revise_time_anchor(%s::regclass, %s, retract => true)", (tgt_coll, a["anchor_id"]))
        table = anchor_table(tgt, tgt_coll)
        # distinct shipments: batch ids without their trailing :<k> (a recording id may itself contain colons)
        shipped = tgt.execute(f"SELECT count(DISTINCT regexp_replace(batch_id, ':[0-9]+$', '')) FROM {table} "
                              f"WHERE recording_id = %s AND starts_with(batch_id, %s)",
                              (rid, f"ship:{rid}:")).fetchone()[0]
        rows = axis_rows(anchors)
        for k in range(0, len(rows), ANCHOR_BATCH):
            n += tgt.execute("SELECT morlet_add_time_anchors(%s::regclass, %s, %s::jsonb)",
                             (tgt_coll, f"ship:{rid}:{version}-{shipped}:{k // ANCHOR_BATCH}",
                              json.dumps(rows[k:k + ANCHOR_BATCH]))).fetchone()[0]
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


def refuse_revised(conn, collection, ids):
    """Refuse recordings of `collection` among `ids` that have interpretation revisions (#600): a corrected unit or
    calibration. A copy would carry the corrected values without their history, so they are refused until copies
    can carry it (#609). A history the caller cannot read is refused too: whether a recording was corrected is
    then unknown. MorletDB's own row policy shows a recording's whole history to anyone who sees the
    recording; a history table with any other policy is refused, since rows could be hidden."""
    hist, readable = conn.execute(
        "SELECT h::text, h IS NOT NULL AND has_table_privilege(h,'SELECT') AND NOT EXISTS(SELECT 1 FROM pg_policy p "
        "WHERE p.polrelid=h AND p.polname<>'morlet_follows_recording') FROM (SELECT to_regclass(format('%%I.%%I',"
        "n.nspname,c.relname||'_interpretation')) h FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.oid=%s::regclass) q", (collection,)).fetchone()
    if hist is None or not ids:
        return
    if not readable:
        raise ValueError(f"cannot read {hist}: whether these recordings have a corrected unit or calibration is "
                         "unknown, so nothing is shipped (#609)")
    revised = [r[0] for r in conn.execute(f"SELECT DISTINCT recording_id FROM {hist} WHERE recording_id = ANY(%s) "
                                          "ORDER BY 1", (list(ids),)).fetchall()]
    if revised:
        raise ValueError(f"{', '.join(revised)}: corrected unit or calibration (interpretation revisions); a copy "
                         "cannot carry that history yet, so nothing is shipped (#609)")


def ship(src, tgt, src_coll, tgt_coll, flt, batch):
    report = []
    recs = recordings(src, src_coll, flt)
    refuse_revised(src, src_coll, [r[0] for r in recs])    # before anything is written to the target
    for rec in recs:
        with anchor_lock(tgt, tgt_coll, rec[0]):
            report.append(ship_recording(src, tgt, src_coll, tgt_coll, rec, batch))
    return report


def ship_recording(src, tgt, src_coll, tgt_coll, rec, batch):
    """Ship one recording (see the module docstring); its report entry."""
    rid, src_end = rec[0], int(rec[6]["end"])        # the end shipped to: read once, at the start
    t0 = time.time()
    anchors, version = active_anchors(src, src_coll, rid)   # the time axis shipped: read once, at the start
    exists = tgt.execute(f"SELECT 1 FROM {tgt_coll} WHERE recording_id = %s", (rid,)).fetchone() is not None
    action, have = plan_anchors(tgt, tgt_coll, rid, anchors, exists)
    end = ensure_target(tgt, tgt_coll, rec)
    if end > src_end:
        raise ValueError(f"{rid}: the target holds {end} samples, more than the source's {src_end}")
    if not same_prefix(src, tgt, src_coll, tgt_coll, rid, end):
        raise ValueError(f"{rid}: the target's first {end} samples are not the source's chunks; nothing was written")
    # the time axis before the samples: an interrupted run never leaves chunks on the wrong axis
    anchors_sent = 0 if action == "same" else ship_anchors(tgt, tgt_coll, rid, anchors, version.split(":")[-1], have)
    if time_axis(tgt, tgt_coll, rid) != version:
        raise ValueError(f"{rid}: the target's time axis is not the source's after shipping its anchors "
                         f"(source {version}, target {time_axis(tgt, tgt_coll, rid)}); no chunk was written")
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
    if a != b:
        raise ValueError(f"{rid}: chunk bytes differ after shipping (source {a}, target {b})")
    if axis_src != axis_tgt:
        why = "the source's time anchors changed while shipping; run again" if axis_src != version else \
            "the time axes differ after shipping"
        raise ValueError(f"{rid}: {why} (source {axis_src}, target {axis_tgt})")
    return {"recording_id": rid, "chunks_sent": sent, "bytes_sent": bytes_sent, "end": end,
            "chunks": a[1], "sha256": a[0], "anchors_sent": anchors_sent, "time_axis": axis_src,
            "identical": True, "seconds": round(time.time() - t0, 2)}


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
