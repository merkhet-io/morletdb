"""Tests of the Shelly collector: python -m unittest discover -s examples/collectors/shelly
Without MORLET_SHELLY_TEST_DSN only the parts without a database run."""
import datetime as dt
import json
import os
import tempfile
import types
import unittest

import collector as C

T0 = dt.datetime(2026, 11, 8, 23, 59, 0, tzinfo=dt.timezone.utc).timestamp()


def status(k):
    """A washing-machine-like plug answer for poll k: heating, then motor, then idle."""
    p = 2000.0 if k < 20 else 300.0 + (k % 7) * 10 if k < 60 else 1.5
    return {"id": 0, "output": True, "apower": p, "voltage": 230.0 + (k % 3) * 0.1, "freq": 50.0,
            "current": round(p / 230.0, 3), "aenergy": {"total": 1000.0 + k * 0.5}, "temperature": {"tC": 40.0}}


class FakeTime:
    def __init__(self, t):
        self.t = t

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class Segmenting(unittest.TestCase):
    def test_reading_takes_reported_channels_only(self):
        self.assertEqual(C.reading(status(0)), {"power": 2000.0, "voltage": 230.0, "frequency": 50.0,
                                                "current": 8.696, "energy": 1000.0})
        self.assertEqual(C.reading({"apower": 3, "voltage": True, "aenergy": {}}), {"power": 3.0})

    def test_a_missing_or_late_reading_ends_the_segment_and_nothing_is_invented(self):
        s = C.Segmenter(1.0)
        events, t = [], T0
        for k in range(10):
            t = s.due(t) + (0.9 if k == 7 else 0.05)   # polls follow the segment clock; poll 7 answers late
            v = None if k == 4 else C.reading(status(k))  # poll 4 fails
            events += s.feed(t, v)
            if v is None:
                t += 1.0                                   # the collector waits one interval after a failure
        kinds = [e[0] for e in events if e[0] != "sample"]
        self.assertEqual(kinds, ["start", "end", "start", "end", "start"])
        ends = [e for e in events if e[0] == "end"]
        self.assertEqual([e[2] for e in ends], ["no answer", "late answer"])
        self.assertEqual([e[1].n for e in ends], [4, 2])      # 0-3, then 5-6; 7.. a new segment
        self.assertEqual(s.seg.n, 3)
        self.assertEqual(sum(1 for e in events if e[0] == "sample"), 9)   # nine real readings, one missing

    def test_segments_end_at_utc_midnight(self):
        s = C.Segmenter(1.0)
        ev = []
        for k in range(120):
            ev += s.feed(T0 + k, C.reading(status(k)))
        ends = [e for e in ev if e[0] == "end"]
        self.assertEqual([e[2] for e in ends], ["midnight"])
        self.assertEqual(ends[0][1].n, 60)
        self.assertEqual(s.seg.start, dt.datetime(2026, 11, 9, tzinfo=dt.timezone.utc))


class Spooling(unittest.TestCase):
    def test_spool_and_gap_log_and_replay_without_a_database(self):
        d = tempfile.mkdtemp()
        ft = FakeTime(T0 + 0.01)
        answers = iter(range(1000))

        def fake(plug, timeout):
            k = next(answers)
            if k == 5:
                raise OSError("timeout")
            return status(k)
        C.get_status, real = fake, C.get_status
        try:
            a = types.SimpleNamespace(plug="x", interval=1.0, flush_s=10.0, spool=d)
            C.run(a, None, ft.clock, ft.sleep, polls=12)
        finally:
            C.get_status = real
        gaps = [json.loads(x) for x in open(os.path.join(d, "gaps.jsonl"))]
        self.assertEqual([g["cause"] for g in gaps], ["no answer"])
        done = sorted(f for f in os.listdir(d) if f.endswith(".done"))
        live = sorted(f for f in os.listdir(d) if f.endswith(".jsonl") and f != "gaps.jsonl")
        self.assertEqual((len(done), len(live)), (1, 1))
        self.assertEqual(len(open(os.path.join(d, live[0])).readlines()), 6)    # polls 6..11
        # a restart replays the open spool and records it
        C.replay(__import__("pathlib").Path(d), C.Segmenter(1.0), None, open(os.path.join(d, "gaps.jsonl"), "a"))
        self.assertFalse([f for f in os.listdir(d) if f.endswith(".jsonl") and f != "gaps.jsonl"])
        self.assertEqual(json.loads(open(os.path.join(d, "gaps.jsonl")).readlines()[-1])["cause"], "collector restart")


DSN = os.environ.get("MORLET_SHELLY_TEST_DSN", "")


@unittest.skipUnless(DSN, "set MORLET_SHELLY_TEST_DSN to run the database test")
class Database(unittest.TestCase):
    def test_segments_are_synchronous_acquisitions_stored_bitwise(self):
        import psycopg
        coll = "public.shelly_test"
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
            for t in c.execute("SELECT tablename FROM pg_tables WHERE tablename LIKE 'shelly\\_test%%'").fetchall():
                c.execute(f"DROP TABLE IF EXISTS {t[0]} CASCADE")
        d = tempfile.mkdtemp()
        ft = FakeTime(T0 - 3600 + 0.01)
        answers = iter(range(1000))

        def fake(plug, timeout):
            k = next(answers)
            if k == 50:
                raise OSError("timeout")
            return status(k)
        C.get_status, real = fake, C.get_status
        try:
            a = types.SimpleNamespace(plug="x", interval=1.0, flush_s=10.0, spool=d)
            store = C.Store(DSN, coll, "washer", 1.0, create=True)
            C.run(a, store, ft.clock, ft.sleep, polls=120)
            # a crash before the last flush: the restart replays the spool, and identical samples are not stored twice
            C.replay(__import__("pathlib").Path(d), C.Segmenter(1.0), store, open(os.path.join(d, "gaps.jsonl"), "a"))
        finally:
            C.get_status = real
        with psycopg.connect(DSN, autocommit=True) as c:
            acqs = c.execute("SELECT acquisition_id,sync_roles FROM shelly_test_acq ORDER BY 1").fetchall()
            self.assertEqual(len(acqs), 2)
            self.assertEqual(acqs[0][1], ["current", "energy", "frequency", "power", "voltage"])
            ends = c.execute("SELECT (checkpoint->>'end')::bigint FROM shelly_test WHERE recording_id LIKE 'washer.power.%%' "
                             "ORDER BY recording_id").fetchall()
            self.assertEqual([e[0] for e in ends], [50, 69])
            back = c.execute("SELECT samples FROM morlet_read_sync_raw('shelly_test',%s,0,50) WHERE role='power'",
                             (acqs[0][0],)).fetchone()[0]
            import struct
            self.assertEqual(list(struct.unpack("<50d", back)), [status(k)["apower"] for k in range(50)])
            st = c.execute("SELECT sample_count,mean_ab FROM morlet_acquisition_cross_stats('shelly_test',%s,"
                           "'voltage','current',0,50)", (acqs[0][0],)).fetchone()
            self.assertEqual(st[0], 50)
            self.assertAlmostEqual(st[1], sum(status(k)["voltage"] * status(k)["current"] for k in range(50)) / 50, 9)


if __name__ == "__main__":
    unittest.main()
