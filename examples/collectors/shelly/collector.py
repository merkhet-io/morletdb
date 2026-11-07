#!/usr/bin/env python3
"""Shelly smart-plug collector (Gen2+ local RPC, for example a Shelly Plug S Gen3): polls the plug once per interval
and stores power, voltage, current, frequency and the energy counter in MorletDB, losslessly and without inventing
samples. Written for the home study D1 (evaluation/home_d1); works for any plug with Switch.GetStatus.

  export DATABASE_URL=postgresql://...            # a role that may append to the collection
  export SHELLY_PASSWORD=...                       # only if the plug has authentication on; never on the command line
  python examples/collectors/shelly/collector.py --plug 192.168.2.50 --device washer --collection public.home --create

How it stores the data:
- **Sample clock:** poll k of a segment is due at segment start + k·interval; a poll that fails or comes back late
  ends the segment, and the next answer starts a new one. No value is interpolated or repeated, so every stored sample
  is a real reading, and a gap is a gap.
- **Segments:** one segment is one synchronous acquisition `<device>.<start>` (#292) whose channels are recordings
  `<device>.<channel>.<start>`, all with the same sample instants. Their exact cross statistics
  (morlet_acquisition_cross_stats) give, for example, mean(voltage x current) beside the plug's own power. Segments
  also end at UTC midnight, so a day is a set of recordings.
- **Durability:** every reading is appended to a spool file (one per segment) before anything else; every flush_s the
  buffered readings are stored in one transaction (morlet_append_acquisition_raw). After a crash or power cut the
  collector replays the spool: already stored samples are identical replays, so nothing is stored twice or lost
  beyond the last reading written to the spool.
- **Gaps:** every gap (start, end, cause) is logged as one JSON line in gaps.jsonl, the evidence for D1's hypothesis H1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CHANNELS = (("power", "W", "apower"), ("voltage", "V", "voltage"), ("current", "A", "current"),
            ("frequency", "Hz", "freq"), ("energy", "Wh", "aenergy.total"))


def reading(status: dict) -> dict[str, float]:
    """The channel values of one Switch.GetStatus answer; channels the plug does not report are left out."""
    out = {}
    for name, _, path in CHANNELS:
        v = status
        for part in path.split("."):
            v = v.get(part) if isinstance(v, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[name] = float(v)
    return out


@dataclass
class Segment:
    start: dt.datetime                  # time of sample 0 (UTC)
    channels: tuple[str, ...]
    n: int = 0                          # readings in the segment
    stored: int = 0                     # readings committed to the database
    buffer: dict[str, list[float]] = field(default_factory=dict)

    @property
    def ident(self) -> str:
        return self.start.strftime("%Y%m%dT%H%M%S")


class Segmenter:
    """Pure segmentation: readings at times t (seconds, UTC) on a fixed interval, with a tolerance for poll jitter."""

    def __init__(self, interval: float, tolerance: float | None = None):
        self.interval = interval
        self.tolerance = interval / 2 if tolerance is None else tolerance
        self.seg: Segment | None = None
        self.t0 = 0.0

    def due(self, now: float) -> float:
        """When the next poll is due."""
        if self.seg is None:
            return now
        return self.t0 + self.seg.n * self.interval

    def feed(self, t: float, values: dict[str, float] | None) -> list[tuple]:
        """Events: ('end', segment, cause), ('start', segment), ('sample', segment, values)."""
        ev: list[tuple] = []
        seg = self.seg
        if seg is not None:
            expected = self.t0 + seg.n * self.interval
            midnight = dt.datetime.fromtimestamp(t, dt.timezone.utc).date() != seg.start.date()
            if values is None or abs(t - expected) > self.tolerance or tuple(sorted(values)) != seg.channels or midnight:
                cause = ("no answer" if values is None else "midnight" if midnight
                         else "channels changed" if tuple(sorted(values)) != seg.channels else "late answer")
                ev.append(("end", seg, cause))
                self.seg = seg = None
        if values is None:
            return ev
        if seg is None:   # the segment's sample clock starts at its first reading
            self.seg = seg = Segment(dt.datetime.fromtimestamp(t, dt.timezone.utc), tuple(sorted(values)))
            self.t0 = t
            ev.append(("start", seg))
        for c in seg.channels:
            seg.buffer.setdefault(c, []).append(values[c])
        seg.n += 1
        ev.append(("sample", seg, values))
        return ev


class Store:
    """MorletDB side: one synchronous acquisition per segment, group appends of the buffered readings."""

    def __init__(self, dsn: str, collection: str, device: str, interval: float, create: bool):
        import psycopg
        self.psycopg = psycopg
        self.conn = psycopg.connect(dsn, autocommit=True, application_name="morlet-shelly")
        self.c, self.device, self.rate = collection, device, 1.0 / interval
        if create:
            schema, _, name = collection.rpartition(".")
            with self.conn.transaction():
                if self.conn.execute("SELECT to_regclass(%s)", (collection,)).fetchone()[0] is None:
                    self.conn.execute("SELECT morlet_create_stream_collection(%s,%s)", (schema or "public", name))
                    self.conn.execute("SELECT morlet_enable_envelopes(%s::regclass)", (collection,))
                    self.conn.execute("SELECT morlet_enable_rollups(%s::regclass)", (collection,))
                    self.conn.execute("SELECT morlet_enable_acquisitions(%s::regclass)", (collection,))
                self.conn.execute("SELECT morlet_enable_sync(%s::regclass)", (collection,))

    def rid(self, seg: Segment, channel: str) -> str:
        return f"{self.device}.{channel}.{seg.ident}"

    def acq(self, seg: Segment) -> str:
        return f"{self.device}.{seg.ident}"

    def begin(self, seg: Segment) -> None:
        """Recordings, acquisition and its declaration; idempotent, so a replay after a crash finds them."""
        meta = {"device": self.device, "start_ns": str(round(seg.start.timestamp() * 1_000_000) * 1000),
                "source": "shelly Switch.GetStatus"}
        units = {n: u for n, u, _ in CHANNELS}
        with self.conn.transaction():
            for ch in seg.channels:
                self.conn.execute("SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,%s,%s::jsonb,512,256,"
                                  "'zscore','none')", (self.c, self.rid(seg, ch), self.rate, ch, units[ch],
                                                       json.dumps(meta)))
            self.conn.execute("SELECT morlet_create_acquisition(%s::regclass,%s,%s,%s)",
                              (self.c, self.acq(seg), self.device, seg.start))
            for ch in seg.channels:
                self.conn.execute("SELECT morlet_add_acquisition_channel(%s::regclass,%s,%s,%s)",
                                  (self.c, self.acq(seg), self.rid(seg, ch), ch))
            if not self.conn.execute(f"SELECT sync_roles IS NOT NULL FROM {self.c}_acq WHERE acquisition_id=%s",
                                     (self.acq(seg),)).fetchone()[0]:
                self.conn.execute("SELECT morlet_declare_sync(%s::regclass,%s,%s)", (self.c, self.acq(seg), self.rate))

    def stored_end(self, seg: Segment) -> int:
        """How many readings of the segment are already stored (the group's common end)."""
        r = self.conn.execute(f"SELECT min((checkpoint->>'end')::bigint) FROM {self.c} WHERE recording_id=ANY(%s)",
                              ([self.rid(seg, ch) for ch in seg.channels],)).fetchone()[0]
        return int(r or 0)

    def flush(self, seg: Segment) -> None:
        """Stores the buffered readings of every channel in one transaction (binary parameters: %b)."""
        if not seg.buffer or not seg.buffer[seg.channels[0]]:
            return
        n = len(seg.buffer[seg.channels[0]])
        parts = [struct.pack(f"<{n}d", *seg.buffer[ch]) for ch in seg.channels]
        self.conn.execute("SELECT morlet_append_acquisition_raw(%s::regclass,%s,%s,%s,%b)",
                          (self.c, self.acq(seg), seg.stored, list(seg.channels), parts))
        seg.stored += n
        seg.buffer = {}


def get_status(plug: str, timeout: float) -> dict:
    url = f"http://{plug}/rpc/Switch.GetStatus?id=0"
    password = os.environ.get("SHELLY_PASSWORD")
    if password:   # Gen2+ uses HTTP digest authentication with the user "admin"
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, url, "admin", password)
        opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))
    else:
        opener = urllib.request.build_opener()
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read())


