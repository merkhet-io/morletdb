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


if __name__ == "__main__":
    unittest.main()
