#!/usr/bin/env python3
"""Compares what the SeedLink collector stored with the data centre's archive (FDSN dataselect), sample for sample.

  export DATABASE_URL=postgresql://...
  python examples/collectors/seedlink/verify_fdsn.py --collection public.live --fdsn GFZ [--sample 20] [--seed 1]

For each checked recording (all, or a seeded random sample) it downloads the recording's time span from the archive
and aligns the archive's samples to the stored ones by sample index (start_ns from the recording's metadata). It
reports, per recording and in total: samples compared, equal, different, and stored samples the archive does not
have. Exit status 1 if any compared
sample differs. The archive can lag the live stream by minutes; recordings that end less than --settle-min ago are
skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

NS = 1_000_000_000


def windows(rows, a, ns):
    """(rid, rate, meta, s0, s1) for seeded random 1-hour windows inside recordings, weighted by length, plus the
    hours named in --at on every channel of one seeded station. Windows may be shorter at a recording's end."""
    import datetime as dt
    def t(iso):
        return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * ns) if iso else None
    lo, hi = t(a.t_from), t(a.t_to)
    spans = []
    for rid, rate, meta, end in rows:
        st = int(meta["start_ns"])
        a0 = 0 if lo is None else max(0, round((lo - st) * rate / ns))
        a1 = end if hi is None else min(end, round((hi - st) * rate / ns))
        if a1 - a0 >= a.min_minutes * 60 * rate:
            spans.append((rid, rate, meta, a0, a1))
    rng = random.Random(a.seed)
    out = []
    total = sum(s[4] - s[3] for s in spans)
    for _ in range(a.windows if total else 0):
        x = rng.uniform(0, total)
        for rid, rate, meta, a0, a1 in spans:
            if x <= a1 - a0:
                h = int(3600 * rate)
                s0 = a0 + int(rng.uniform(0, max(0, a1 - a0 - h)))
                out.append((rid, rate, meta, s0, min(a1, s0 + h)))
                break
            x -= a1 - a0
    if a.at and spans:
        station = rng.choice(sorted({s[2]["network"] + "." + s[2]["station"] for s in spans}))
        for iso in a.at:
            when = t(iso)
            for rid, rate, meta, a0, a1 in spans:
                if meta["network"] + "." + meta["station"] != station:
                    continue
                st = int(meta["start_ns"])
                s0 = max(a0, round((when - st) * rate / ns))
                s1 = min(a1, s0 + int(3600 * rate))
                if s1 > s0:
                    out.append((rid, rate, meta, s0, s1))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="public.live")
    ap.add_argument("--fdsn", default="GFZ", help="ObsPy FDSN client name or URL")
    ap.add_argument("--sample", type=int, default=0, help="check this many random recordings (0: all)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--settle-min", type=float, default=30.0)
    ap.add_argument("--max-hours", type=float, default=2.0, help="check at most this much of each recording")
    ap.add_argument("--windows", type=int, default=0,
                    help="instead of whole recordings: this many random 1-hour windows (seeded), weighted by length")
    ap.add_argument("--at", nargs="*", default=[],
                    help="with --windows: also the hour starting at each of these UTC times (ISO), on every channel "
                         "of one seeded station")
    ap.add_argument("--min-minutes", type=float, default=10.0, help="with --windows: skip shorter recording spans")
    ap.add_argument("--from", dest="t_from", help="with --windows: only windows after this UTC time (ISO)")
    ap.add_argument("--to", dest="t_to", help="with --windows: only windows before this UTC time (ISO)")
    ap.add_argument("--out", help="write the report as JSON")
    a = ap.parse_args(argv)
    import numpy as np
    import psycopg
    from obspy import UTCDateTime
    from obspy.clients.fdsn import Client

    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    rows = conn.execute(f"SELECT recording_id, sample_rate_hz, metadata, (checkpoint->>'end')::bigint "
                        f"FROM {a.collection} ORDER BY recording_id").fetchall()
    now_ns = time.time_ns()
    rows = [r for r in rows if r[3] and int(r[2]["start_ns"]) + r[3] / r[1] * NS < now_ns - a.settle_min * 60 * NS]
    if a.sample:
        rows = random.Random(a.seed).sample(rows, min(a.sample, len(rows)))
    jobs = [(rid, rate, meta, 0, int(min(end, a.max_hours * 3600 * rate))) for rid, rate, meta, end in rows]
    if a.windows:
        jobs = windows(rows, a, NS)
    client = Client(a.fdsn)
    total = dict(recordings=0, compared=0, equal=0, different=0, not_in_archive=0, errors=0)
    report = []
    for rid, rate, meta, s0, s1 in jobs:
        n = s1 - s0
        start_ns = int(meta["start_ns"]) + round(s0 * NS / rate)
        t0 = UTCDateTime(ns=start_ns)
        raw = conn.execute("SELECT morlet_read_interval_raw(%s::regclass,%s,%s,%s)",
                           (a.collection, rid, s0, s1)).fetchone()[0]
        stored = np.frombuffer(raw, dtype="<f8")
        r = dict(recording=rid, first_sample=s0, samples=n, start=str(t0))
        try:
            st = client.get_waveforms(meta["network"], meta["station"], meta["location"] or "--", meta["channel"],
                                      t0 - 1, t0 + n / rate + 1)
        except Exception as exc:  # noqa: BLE001
            r["error"] = f"{type(exc).__name__}: {exc}"
            total["errors"] += 1
            report.append(r)
            continue
        archive = np.full(n, np.nan)
        for tr in st:
            if abs(tr.stats.sampling_rate - rate) > 1e-9:
                continue
            off = round((tr.stats.starttime.ns - start_ns) * rate / NS)
            lo, hi = max(off, 0), min(off + tr.stats.npts, n)
            if lo < hi:
                archive[lo:hi] = tr.data[lo - off:hi - off]
        have = ~np.isnan(archive)
        cmp_ = int(have.sum())
        eq = int((archive[have] == stored[have]).sum())
        r.update(compared=cmp_, equal=eq, different=cmp_ - eq, not_in_archive=int(n - cmp_))
        total["recordings"] += 1
        for k in ("compared", "equal", "different", "not_in_archive"):
            total[k] += r[k]
        report.append(r)
        print(json.dumps(r), flush=True)
    print(json.dumps({"total": total}))
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"total": total, "recordings": report}, f, indent=1)
    return 1 if total["different"] else 0


if __name__ == "__main__":
    sys.exit(main())