def run(a, store: Store | None, clock=time.time, sleep=time.sleep, polls: int | None = None) -> None:
    spool_dir, seg_ = Path(a.spool), Segmenter(a.interval)
    spool_dir.mkdir(parents=True, exist_ok=True)
    gaps = open(spool_dir / "gaps.jsonl", "a")
    replay(spool_dir, seg_, store, gaps)
    spool = None
    last_flush = clock()
    done = 0
    while polls is None or done < polls:
        due = seg_.due(clock())
        if due > clock():
            sleep(due - clock())
        t = clock()
        try:
            values = reading(get_status(a.plug, timeout=min(2.0, a.interval * 0.8)))
        except (OSError, ValueError, urllib.error.URLError):
            values = None
        done += 1
        if values is None:
            sleep(a.interval)          # an unreachable plug is retried once per interval, not in a tight loop
        for e in seg_.feed(t, values):
            if e[0] == "end":
                if store:
                    store.flush(e[1])
                gaps.write(json.dumps({"after_segment": e[1].ident, "readings": e[1].n, "at": t, "cause": e[2]}) + "\n")
                gaps.flush()
                if spool:
                    spool.close()
                    os.replace(spool.name, spool.name + ".done")
                spool = None
            elif e[0] == "start":
                spool = open(spool_dir / f"{e[1].ident}.jsonl", "a")
                if store:
                    store.begin(e[1])
            else:
                spool.write(json.dumps({"t": t, "v": e[2]}) + "\n")
                spool.flush()
                os.fsync(spool.fileno())
        if store and seg_.seg is not None and clock() - last_flush >= a.flush_s:
            store.flush(seg_.seg)
            last_flush = clock()


