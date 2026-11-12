"""morlet_ship.py against a database (#420): ship, resume, identical bytes. Needs MORLET_MCP_TEST_DATABASE_URL
(a database where the role may create tables, with pg_morlet SQL 0.3.9)."""
import json
import os
import unittest

import morlet_ship as ms

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class Shipping(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.c = psycopg.connect(URL, autocommit=True)
        if self.c.execute("SELECT to_regproc('public.morlet_append_encoded_chunk') IS NULL").fetchone()[0]:
            self.skipTest("server without chunk shipping (SQL 0.3.9)")
        for coll in ("ship_edge", "ship_central"):
            for t in ("chunks", "segments", "envelopes"):
                self.c.execute(f"DROP TABLE IF EXISTS public.{coll}_{t} CASCADE")
            self.c.execute(f"DROP TABLE IF EXISTS public.{coll} CASCADE")
            self.c.execute("SELECT morlet_create_stream_collection('public',%s)", (coll,))
        self.c.execute("SELECT morlet_start_recording('ship_edge','spindle',1000,'acc','g',%s,512,256,'zscore','none',0.001)",
                       (json.dumps({"machine": "M1"}),))
        self.append(0, 10000)

    def append(self, start, end):
        self.c.execute("SELECT morlet_append_chunks_raw('ship_edge','spindle',%s::bigint,morlet_float8_le_bytes(ARRAY("
                       "SELECT round(sin(i/7.0)*1000)/1000 FROM generate_series(%s::bigint,%s::bigint) i)),2500)", (start, start, end - 1))

    def tearDown(self):
        self.c.close()

    def test_ship_then_resume(self):
        rep = ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.assertEqual((rep[0]["chunks_sent"], rep[0]["end"], rep[0]["identical"]), (4, 10000, True))
        meta = self.c.execute("SELECT metadata, sample_quantum FROM ship_central WHERE recording_id='spindle'").fetchone()
        self.assertEqual(meta, ({"machine": "M1"}, 0.001))
        self.append(10000, 15000)                                   # the edge keeps recording
        rep = ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.assertEqual((rep[0]["chunks_sent"], rep[0]["end"], rep[0]["chunks"]), (2, 15000, 6))  # only the new ones
        rep = ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.assertEqual(rep[0]["chunks_sent"], 0)                  # nothing new: nothing sent
        same = self.c.execute("SELECT morlet_read_interval_raw('ship_central','spindle',0,15000) = "
                              "morlet_read_interval_raw('ship_edge','spindle',0,15000)").fetchone()[0]
        self.assertTrue(same)

    def central(self):
        return self.c.execute("SELECT checkpoint->>'end', (SELECT count(*) FROM ship_central_chunks) "
                              "FROM ship_central WHERE recording_id='spindle'").fetchone()

    def test_a_target_with_other_parameters_is_refused_before_writing(self):      # #443
        for args, what in (((2000, "acc", "g", {"machine": "M1"}, 0.001), "sample_rate_hz"),
                           ((1000, "acc", "g", {"machine": "M2"}, 0.001), "metadata"),
                           ((1000, "acc", "V", {"machine": "M1"}, 0.001), "unit"),
                           ((1000, "acc", "g", {"machine": "M1"}, 0.01), "sample_quantum")):
            self.c.execute("DELETE FROM ship_central")
            rate, ch, unit, meta, q = args
            self.c.execute("SELECT morlet_start_recording('ship_central','spindle',%s,%s,%s,%s,512,256,'zscore','none',%s)",
                           (rate, ch, unit, json.dumps(meta), q))
            with self.assertRaisesRegex(ValueError, what):
                ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
            self.assertEqual(self.central(), ("0", 0))                     # nothing written

    def test_a_conflicting_prefix_is_refused_and_left_unchanged(self):            # #444
        self.c.execute("SELECT morlet_start_recording('ship_central','spindle',1000,'acc','g',%s,512,256,'zscore','none',0.001)",
                       (json.dumps({"machine": "M1"}),))
        self.c.execute("SELECT morlet_append_chunks_raw('ship_central','spindle',0,morlet_float8_le_bytes(ARRAY("
                       "SELECT 9.0::float8 FROM generate_series(1,2500))),2500)")
        before = self.central()
        with self.assertRaisesRegex(ValueError, "not the source's chunks"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.assertEqual(self.central(), before)

    def test_other_chunk_boundaries_are_refused(self):                            # #444, same samples, other chunks
        self.c.execute("SELECT morlet_start_recording('ship_central','spindle',1000,'acc','g',%s,512,256,'zscore','none',0.001)",
                       (json.dumps({"machine": "M1"}),))
        self.c.execute("SELECT morlet_append_chunks_raw('ship_central','spindle',0,morlet_read_interval_raw("
                       "'ship_edge','spindle',0,5000),1000)")
        before = self.central()
        with self.assertRaisesRegex(ValueError, "not the source's chunks"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.assertEqual(self.central(), before)

    def test_a_longer_target_is_refused(self):
        ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.c.execute("SELECT morlet_append_chunks_raw('ship_central','spindle',10000,morlet_float8_le_bytes(ARRAY("
                       "SELECT 1.0::float8 FROM generate_series(1,100))),2500)")
        with self.assertRaisesRegex(ValueError, "more than the source"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)

    def test_an_interrupted_identical_transfer_resumes(self):
        class Cut:                                               # the link drops after two committed chunks
            def __init__(self, conn):
                self.conn, self.appends = conn, 0

            def execute(self, q, *a, **k):
                if "morlet_append_encoded_chunk" in q:
                    self.appends += 1
                    if self.appends > 2:
                        raise OSError("link down")
                return self.conn.execute(q, *a, **k)

            def transaction(self):
                return self.conn.transaction()
        with self.assertRaises(OSError):
            ms.ship(self.c, Cut(self.c), "public.ship_edge", "public.ship_central", {}, 1)
        self.assertEqual(self.central(), ("5000", 2))
        rep = ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 1)
        self.assertEqual((rep[0]["chunks_sent"], rep[0]["end"], rep[0]["identical"]), (2, 10000, True))

if __name__ == "__main__":
    unittest.main()
