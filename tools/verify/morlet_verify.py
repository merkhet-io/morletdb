#!/usr/bin/env python3
"""Integrity scrub of a MorletDB collection (#193): calls morlet_verify in batches and reports what it finds.

Every chunk is decoded (structure and CRC-32C) and checked against its sample count, chunk summaries (where
enabled) are compared with the decoded samples, and chunks must cover each recording without gaps up to its
checkpoint. Nothing is changed: the scrub runs in read-only transactions, one per batch, and can pause between
batches (--sleep) to limit load, or continue after a given recording (--after).

  python3 tools/verify/morlet_verify.py --dsn "$DATABASE_URL" public.signals
  python3 tools/verify/morlet_verify.py --dsn "$DATABASE_URL" public.signals --batch 20 --sleep 0.5 --json

Exit status: 0 when no problem was found, 1 when at least one was, 2 on usage or connection errors.
Run it as a role that sees every row of the collection: rows hidden by row security are reported as gaps.
"""
import argparse
import json
import os
import sys
import time

import psycopg


def scrub(dsn, collection, batch=100, sleep=0.0, after=None, out=print):
    """Yields one dict per recording: recording_id, chunks, samples, problems."""
    with psycopg.connect(dsn) as conn:
        while True:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                rows = conn.execute("SELECT recording_id, chunks, samples, problems FROM morlet_verify(%s::regclass, %s, %s)",
                                    (collection, after, batch)).fetchall()
            for recording_id, chunks, samples, problems in rows:
                yield {"recording_id": recording_id, "chunks": chunks, "samples": samples, "problems": list(problems)}
            if len(rows) < batch:
                return
            after = rows[-1][0]
            if sleep:
                time.sleep(sleep)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("collection", help="stream collection, for example public.signals")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    ap.add_argument("--batch", type=int, default=100, help="recordings per read-only transaction (1..100000)")
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds to pause between batches")
    ap.add_argument("--after", help="continue after this recording_id")
    ap.add_argument("--json", action="store_true", help="one JSON object per recording")
    a = ap.parse_args()
    if not 1 <= a.batch <= 100000 or a.sleep < 0:
        ap.error("--batch must be 1..100000 and --sleep nonnegative")
    recordings = chunks = samples = bad = 0
    try:
        for r in scrub(a.dsn, a.collection, a.batch, a.sleep, a.after):
            recordings += 1
            chunks += r["chunks"]
            samples += r["samples"]
            bad += bool(r["problems"])
            if a.json:
                print(json.dumps(r))
            elif r["problems"]:
                for p in r["problems"]:
                    print(f"{r['recording_id']}: {p}")
    except psycopg.Error as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    summary = {"recordings": recordings, "chunks": chunks, "samples": samples, "recordings_with_problems": bad}
    print(json.dumps({"summary": summary}) if a.json else
          f"{recordings} recordings, {chunks} chunks, {samples} samples checked; {bad} with problems", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