def replay(spool_dir: Path, seg_: Segmenter, store: Store | None, gaps) -> None:
    """Stores what an earlier run spooled but may not have committed: the same segment, identical replays."""
    for f in sorted(spool_dir.glob("*.jsonl")):
        if f.name == "gaps.jsonl":
            continue
        lines = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
        if not lines:
            f.unlink()
            continue
        s = Segmenter(seg_.interval)
        for ln in lines:
            for e in s.feed(ln["t"], ln["v"]):
                if e[0] == "start" and store:
                    store.begin(e[1])
        if s.seg and store:
            # append only what is not stored yet, so chunk boundaries stay those of the original flushes
            end = store.stored_end(s.seg)
            s.seg.buffer = {ch: v[end:] for ch, v in s.seg.buffer.items()}
            s.seg.stored = end
            store.flush(s.seg)
        gaps.write(json.dumps({"after_segment": s.seg.ident if s.seg else f.stem, "cause": "collector restart"}) + "\n")
        gaps.flush()
        os.replace(f, str(f) + ".done")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plug", required=True, help="IP address or host name of the plug")
    ap.add_argument("--device", required=True, help="name for the appliance, used in recording ids")
    ap.add_argument("--collection", default="public.home")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--flush-s", dest="flush_s", type=float, default=60.0)
    ap.add_argument("--spool", default=os.path.expanduser("~/.morlet-shelly"))
    ap.add_argument("--create", action="store_true", help="create and configure the collection if missing")
    a = ap.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("set DATABASE_URL", file=sys.stderr)
        return 2
    run(a, Store(dsn, a.collection, a.device, a.interval, a.create))
    return 0


if __name__ == "__main__":
    sys.exit(main())
