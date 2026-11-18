"""morlet_ship.py against a database (#420): ship, resume, identical bytes. Needs MORLET_MCP_TEST_DATABASE_URL
(a database where the role may create tables, with pg_morlet SQL 0.3.9)."""
import json
import os
import unittest

import morlet_ship as ms

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")


class Conn:
    """Answers morlet_encoded_chunks from a list of (start, count, bytes) per collection, without a database."""
    def __init__(self, stored):
        self.stored = stored

    def execute(self, _q, args):
        coll, _rid, start, page = args
        rows = [r for r in self.stored[coll] if r[0] >= start][:page]
        return type("Cursor", (), {"fetchall": lambda _self: rows})()


class SamePrefix(unittest.TestCase):                                         # #495
    def check(self, src, tgt, end):
        return ms.same_prefix(Conn({"s": src}), Conn({"t": tgt}), "s", "t", "r", end)

    def test_one_missing_trailing_chunk_is_a_difference_in_either_direction(self):
        two = [(0, 4, b"a"), (4, 4, b"b")]
        self.assertTrue(self.check(two, two, 8))
        self.assertFalse(self.check(two, two[:1], 8))       # the target lacks its last chunk (compacted)
        self.assertFalse(self.check(two[:1], two, 8))       # the source lacks its last chunk
        self.assertFalse(self.check(two, [], 8))
        self.assertFalse(self.check([], two, 8))
        self.assertTrue(self.check([], [], 0))


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class Shipping(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.c = psycopg.connect(URL, autocommit=True)
        if self.c.execute("SELECT to_regproc('public.morlet_append_encoded_chunk') IS NULL").fetchone()[0]:
            self.skipTest("server without chunk shipping (SQL 0.3.9)")
        for coll in ("ship_edge", "ship_central"):
            for t in ("chunks", "segments", "envelopes", "episodes", "kept_summaries", "ledger", "memory_runs"):
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

    def tail(self, coll, start, values):
        self.c.execute(f"SELECT morlet_append_chunks_raw('{coll}','tail',%s::bigint,morlet_float8_le_bytes(%s::float8[]),4)",
                       (start, values))

    def compact_last_chunk(self, coll):
        """Remove the chunk [4, 8) of 'tail' with the supported memory API (level 4); the checkpoint stays at 8."""
        self.c.execute(f"SELECT morlet_enable_memory('{coll}')")
        self.c.execute(f"DELETE FROM {coll}_episodes")
        self.c.execute(f"""SELECT morlet_set_level('{coll}',morlet_start_memory_run('{coll}','retention','1'),
            jsonb_build_array(jsonb_build_object('episode_id',e,'level',4)))
          FROM morlet_add_episodes('{coll}','[{{"recording_id":"tail","start_sample":4,"end_sample":8}}]') e""")

    def state(self, coll):
        return self.c.execute(f"SELECT checkpoint->>'end', ARRAY(SELECT start_sample FROM {coll}_chunks "
                              f"WHERE recording_id='tail' ORDER BY start_sample) FROM {coll} "
                              f"WHERE recording_id='tail'").fetchone()

    def two_chunks_on_both_sides(self):
        for coll in ("ship_edge", "ship_central"):
            self.c.execute(f"SELECT morlet_start_recording('{coll}','tail',1000,'acc','g',%s,512,256,'zscore','none',0.001)",
                           (json.dumps({"machine": "M9"}),))
            self.tail(coll, 0, [1.0, 2.0, 3.0, 4.0])
            self.tail(coll, 4, [5.0, 6.0, 7.0, 8.0])

    def test_a_target_missing_its_compacted_last_chunk_is_refused_before_writing(self):   # #495
        self.two_chunks_on_both_sides()
        self.compact_last_chunk("ship_central")
        self.assertEqual(self.state("ship_central"), ("8", [0]))
        self.tail("ship_edge", 8, [9.0, 10.0, 11.0, 12.0])
        with self.assertRaisesRegex(ValueError, "not the source's chunks; nothing was written"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {"machine": "M9"}, 3)
        self.assertEqual(self.state("ship_central"), ("8", [0]))              # no chunk at 8, checkpoint not advanced

    def test_a_source_missing_its_compacted_last_chunk_is_refused_before_writing(self):   # #495, the mirror
        self.two_chunks_on_both_sides()
        self.compact_last_chunk("ship_edge")
        self.tail("ship_edge", 8, [9.0, 10.0, 11.0, 12.0])
        self.assertEqual(self.state("ship_edge"), ("12", [0, 8]))
        with self.assertRaisesRegex(ValueError, "not the source's chunks; nothing was written"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {"machine": "M9"}, 3)
        self.assertEqual(self.state("ship_central"), ("8", [0, 4]))

    def test_a_gap_in_the_source_is_refused_before_the_batch_is_written(self):
        self.c.execute("SELECT morlet_start_recording('ship_edge','tail',1000,'acc','g',%s,512,256,'zscore','none',0.001)",
                       (json.dumps({"machine": "M9"}),))
        for start in (0, 4, 8):
            self.tail("ship_edge", start, [1.0, 2.0, 3.0, 4.0])
        self.c.execute("DELETE FROM ship_edge_chunks WHERE recording_id='tail' AND start_sample=4")  # not via memory
        with self.assertRaisesRegex(ValueError, "no chunk at 4"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {"machine": "M9"}, 3)
        self.assertEqual(self.state("ship_central"), ("0", []))


if __name__ == "__main__":
    unittest.main()
