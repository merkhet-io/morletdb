"""Tests of the SeedLink collector without a server or a database (python -m unittest discover -s ...)."""
import argparse
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collector import NS, Collector, Packet, Segmenter, read_spool  # noqa: E402

T0 = int(dt.datetime(2026, 11, 8, 12, 0, tzinfo=dt.timezone.utc).timestamp()) * NS
R = 100.0


def pkt(sid, first, n, base=0, rate=R, t0=T0):
    """n samples of stream sid starting at sample index first (values base+first ... ) on a 100 Hz clock."""
    return Packet(sid, t0 + round(first * NS / rate), rate, list(range(base + first, base + first + n)))


def kinds(ev):
    return [e[0] if e[0] != "end" else ("end", e[2]) for e in ev]


class SegmenterTest(unittest.TestCase):
    def test_contiguous_packets_continue_one_segment(self):
        s = Segmenter()
        self.assertEqual(kinds(s.feed(pkt("GE.WLF..HHZ", 0, 400))), ["start", "samples"])
        self.assertEqual(kinds(s.feed(pkt("GE.WLF..HHZ", 400, 400))), ["samples"])
        self.assertEqual(s.segs["GE.WLF..HHZ"].n, 800)

    def test_small_jitter_is_not_a_gap_but_a_missing_packet_is(self):
        s = Segmenter()
        s.feed(pkt("A.B..HHZ", 0, 400))
        jitter = Packet("A.B..HHZ", T0 + 4 * NS + 3_000_000, R, [1] * 10)   # 0.3 samples late
        self.assertEqual(kinds(s.feed(jitter)), ["samples"])
        ev = s.feed(pkt("A.B..HHZ", 900, 10))
        self.assertEqual(kinds(ev), [("end", "gap"), "start", "samples"])

    def test_resent_identical_samples_are_dropped(self):
        s = Segmenter()
        s.feed(pkt("A.B..HHZ", 0, 400))
        self.assertEqual(s.feed(pkt("A.B..HHZ", 100, 300)), [])                # entirely known
        ev = s.feed(pkt("A.B..HHZ", 300, 200))                                 # 100 known, 100 new
        self.assertEqual(kinds(ev), ["samples"])
        self.assertEqual((ev[0][2], ev[0][3][0], len(ev[0][3])), (400, 400, 100))

    def test_resent_samples_that_differ_start_a_new_segment(self):
        s = Segmenter()
        s.feed(pkt("A.B..HHZ", 0, 400))
        ev = s.feed(pkt("A.B..HHZ", 300, 200, base=7))
        self.assertEqual(kinds(ev), [("end", "resent samples differ"), "start", "samples"])

    def test_new_day_and_rate_change_end_segments(self):
        s = Segmenter()
        late = T0 + 12 * 3600 * NS - 400 * NS // 100                         # 4 s before midnight
        s.feed(Packet("A.B..HHZ", late, R, [0] * 400))
        ev = s.feed(Packet("A.B..HHZ", late + 4 * NS, R, [0] * 400))
        self.assertEqual(kinds(ev), [("end", "new UTC day"), "start", "samples"])
        ev = s.feed(Packet("A.B..HHZ", late + 8 * NS, 50.0, [0] * 10))
        self.assertEqual(kinds(ev), [("end", "rate changed"), "start", "samples"])

    def test_streams_are_independent(self):
        s = Segmenter()
        s.feed(pkt("A.B..HHZ", 0, 400))
        self.assertEqual(kinds(s.feed(pkt("A.B..HHN", 1000, 400))), ["start", "samples"])
        self.assertEqual(kinds(s.feed(pkt("A.B..HHZ", 400, 400))), ["samples"])


class FakeStore:
    def __init__(self):
        self.data = {}

    def ensure(self, rid, sid, rate, start_ns, server):
        self.data.setdefault(rid, [])

    def stored_end(self, rid):
        return len(self.data.get(rid, []))

    def append(self, rid, off, values, chunk):
        assert off == len(self.data[rid]), (rid, off, len(self.data[rid]))   # contiguous, no rewrite
        self.data[rid].extend(values)


class Clock:
    def __init__(self):
        self.t = T0 / NS

    def __call__(self):
        return self.t


def args(spool):
    return argparse.Namespace(spool=spool, chunk=1000, flush_s=60.0, server="test:18000")


class CollectorTest(unittest.TestCase):
    def test_every_sample_is_stored_once_across_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            clock, store = Clock(), FakeStore()
            c = Collector(args(d), store, clock)
            sent = {"A.B..HHZ": [], "A.B..HHN": []}
            for k in range(30):                           # 30 packets of 250 samples per stream
                for sid in sent:
                    p = pkt(sid, k * 250, 250)
                    sent[sid] += p.data
                    c.handle(p)
                clock.t += 2.5
            # crash: buffers lost; only the spool and what was flushed survive
            c.close()
            self.assertTrue(any(len(v) < 7500 for v in store.data.values()))
            c2 = Collector(args(d), store, clock)
            c2.replay()
            got = {rid.rsplit(".", 1)[0]: v for rid, v in store.data.items()}
            self.assertEqual(got, sent)
            self.assertEqual(list(Path(d).glob("*.spool")), [])
            c2.close()

    def test_spool_files_are_released_once_stored(self):
        with tempfile.TemporaryDirectory() as d:
            clock, store = Clock(), FakeStore()
            c = Collector(args(d), store, clock)
            c.handle(pkt("A.B..HHZ", 0, 500))
            clock.t += 3700                               # the next hour: a new spool file
            c.handle(pkt("A.B..HHZ", 500, 500))
            self.assertEqual(len(list(Path(d).glob("*.spool"))), 2)
            c.tick()                                      # buffer older than flush_s: stored
            self.assertEqual(len(list(Path(d).glob("*.spool"))), 1)   # only the open hour stays
            c.close()

    def test_a_torn_spool_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector(args(d), None, Clock())
            c.handle(pkt("A.B..HHZ", 0, 100))
            c.handle(pkt("A.B..HHZ", 100, 100))
            c.close()
            f = next(Path(d).glob("*.spool"))
            f.write_bytes(f.read_bytes()[:-10])
            recs = list(read_spool(f))
            self.assertEqual([(h["off"], h["n"]) for h, _ in recs], [(0, 100)])


if __name__ == "__main__":
    unittest.main()
