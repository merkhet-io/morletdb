"""Metric bridge (#639). Line-protocol encoding runs without a database. The rest needs MORLET_BRIDGE_TEST_DSN: a
PostgreSQL server with pg_morlet SQL 0.3.18 or later where the role may create databases and roles; each test
gets its own database.

  MORLET_BRIDGE_TEST_DSN='host=localhost port=28817 dbname=postgres' python -m unittest discover -s tools/bridge
"""
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from morlet_bridge import Bridge, line  # noqa: E402

DSN = os.environ.get("MORLET_BRIDGE_TEST_DSN")
T0 = 1767225600000000000
VALUES = [-0.0, 1.0, -1.0, 2.0] * 256          # mean 0.5, RMS sqrt(1.5), min -1, max 2, signed zero first
RAW = struct.pack("<1024d", *VALUES)


def row(**kw):
    r = {"asset": "pump A", "channel": "vib,x", "calculation": "interval-stats-v1", "minimum": -1.0,
         "maximum": 2.0, "mean": 0.1, "rms": 1.2247448713915890, "sample_count": 1024, "start_sample": 0,
         "end_sample": 1024, "ledger_id": 7, "end_time_ns": T0 + 10 ** 9, "recording_id": 'r"1',
         "collection": "public.signals", "interpretation_revision": None, "time_ns": T0}
    r.update(kw)
    return r


class LineProtocol(unittest.TestCase):
    def test_tags_fields_and_timestamp(self):
        self.assertEqual(
            line("morlet metric", row()),
            'morlet\\ metric,asset=pump\\ A,calculation=interval-stats-v1,channel=vib\\,x '
            'minimum=-1.0,maximum=2.0,mean=0.1,rms=1.224744871391589,sample_count=1024i,start_sample=0i,'
            'end_sample=1024i,ledger_id=7i,end_time_ns=1767225601000000000i,recording_id="r\\"1",'
            'collection="public.signals" 1767225600000000000')

    def test_floats_round_trip_and_nonfinite_values_are_named_not_written(self):
        for v in (0.1, -0.0, 5e-324, 1.7976931348623157e308, math.sqrt(1.5)):
            text = line("m", row(mean=v))
            written = text.split("mean=")[1].split(",")[0]
            self.assertEqual(struct.pack("<d", float(written)), struct.pack("<d", v))
        text = line("m", row(rms=float("nan"), maximum=float("inf"), interpretation_revision=2))
        self.assertNotIn("rms=", text)
        self.assertNotIn("maximum=", text)
        self.assertIn('nonfinite="maximum,rms"', text)
        self.assertIn("interpretation_revision=2i", text)


