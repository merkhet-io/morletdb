#!/usr/bin/env python3
"""SeedLink collector: stores live waveforms from a public SeedLink server (for example GEOFON, geofon.gfz.de:18000)
in MorletDB, losslessly and without inventing samples. Written for the soak test (#191, evaluation/soak_s1); works for
any SeedLink server and stream selection.

  export DATABASE_URL=postgresql://...            # a role that may append to the collection
  python examples/collectors/seedlink/collector.py --server geofon.gfz.de:18000 \
      --streams GE.WLF..HH? CX.PB01..HH? --collection public.live --create

How it stores the data:
- **Segments:** a stream's packets continue one recording while each starts where the previous one ended (within half
  a sample). A gap, a rate change, a packet that disagrees with samples already received, or a new UTC day ends the
  segment; the next packet starts a recording `<net>.<sta>.<loc>.<cha>.<start>`. A packet that repeats samples
  already received (SeedLink resends after a reconnect) is checked against them and only its new samples are kept.
  No value is interpolated, so every stored sample is one the server sent, and a gap is a gap.
- **Durability:** every packet is written to an hourly spool file, with its recording id and sample offset, before
  it is buffered; the spool is fsynced every second. Each stream's buffer is stored when it reaches --chunk samples
  or --flush-s seconds (morlet_append_chunks_raw). After a crash or power cut the collector replays the spool and
  appends what the database does not have yet: nothing is stored twice, and at most the last second of received
  packets can be lost. A spool file is deleted once every sample in it is stored.
- **Evidence:** gaps.jsonl has one line per segment end (stream, cause, sample count) and per reconnect; status.json
  is rewritten every minute (packets, samples, stored, backlog, reconnects, last packet per stream).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import queue
import signal
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

NS = 1_000_000_000


@dataclass
class Packet:
    sid: str            # NET.STA.LOC.CHA
    start_ns: int       # time of the first sample, UTC nanoseconds (miniSEED header)
    rate: float         # samples per second
    data: list[int]     # integer counts


@dataclass
class Segment:
    sid: str
    start_ns: int
    rate: float
    n: int = 0                                  # samples received into this segment
    stored: int = 0                             # samples committed to the database
    tail: list[int] = field(default_factory=list)  # last samples received, to check resent packets
    buffer: list[int] = field(default_factory=list)
    buffer_since: float = 0.0

    @property
    def rid(self) -> str:
        return f"{self.sid}.{dt.datetime.fromtimestamp(self.start_ns / NS, dt.timezone.utc):%Y%m%dT%H%M%S}"

    def end_ns(self) -> int:
        return self.start_ns + round(self.n * NS / self.rate)


TAIL = 4096


class Segmenter:
    """Pure segmentation of one server's packets, per stream."""

    def __init__(self):
        self.segs: dict[str, Segment] = {}

    def feed(self, p: Packet) -> list[tuple]:
        """Events: ('end', seg, cause), ('start', seg), ('samples', seg, offset, values)."""
        ev: list[tuple] = []
        seg = self.segs.get(p.sid)
        data = list(p.data)
        start = p.start_ns
        if seg is not None:
            period = NS / seg.rate
            delta = start - seg.end_ns()
            new_day = (dt.datetime.fromtimestamp(start / NS, dt.timezone.utc).date()
                       != dt.datetime.fromtimestamp(seg.start_ns / NS, dt.timezone.utc).date())
            cause = None
            if p.rate != seg.rate:
                cause = "rate changed"
            elif delta > period / 2:
                cause = "gap"
            elif delta < -period / 2:
                k = round(-delta / period)                  # samples this packet repeats
                if k > len(seg.tail):
                    cause = "overlap beyond the checked tail"
                elif data[:k] != seg.tail[len(seg.tail) - k:][:len(data)]:
                    cause = "resent samples differ"
                elif k >= len(data):
                    return ev                               # an identical duplicate: nothing new
                else:
                    data, start = data[k:], seg.end_ns()
            if cause is None and new_day:
                cause = "new UTC day"
            if cause:
                ev.append(("end", seg, cause))
                seg = None
        if seg is None:
            seg = Segment(p.sid, start, p.rate)
            self.segs[p.sid] = seg
            ev.append(("start", seg))
        ev.append(("samples", seg, seg.n, data))
        seg.n += len(data)
        seg.tail = (seg.tail + data)[-TAIL:]
        return ev


