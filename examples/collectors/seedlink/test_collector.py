"""Tests of the SeedLink collector without a server or a database (python -m unittest discover -s ...)."""
import argparse
import datetime as dt
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collector import NS, Collector, Packet, Segmenter, Store, read_spool  # noqa: E402

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



class Lost(Exception):
    """Stands in for psycopg.OperationalError (connection closed, AdminShutdown)."""


class FakeDb:
    """A database that can restart: appends follow MorletDB's retry rule (an identical retry is a no-op, a
    conflicting one fails). fail_next makes the next statement break the connection, before or after committing."""

    def __init__(self):
        self.data, self.up, self.fail_next, self.connects = {}, True, None, 0

    def connect(self):
        if not self.up:
            raise Lost("connection refused")
        self.connects += 1
        return FakeConn(self)


class FakeConn:
    def __init__(self, db):
        self.db, self.closed = db, False

    def close(self):
        self.closed = True

    def execute(self, sql, args):
        db = self.db
        if self.closed or not db.up:
            raise Lost("the connection is closed")
        mode, db.fail_next = db.fail_next, None
        if mode == "before":
            self.closed = True
            raise Lost("terminating connection due to administrator command")
        if "morlet_ensure_recording" in sql:
            db.data.setdefault(args[1], [])
        elif "morlet_append_chunks_raw" in sql:
            rid, off, raw = args[1], args[2], args[3]
            vals = list(struct.unpack(f"<{len(raw) // 8}d", raw))
            have = db.data[rid]
            if off + len(vals) <= len(have):
                assert have[off:off + len(vals)] == vals, "conflicting retry"
            else:
                assert off == len(have), (rid, off, len(have))
                have.extend(vals)
        if mode == "after":                 # committed, but the reply never arrives
            self.closed = True
            raise Lost("server closed the connection unexpectedly")
        return self


class StoreReconnectTest(unittest.TestCase):
    """Soak S1's exploratory D2x: an orderly database restart made the collector exit on a closed connection."""

    def store(self, db, sleeps):
        return Store("", "public.live", False, 2048, 512, "x", retry_s=600.0, connect=db.connect, retry_on=(Lost,),
                     sleep=sleeps.append)

    def test_a_restart_before_the_commit_is_retried_on_a_new_connection(self):
        db, sleeps, seen = FakeDb(), [], []
        st = self.store(db, sleeps)
        st.on_reconnect = lambda err, waited: seen.append((err, waited))
        st.ensure("R", "A.B..HHZ", 100.0, T0, "test")
        db.fail_next = "before"
        st.append("R", 0, [1, 2, 3], 4096)
        self.assertEqual(db.data["R"], [1.0, 2.0, 3.0])
        self.assertEqual(db.connects, 2)
        self.assertEqual(len(seen), 1)
        self.assertIn("administrator command", seen[0][0])

    def test_a_restart_after_the_commit_is_an_identical_retry_and_stores_nothing_twice(self):
        db, sleeps = FakeDb(), []
        st = self.store(db, sleeps)
        st.ensure("R", "A.B..HHZ", 100.0, T0, "test")
        db.fail_next = "after"
        st.append("R", 0, [1, 2, 3], 4096)
        st.append("R", 3, [4], 4096)
        self.assertEqual(db.data["R"], [1.0, 2.0, 3.0, 4.0])

    def test_it_waits_while_the_database_is_down_and_gives_up_after_retry_s(self):
        db, sleeps = FakeDb(), []
        st = self.store(db, sleeps)
        st.ensure("R", "A.B..HHZ", 100.0, T0, "test")
        db.up = False
        with self.assertRaises(Lost):
            st.append("R", 0, [1], 4096)
        self.assertGreaterEqual(sum(sleeps), 600.0)
        self.assertLessEqual(max(sleeps), 30.0)
        db.up, sleeps[:] = True, []
        calls = []

        def down_for_three_tries():
            calls.append(1)
            db.up = len(calls) > 3
            return db.connect()
        st._connect = down_for_three_tries
        db.up = False
        try:
            st.append("R", 0, [1], 4096)
        finally:
            db.up = True
        self.assertEqual(db.data["R"], [1.0])
        self.assertEqual(sleeps, [1.0, 2.0, 4.0, 8.0])

    def test_the_collector_logs_each_database_reconnect(self):
        with tempfile.TemporaryDirectory() as d:
            db, sleeps = FakeDb(), []
            col = Collector(args(d), self.store(db, sleeps), clock=Clock())
            col.handle(pkt("A.B..HHZ", 0, 400))
            db.fail_next = "before"
            col.flush(col.seg.segs["A.B..HHZ"])
            col.close()
            lines = [json.loads(x) for x in (Path(d) / "gaps.jsonl").read_text().splitlines()]
            self.assertEqual([x["cause"] for x in lines], ["database reconnect"])
            self.assertEqual(col.stats["db_reconnects"], 1)
            self.assertEqual(len(db.data["A.B..HHZ.20261108T120000"]), 400)


    def test_the_retry_budget_is_not_exceeded(self):
        db, sleeps = FakeDb(), []
        st = self.store(db, sleeps)
        st.ensure("R", "A.B..HHZ", 100.0, T0, "test")
        db.up = False
        with self.assertRaises(Lost):
            st.append("R", 0, [1], 4096)
        self.assertEqual(sum(sleeps), 600.0)

    def test_packets_received_while_the_database_stays_down_are_all_in_the_spool(self):
        """Review of #525: a new segment's packet waited in ensure, and queued packets were only in memory."""
        import queue as q_
        with tempfile.TemporaryDirectory() as d:
            db, sleeps = FakeDb(), []
            st = Store("", "public.live", False, 2048, 512, "x", retry_s=5.0, connect=db.connect, retry_on=(Lost,),
                       sleep=sleeps.append)
            col = Collector(args(d), st, clock=Clock())
            col.handle(pkt("A.B..HHZ", 0, 400))
            db.up = False
            with self.assertRaises(Lost):
                col.handle(pkt("A.B..HHN", 0, 400))       # a new recording: ensure fails after the retries
            queued = q_.Queue()
            for k in range(1, 4):
                queued.put(pkt("A.B..HHZ", 400 * k, 400))
                queued.put(pkt("A.B..HHN", 400 * k, 400))
            self.assertEqual(col.spool_rest(queued), 6)
            col.close()
            replayed = FakeStore()
            again = Collector(args(d), replayed, clock=Clock())
            again.replay()
            again.close()
            rid = "A.B..{}.20261108T120000"
            self.assertEqual(replayed.data[rid.format("HHZ")], list(range(0, 1600)))
            self.assertEqual(replayed.data[rid.format("HHN")], list(range(0, 1600)))


if __name__ == "__main__":
    unittest.main()