@unittest.skipUnless(DSN, "needs MORLET_BRIDGE_TEST_DSN")
class Database(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg

    def connect(self, db=None, **kw):
        dsn = self.psycopg.conninfo.make_conninfo(DSN, **({"dbname": db} if db else {}), **kw)
        return self.psycopg.connect(dsn, autocommit=True, options="-c statement_timeout=30000 -c lock_timeout=10000")

    def setUp(self):
        from psycopg import sql
        from psycopg.types.json import Jsonb
        self.sql, self.Jsonb = sql, Jsonb
        self.db = "bridge_" + secrets.token_hex(5)
        with self.connect() as c:
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.db)))
        self.dsn = self.psycopg.conninfo.make_conninfo(DSN, dbname=self.db)
        self.c = self.connect(self.db)
        self.c.execute("CREATE EXTENSION pg_morlet")
        self.c.execute("SELECT morlet_create_stream_collection('public','signals')")
        self.b = Bridge(self.c)
        self.b.install()
        self.start("a")
        self.append("a", 0)
        with self.b.transaction():
            self.sid = self.b.register("public.signals", "a", "pump-A", "vib-x", 1024)
        self.roles = []

    def tearDown(self):
        self.c.close()
        with self.connect() as c:
            c.execute(self.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(self.sql.Identifier(self.db)))
            for r in self.roles:
                c.execute(self.sql.SQL("DROP ROLE IF EXISTS {}").format(self.sql.Identifier(r)))

    def start(self, rid, rate=1024, ns=T0):
        self.c.execute("SELECT morlet_start_recording('signals',%s,%s,'vibration','g',%s,extractor=>'none')",
                       (rid, rate, self.Jsonb({"start_ns": ns})))

    def append(self, rid, offset, values=VALUES):
        self.c.execute("SELECT morlet_append_chunk('signals',%s,%s,%s::float8[])", (rid, offset, values))

    def scalar(self, q, args=()):
        return self.c.execute(q, args).fetchone()[0]

    # ---- publication
    def test_publication_is_exact_atomic_and_retry_safe(self):
        with self.b.transaction():
            lid = self.b.publish(self.sid, 0, 1024)
            self.assertEqual(self.b.publish(self.sid, 0, 1024), lid)            # identical retry: no-op
        r = self.b.one("SELECT * FROM morlet_bridge.ledger")
        self.assertEqual((r["time_ns"], r["end_time_ns"], r["sample_count"], r["minimum"], r["maximum"], r["mean"]),
                         (T0, T0 + 10 ** 9, 1024, -1.0, 2.0, 0.5))
        self.assertAlmostEqual(r["rms"], math.sqrt(1.5), places=15)
        self.assertEqual(r["source_sha256"], hashlib.sha256(RAW).hexdigest())   # signed zero included
        self.assertIsNone(r["provenance"]["interpretation_revision"])
        with self.assertRaises(self.psycopg.errors.DivisionByZero):
            with self.b.transaction():
                self.append("a", 1024)
                self.b.publish(self.sid, 1024, 2048)
                self.c.execute("SELECT 1/0")
        self.assertEqual(self.scalar("SELECT (checkpoint->>'end')::bigint FROM signals"), 1024)
        self.assertEqual(self.scalar("SELECT count(*) FROM morlet_bridge.ledger"), 1)
        self.c.execute("UPDATE morlet_bridge.ledger SET rms = 99")
        with self.assertRaisesRegex(ValueError, "conflicting metric retry"):
            with self.b.transaction():
                self.b.publish(self.sid, 0, 1024)

    def test_partial_misaligned_and_outside_publications_are_refused(self):
        self.append("a", 1024)
        for start, end in ((0, 512), (512, 1536), (0, 2048), (2048, 3072)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                with self.b.transaction():
                    self.b.publish(self.sid, start, end)
        with self.assertRaisesRegex(ValueError, "bridge.transaction"):
            self.b.publish(self.sid, 0, 1024)

    def test_drain_resumes_after_a_terminated_writer(self):
        for k in range(1, 4):
            self.append("a", 1024 * k)
        self.assertEqual(self.b.drain(self.sid, 1)["processed_end"], 1024)
        killer = self.connect(self.db)
        pid = self.scalar("SELECT pg_backend_pid()")
        with self.assertRaises(self.psycopg.OperationalError):
            with self.b.transaction():
                self.b.publish(self.sid, 1024, 2048)
                killer.execute("SELECT pg_terminate_backend(%s)", (pid,))
                self.c.execute("SELECT 1")
        killer.close()
        self.c = self.connect(self.db)
        self.b = Bridge(self.c)
        self.assertEqual(self.scalar("SELECT count(*) FROM morlet_bridge.ledger"), 1)
        st = self.b.drain(self.sid, 10)
        self.assertEqual((st["published_windows"], st["processed_end"], st["complete"]), (3, 4096, True))
        self.assertEqual(self.scalar("SELECT array_agg(start_sample ORDER BY start_sample) FROM morlet_bridge.ledger"),
                         [0, 1024, 2048, 3072])

    def test_anchored_time_and_a_changed_time_axis(self):
        self.c.execute("SELECT morlet_enable_time_anchors('signals')")
        self.start("anch")
        self.append("anch", 0)
        anchors = [{"recording_id": "anch", "sample_offset": n, "utc_ns": T0 + n * 1000000 + 3,
                    "uncertainty_ns": 1, "source": "synthetic"} for n in (0, 1024)]
        self.c.execute("SELECT morlet_add_time_anchors('signals','b1',%s)", (self.Jsonb(anchors),))
        with self.b.transaction():
            sid = self.b.register("public.signals", "anch", "pump-A", "vib-y", 1024)
        self.b.drain(sid, 1)
        r = self.b.one("SELECT * FROM morlet_bridge.ledger WHERE source_id = %s", (sid,))
        self.assertEqual((r["time_ns"], r["end_time_ns"]), (T0 + 3, T0 + 1024 * 1000000 + 3))
        self.assertNotEqual(r["provenance"]["time_axis_version"], "start+rate")
        self.c.execute("SELECT morlet_add_time_anchors('signals','b2',%s)", (self.Jsonb(
            [{"recording_id": "anch", "sample_offset": 512, "utc_ns": T0 + 512 * 1000000 + 9,
              "uncertainty_ns": 1, "source": "synthetic"}]),))
        with self.assertRaisesRegex(ValueError, "time axis changed"):
            self.b.drain(sid, 1)

    def test_an_interpretation_correction_stops_publication(self):
        self.c.execute("SELECT morlet_enable_interpretation('signals')")
        self.start("cal")
        self.append("cal", 0)
        with self.b.transaction():
            sid = self.b.register("public.signals", "cal", "pump-A", "vib-z", 1024)
        self.b.drain(sid, 1)
        self.assertEqual(self.scalar("SELECT interpretation_revision FROM morlet_bridge.sources WHERE source_id=%s",
                                     (sid,)), 0)
        self.assertEqual(self.scalar("SELECT provenance->'interpretation_revision' FROM morlet_bridge.ledger "
                                     "WHERE source_id=%s", (sid,)), 0)
        self.append("cal", 1024)
        self.c.execute("SELECT morlet_correct_interpretation('signals','cal','m/s^2','{\"gain\":9.81}','test')")
        with self.assertRaisesRegex(ValueError, "need review"):
            self.b.drain(sid, 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM morlet_bridge.ledger WHERE source_id=%s", (sid,)), 1)

    def test_a_reverted_correction_still_stops_publication(self):
        # #620: after a correction and its reversal the specification looks unchanged, but the revision moved on
        self.c.execute("SELECT morlet_enable_interpretation('signals')")
        self.c.execute("SELECT morlet_start_recording('signals','rev',1024,'vibration','g',%s,extractor=>'none')",
                       (self.Jsonb({"start_ns": T0, "calibration": {"gain": 1}}),))
        self.append("rev", 0)
        with self.b.transaction():
            sid = self.b.register("public.signals", "rev", "pump-A", "vib-r", 1024)
        spec = self.scalar("SELECT specification FROM morlet_bridge.sources WHERE source_id=%s", (sid,))
        self.c.execute("SELECT morlet_correct_interpretation('signals','rev','g','{\"gain\":2}','wrong gain')")
        self.c.execute("SELECT morlet_correct_interpretation('signals','rev','g','{\"gain\":1}','reverted')")
        self.assertEqual(self.b.source("public.signals", "rev")[1], spec)      # the specification is back
        with self.assertRaisesRegex(ValueError, "interpretation revision changed"):
            self.b.drain(sid, 1)

    def test_sources_with_row_security_are_refused(self):
        self.c.execute("SELECT morlet_create_stream_collection('public','tenant')")
        self.c.execute("SELECT morlet_enable_row_security('tenant')")
        with self.assertRaisesRegex(ValueError, "row security"):
            with self.b.transaction():
                self.b.register("public.tenant", "x", "pump-A", "vib", 1024)

    # ---- sinks
    def test_postgres_sink_is_exactly_once_and_reports_a_changed_copy(self):
        self.append("a", 1024)
        self.b.drain(self.sid, 10)
        self.b.add_sink("pg", "postgres")
        self.assertEqual(self.b.deliver("pg"), 2)
        self.assertEqual(self.b.deliver("pg"), 0)
        self.assertEqual(self.scalar("SELECT count(*) FROM morlet_bridge.metrics"), 2)
        m = self.b.one("SELECT * FROM morlet_bridge.metrics ORDER BY time_ns LIMIT 1")
        self.assertEqual((m["recording_id"], m["start_sample"], m["end_sample"], m["asset"], m["channel"], m["mean"]),
                         ("a", 0, 1024, "pump-A", "vib-x", 0.5))
        self.assertEqual(self.b.sink_status("pg")["pending"], 0)
        # a copy that differs from the ledger is reported, not silently kept
        self.append("a", 2048)
        self.b.drain(self.sid, 10)
        lid = self.scalar("SELECT max(ledger_id) FROM morlet_bridge.ledger")
        self.c.execute("""INSERT INTO morlet_bridge.metrics (ledger_id,time_ns,end_time_ns,asset,channel,calculation,
            collection,recording_id,start_sample,end_sample,sample_count,minimum,maximum,mean,rms)
            VALUES (%s,0,1,'x','x','x','x','x',0,1,1,0,0,0,0)""", (lid,))
        with self.assertRaisesRegex(ValueError, "differs from the ledger"):
            self.b.deliver("pg")
        self.assertIn("differs", self.b.sink_status("pg")["last_error"])
        self.assertEqual(self.b.sink_status("pg")["pending"], 1)

    def test_concurrent_deliveries_deliver_each_row_once(self):
        for k in range(1, 8):
            self.append("a", 1024 * k)
        self.b.drain(self.sid, 10)
        self.b.add_sink("pg", "postgres")
        counts, errors = [], []

        def run():
            try:
                with self.connect(self.db) as c:
                    counts.append(Bridge(c).deliver("pg", limit=3))
            except Exception as exc:                    # noqa: BLE001 - reported below
                errors.append(exc)
        for _ in range(3):
            ts = [threading.Thread(target=run) for _ in range(4)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(errors, [])
        self.assertEqual(sum(counts), 8)
        self.assertEqual(self.scalar("SELECT count(*) FROM morlet_bridge.metrics"), 8)

    def test_line_sink_is_at_least_once(self):
        self.append("a", 1024)
        self.b.drain(self.sid, 10)
        self.b.add_sink("influx", "line", options={"measurement": "vibration"})

        def fail(_text):
            raise OSError("pipe closed")
        with self.assertRaises(OSError):
            self.b.lines("influx", fail)
        self.assertEqual(self.b.sink_status("influx")["pending"], 2)        # nothing recorded as delivered
        out = []
        self.assertEqual(self.b.lines("influx", out.append), 2)
        self.assertEqual(self.b.lines("influx", out.append), 0)
        points = "".join(out).splitlines()
        self.assertEqual(len(points), 2)
        self.assertTrue(points[0].startswith("vibration,asset=pump-A,calculation=interval-stats-v1,channel=vib-x "))
        self.assertTrue(points[0].endswith(f" {T0}"))
        self.assertIn("mean=0.5,", points[0])
        self.assertIn('recording_id="a"', points[0])

    def test_telegraf_execd_answers_each_signal_and_stops_at_eof(self):
        self.append("a", 1024)
        self.b.add_sink("telegraf", "line")
        cli = Path(__file__).with_name("cli.py")
        p = subprocess.Popen([sys.executable, "-I", str(cli), "--dsn", self.dsn, "telegraf", "telegraf", "--drain-all"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p.stdin.write("\n")
        p.stdin.flush()
        first = [p.stdout.readline(), p.stdout.readline()]
        p.stdin.close()
        self.assertEqual(p.wait(timeout=30), 0, p.stderr.read())
        self.assertTrue(all(x.startswith("morlet_metric,asset=pump-A") for x in first), first)
        self.assertEqual(self.b.sink_status("telegraf")["pending"], 0)

    def test_metric_readers_need_their_own_grant_and_get_no_waveform_access(self):
        self.b.drain(self.sid, 10)
        self.b.add_sink("pg", "postgres")
        self.b.deliver("pg")
        reader, other = "bridge_reader_" + secrets.token_hex(3), "bridge_other_" + secrets.token_hex(3)
        pw = secrets.token_hex(12)
        for r in (reader, other):
            self.c.execute(self.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(self.sql.Identifier(r),
                                                                                 self.sql.Literal(pw)))
            self.roles.append(r)
        self.c.execute(self.sql.SQL("GRANT USAGE ON SCHEMA morlet_bridge TO {}").format(self.sql.Identifier(reader)))
        self.c.execute(self.sql.SQL("GRANT SELECT ON morlet_bridge.metrics TO {}").format(self.sql.Identifier(reader)))
        with self.connect(self.db, user=reader, password=pw) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM morlet_bridge.metrics").fetchone()[0], 1)
            for q in ("SELECT count(*) FROM morlet_bridge.ledger", "SELECT count(*) FROM signals",
                      "SELECT count(*) FROM signals_chunks"):
                with self.subTest(q=q), self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    c.execute(q)
        with self.connect(self.db, user=other, password=pw) as c:
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT count(*) FROM morlet_bridge.metrics")


if __name__ == "__main__":
    unittest.main()