# Spool records: u32 header length, JSON header {rid, sid, rate, start_ns, off, n}, n little-endian int32.
def spool_record(seg: Segment, off: int, values: list[int]) -> bytes:
    h = json.dumps({"rid": seg.rid, "sid": seg.sid, "rate": seg.rate, "start_ns": seg.start_ns, "off": off,
                    "n": len(values)}).encode()
    return struct.pack("<I", len(h)) + h + struct.pack(f"<{len(values)}i", *values)


def read_spool(path: Path):
    """Yields (header, values); a torn last record (crash during the write) is ignored."""
    b = path.read_bytes()
    i = 0
    while i + 4 <= len(b):
        (hl,) = struct.unpack_from("<I", b, i)
        if i + 4 + hl > len(b):
            return
        h = json.loads(b[i + 4:i + 4 + hl])
        j = i + 4 + hl
        if j + 4 * h["n"] > len(b):
            return
        yield h, list(struct.unpack_from(f"<{h['n']}i", b, j))
        i = j + 4 * h["n"]


class Store:
    """MorletDB side: one recording per segment, chunked appends of the buffered samples."""

    def __init__(self, dsn: str, collection: str, create: bool, window: int, stride: int, extractor: str):
        import psycopg
        self.conn = psycopg.connect(dsn, autocommit=True, application_name="morlet-seedlink")
        self.c = collection
        self.window, self.stride, self.extractor = window, stride, extractor
        self.known: set[str] = set()
        if create:
            schema, _, name = collection.rpartition(".")
            with self.conn.transaction():
                if self.conn.execute("SELECT to_regclass(%s)", (collection,)).fetchone()[0] is None:
                    self.conn.execute("SELECT morlet_create_stream_collection(%s,%s)", (schema or "public", name))
                    self.conn.execute("SELECT morlet_enable_envelopes(%s::regclass)", (collection,))
                    self.conn.execute("SELECT morlet_enable_rollups(%s::regclass)", (collection,))

    def ensure(self, rid: str, sid: str, rate: float, start_ns: int, server: str) -> None:
        if rid in self.known:
            return
        net, sta, loc, cha = sid.split(".")
        meta = {"network": net, "station": sta, "location": loc, "channel": cha, "start_ns": str(start_ns),
                "source": f"seedlink://{server}"}
        self.conn.execute("SELECT morlet_ensure_recording(%s::regclass,%s,%s,%s,'counts',%s::jsonb,%s,%s,'zscore',%s)",
                          (self.c, rid, rate, cha, json.dumps(meta), self.window, self.stride, self.extractor))
        self.known.add(rid)

    def stored_end(self, rid: str) -> int:
        r = self.conn.execute(f"SELECT (checkpoint->>'end')::bigint FROM {self.c} WHERE recording_id=%s",
                              (rid,)).fetchone()
        return int(r[0]) if r and r[0] is not None else 0

    def append(self, rid: str, off: int, values: list[int], chunk: int) -> None:
        raw = struct.pack(f"<{len(values)}d", *values)
        self.conn.execute("SELECT morlet_append_chunks_raw(%s::regclass,%s,%s,%b,%s)", (self.c, rid, off, raw, chunk))


class Collector:
    def __init__(self, a, store: Store | None, clock=time.time):
        self.a, self.store, self.clock = a, store, clock
        self.spool_dir = Path(a.spool)
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.gaps = open(self.spool_dir / "gaps.jsonl", "a")
        self.seg = Segmenter()
        self.spool = None
        self.spool_hour = None
        self.spool_dirty = False
        self.last_fsync = 0.0
        self.pending: dict[str, int] = {}       # spool file -> streams whose samples in it are not all stored
        self.file_need: dict[str, dict[str, int]] = {}  # spool file -> rid -> sample end that must be stored
        self.stats = {"packets": 0, "samples": 0, "stored": 0, "duplicates": 0, "segments": 0, "reconnects": 0,
                      "started": clock()}
        self.last_seen: dict[str, float] = {}

    def log(self, **kw) -> None:
        self.gaps.write(json.dumps({"at": self.clock(), **kw}) + "\n")
        self.gaps.flush()

    # -- spool -------------------------------------------------------------------------------------------------
    def spool_write(self, seg: Segment, off: int, values: list[int]) -> None:
        hour = dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).strftime("%Y%m%dT%H")
        if hour != self.spool_hour:
            if self.spool:
                self.spool.flush()
                os.fsync(self.spool.fileno())
                self.spool.close()
            self.spool_hour = hour
            self.spool = open(self.spool_dir / f"{hour}.spool", "ab")
            self.file_need.setdefault(self.spool.name, {})
        self.spool.write(spool_record(seg, off, values))
        self.spool_dirty = True
        need = self.file_need[self.spool.name]
        need[seg.rid] = max(need.get(seg.rid, 0), off + len(values))

    def sync(self, force: bool = False) -> None:
        if self.spool and self.spool_dirty and (force or self.clock() - self.last_fsync >= 1.0):
            self.spool.flush()
            os.fsync(self.spool.fileno())
            self.spool_dirty = False
            self.last_fsync = self.clock()

    def release_spool(self, stored: dict[str, int]) -> None:
        """Deletes closed spool files whose samples are all stored."""
        for name, need in list(self.file_need.items()):
            if self.spool and name == self.spool.name:
                continue
            if all(stored.get(rid, 0) >= end for rid, end in need.items()):
                Path(name).unlink(missing_ok=True)
                del self.file_need[name]

    # -- packets -----------------------------------------------------------------------------------------------
    def handle(self, p: Packet) -> None:
        self.stats["packets"] += 1
        self.last_seen[p.sid] = self.clock()
        for e in self.seg.feed(p):
            if e[0] == "end":
                self.flush(e[1])
                self.log(stream=e[1].sid, recording=e[1].rid, samples=e[1].n, cause=e[2])
            elif e[0] == "start":
                self.stats["segments"] += 1
                if self.store:
                    self.store.ensure(e[1].rid, e[1].sid, e[1].rate, e[1].start_ns, self.a.server)
            else:
                seg, off, values = e[1], e[2], e[3]
                self.stats["samples"] += len(values)
                self.spool_write(seg, off, values)
                if not seg.buffer:
                    seg.buffer_since = self.clock()
                seg.buffer.extend(values)
                if len(seg.buffer) >= self.a.chunk:
                    self.flush(seg)
        self.sync()

    def flush(self, seg: Segment) -> None:
        if not seg.buffer:
            return
        self.sync(force=True)       # the spool holds every sample before the database sees it
        if self.store:
            self.store.append(seg.rid, seg.stored, seg.buffer, self.a.chunk)
        seg.stored += len(seg.buffer)
        self.stats["stored"] += len(seg.buffer)
        seg.buffer = []

    def tick(self) -> None:
        """Flushes buffers older than --flush-s and releases spool files that are fully stored."""
        now = self.clock()
        for seg in self.seg.segs.values():
            if seg.buffer and now - seg.buffer_since >= self.a.flush_s:
                self.flush(seg)
        stored = {s.rid: s.stored for s in self.seg.segs.values()}
        ended = {rid for need in self.file_need.values() for rid in need} - set(stored)
        stored.update({rid: 1 << 62 for rid in ended})   # an ended segment was flushed when it ended
        self.release_spool(stored)

    def write_status(self) -> None:
        now = self.clock()
        st = dict(self.stats, at=now, streams=len(self.last_seen),
                  stale=sorted(s for s, t in self.last_seen.items() if now - t > 300))
        tmp = self.spool_dir / "status.json.tmp"
        tmp.write_text(json.dumps(st, indent=1))
        os.replace(tmp, self.spool_dir / "status.json")

    def close(self) -> None:
        self.sync(force=True)
        if self.spool:
            self.spool.close()
        self.gaps.close()

    # -- replay ------------------------------------------------------------------------------------------------
    def replay(self) -> None:
        """Appends what earlier runs spooled but did not store; then removes those spool files."""
        files = sorted(self.spool_dir.glob("*.spool"))
        for f in files:
            ends: dict[str, int] = {}
            for h, values in read_spool(f):
                if self.store:
                    self.store.ensure(h["rid"], h["sid"], h["rate"], h["start_ns"], self.a.server)
                    if h["rid"] not in ends:
                        ends[h["rid"]] = self.store.stored_end(h["rid"])
                    end = ends[h["rid"]]
                    if h["off"] + h["n"] > end:
                        if h["off"] > end:
                            self.log(recording=h["rid"], cause="spool hole on replay", stored=end, next=h["off"])
                            continue
                        self.store.append(h["rid"], end, values[end - h["off"]:], self.a.chunk)
                        ends[h["rid"]] = h["off"] + h["n"]
            self.log(cause="collector restart: spool replayed", file=f.name, recordings=len(ends))
            f.unlink()


def seedlink_thread(a, q: "queue.Queue", stats: dict, stop: threading.Event) -> None:
    """Runs the ObsPy SeedLink client; reconnects with backoff (10 s to 5 min); never polls an archive."""
    from obspy.clients.seedlink.easyseedlink import create_client
    delay = 10.0

    def on_data(tr):
        q.put(Packet(tr.id, tr.stats.starttime.ns, float(tr.stats.sampling_rate), [int(x) for x in tr.data]))

    while not stop.is_set():
        try:
            client = create_client(a.server, on_data=on_data)
            for s in a.streams:
                net, sta, loc, cha = s.split(".")
                client.select_stream(net, sta, f"{loc}{cha}")
            delay = 10.0
            client.run()
            err = "connection ended"
        except Exception as exc:  # noqa: BLE001 - reported in gaps.jsonl
            err = f"{type(exc).__name__}: {exc}"
        stats["reconnects"] += 1
        q.put(("reconnect", err))
        stop.wait(delay)
        delay = min(delay * 2, 300.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="geofon.gfz.de:18000")
    ap.add_argument("--streams", nargs="+", required=True, help="NET.STA.LOC.CHA, ? wildcards allowed in CHA")
    ap.add_argument("--collection", default="public.live")
    ap.add_argument("--chunk", type=int, default=4096, help="samples per stored chunk")
    ap.add_argument("--flush-s", dest="flush_s", type=float, default=60.0)
    ap.add_argument("--spool", default=os.path.expanduser("~/.morlet-seedlink"))
    ap.add_argument("--window", type=int, default=2048)
    ap.add_argument("--stride", type=int, default=512)
    ap.add_argument("--extractor", default="morlet-energy-lowrate-v1")
    ap.add_argument("--create", action="store_true", help="create and configure the collection if missing")
    ap.add_argument("--setup-only", action="store_true", help="with --create: create the collection, then exit")
    a = ap.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("set DATABASE_URL", file=sys.stderr)
        return 2
    store = Store(dsn, a.collection, a.create, a.window, a.stride, a.extractor)
    if a.setup_only:
        return 0
    col = Collector(a, store)

    def on_term(signum, frame):   # docker stop and systemd send SIGTERM: store the buffers, then exit
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    col.replay()
    q: queue.Queue = queue.Queue(maxsize=100_000)
    stop = threading.Event()
    threading.Thread(target=seedlink_thread, args=(a, q, col.stats, stop), daemon=True).start()
    last_status = 0.0
    try:
        while True:
            try:
                item = q.get(timeout=1.0)
            except queue.Empty:
                item = None
            if isinstance(item, Packet):
                col.handle(item)
            elif isinstance(item, tuple):
                col.log(cause="reconnect", error=item[1])
            col.tick()
            if time.time() - last_status >= 60:
                col.stats["backlog"] = q.qsize()
                col.write_status()
                last_status = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for seg in col.seg.segs.values():
            col.flush(seg)
        col.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
