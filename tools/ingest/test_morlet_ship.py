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
            for t in ("chunks", "segments", "envelopes", "episodes", "kept_summaries", "ledger", "memory_runs",
                      "time_anchors", "interpretation"):
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

    def anchor(self, coll, batch, rows):
        if ms.anchor_table(self.c, coll) is None:
            self.c.execute("SELECT morlet_enable_time_anchors(%s::regclass)", (coll,))
        self.c.execute("SELECT morlet_add_time_anchors(%s::regclass,%s,%s::jsonb)", (coll, batch, json.dumps(
            [{"recording_id": "spindle", "sample_offset": n, "utc_ns": str(t), "uncertainty_ns": 1000,
              "source": "daq", "break_before": n > 0} for n, t in rows])))

    def utc(self, coll, n):
        return self.c.execute("SELECT utc_ns::bigint, basis FROM morlet_sample_utc(%s::regclass,'spindle',%s)",
                              (coll, n)).fetchone()

    def test_time_anchors_ship_with_the_recording(self):                         # #564
        """The report: chunk bytes were identical, but sample 5000 mapped to 5 s instead of the anchors' 2.5 s.
        The anchors now ship before the chunks, a resend is a no-op, and the time axes are compared."""
        if self.c.execute("SELECT to_regproc('public.morlet_time_axis_version') IS NULL").fetchone()[0]:
            self.skipTest("server without time anchors (SQL 0.3.13)")
        self.c.execute("UPDATE ship_edge SET metadata = metadata || '{\"start_ns\": 0}' WHERE recording_id='spindle'")
        self.anchor("ship_edge", "b1", [(0, 0), (5000, 2_500_000_000)])
        rep = ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)[0]
        self.assertEqual(self.utc("ship_central", 5000), (2_500_000_000, "anchors"))
        self.assertEqual(self.utc("ship_central", 5000), self.utc("ship_edge", 5000))
        self.assertEqual((rep["chunks_sent"], rep["anchors_sent"], rep["identical"]), (4, 2, True))
        self.assertTrue(rep["time_axis"].startswith("anchors:"))
        rep = ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)[0]
        self.assertEqual((rep["chunks_sent"], rep["anchors_sent"], rep["identical"]), (0, 0, True))
        # a revision at the edge replaces the anchors shipped earlier (the earlier rows stay, retracted)
        aid = self.c.execute("SELECT anchor_id FROM ship_edge_time_anchors WHERE sample_offset=5000").fetchone()[0]
        self.c.execute("SELECT morlet_revise_time_anchor('ship_edge',%s,'{\"utc_ns\": 2600000000}')", (aid,))
        self.append(10000, 12500)
        rep = ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)[0]
        self.assertEqual((rep["chunks_sent"], rep["anchors_sent"], rep["identical"]), (1, 2, True))
        self.assertEqual(self.utc("ship_central", 5000), (2_600_000_000, "anchors"))
        # review: shipped anchors retracted on the target are shipped again under a new batch, before any chunk
        self.c.execute("SELECT morlet_revise_time_anchor('ship_central',x.anchor_id,retract=>true) "
                       "FROM ship_central_time_anchors x WHERE NOT x.retracted AND NOT EXISTS "
                       "(SELECT 1 FROM ship_central_time_anchors y WHERE y.supersedes=x.anchor_id)")
        self.assertEqual(self.utc("ship_central", 5000)[1], "start+rate")
        self.append(12500, 15000)
        rep = ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)[0]
        self.assertEqual((rep["chunks_sent"], rep["anchors_sent"], rep["identical"]), (1, 2, True))
        self.assertEqual(self.utc("ship_central", 5000), (2_600_000_000, "anchors"))

    def test_anchors_of_a_quoted_collection_name_ship(self):                    # #564 review
        if self.c.execute("SELECT to_regproc('public.morlet_time_axis_version') IS NULL").fetchone()[0]:
            self.skipTest("server without time anchors (SQL 0.3.13)")
        for t in ("chunks", "segments", "time_anchors"):
            self.c.execute(f'DROP TABLE IF EXISTS public."Edge X_{t}" CASCADE')
        self.c.execute('DROP TABLE IF EXISTS public."Edge X" CASCADE')
        self.c.execute("SELECT morlet_create_stream_collection('public','Edge X')")
        self.c.execute("""SELECT morlet_start_recording('"Edge X"','spindle',1000,'acc','g','{"start_ns": 0}',512,256,
            'zscore','none',0.001)""")
        self.c.execute("""SELECT morlet_append_chunks_raw('"Edge X"','spindle',0,morlet_read_interval_raw('ship_edge',
            'spindle',0,5000),2500)""")
        self.anchor('"Edge X"', "q1", [(0, 0), (2500, 1_000_000_000)])
        try:
            rep = ms.ship(self.c, self.c, 'public."Edge X"', "public.ship_central", {}, 3)[0]
            self.assertEqual((rep["anchors_sent"], rep["identical"]), (2, True))
            self.assertEqual(self.utc("ship_central", 2500), (1_000_000_000, "anchors"))
        finally:
            for t in ("chunks", "segments", "time_anchors"):
                self.c.execute(f'DROP TABLE IF EXISTS public."Edge X_{t}" CASCADE')
            self.c.execute('DROP TABLE IF EXISTS public."Edge X" CASCADE')

    def test_anchors_revised_on_the_target_are_refused_before_writing(self):      # #564
        if self.c.execute("SELECT to_regproc('public.morlet_time_axis_version') IS NULL").fetchone()[0]:
            self.skipTest("server without time anchors (SQL 0.3.13)")
        self.anchor("ship_edge", "b1", [(0, 0)])
        ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        aid = self.c.execute("SELECT anchor_id FROM ship_central_time_anchors").fetchone()[0]
        self.c.execute("SELECT morlet_revise_time_anchor('ship_central',%s,'{\"uncertainty_ns\": 5}')", (aid,))
        self.append(10000, 12500)
        before = self.central(), self.target_anchors()
        with self.assertRaisesRegex(ValueError, "anchors of its own"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.assertEqual((self.central(), self.target_anchors()), before)        # chunks and anchors unchanged

    def target_anchors(self):
        return self.c.execute("SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY anchor_id), '[]') "
                              "FROM ship_central_time_anchors x").fetchone()[0]

    def test_a_batch_named_like_a_shipment_is_not_the_shippers(self):           # #564 review
        """Anyone may choose a batch id: a target anchor added under ship:<rid>:... is refused, not retracted."""
        if self.c.execute("SELECT to_regproc('public.morlet_time_axis_version') IS NULL").fetchone()[0]:
            self.skipTest("server without time anchors (SQL 0.3.13)")
        self.anchor("ship_edge", "b1", [(0, 0)])
        self.c.execute("SELECT morlet_start_recording('ship_central','spindle',1000,'acc','g',%s,512,256,'zscore','none',0.001)",
                       (json.dumps({"machine": "M1"}),))
        for batch in ("ship:spindle:local", "ship:spindle:0123456789abcdef0123456789abcdef-0:0"):
            self.anchor("ship_central", batch, [(0, len(batch))])
            before = self.central(), self.target_anchors()
            with self.assertRaisesRegex(ValueError, "anchors of its own"):
                ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
            self.assertEqual((self.central(), self.target_anchors()), before)
            self.c.execute("SELECT morlet_revise_time_anchor('ship_central',anchor_id,retract=>true) "
                           "FROM ship_central_time_anchors x WHERE batch_id=%s", (batch,))

    def test_anchor_edits_on_the_target_wait_for_a_shipment(self):              # #564 review
        """Between the axis check and the last chunk, another session's revision of the recording's anchors waits
        (the shipper holds the anchor lock), so no chunk is committed on an axis other than the one checked."""
        import psycopg
        if self.c.execute("SELECT to_regproc('public.morlet_time_axis_version') IS NULL").fetchone()[0]:
            self.skipTest("server without time anchors (SQL 0.3.13)")
        self.anchor("ship_edge", "b1", [(0, 0)])
        other = psycopg.connect(URL, autocommit=True)
        tried = []

        class During:                                    # tries a revision from another session mid-shipment
            def __init__(self, conn):
                self.conn = conn

            def execute(self, q, *a, **k):
                if "morlet_append_encoded_chunk" in q and not tried:
                    aid = self.conn.execute("SELECT anchor_id FROM ship_central_time_anchors").fetchone()[0]
                    try:
                        with other.transaction():
                            other.execute("SET LOCAL lock_timeout = '300ms'")
                            other.execute("SELECT morlet_revise_time_anchor('ship_central',%s,"
                                          "'{\"uncertainty_ns\": 7}')", (aid,))
                        tried.append("revised")
                    except psycopg.errors.LockNotAvailable:
                        tried.append("waited")
                return self.conn.execute(q, *a, **k)

            def transaction(self):
                return self.conn.transaction()
        try:
            rep = ms.ship(self.c, During(self.c), "public.ship_edge", "public.ship_central", {}, 3)[0]
        finally:
            other.close()
        self.assertEqual(tried, ["waited"])
        self.assertTrue(rep["identical"])
        # after the shipment the lock is released: a revision goes through
        aid = self.c.execute("SELECT anchor_id FROM ship_central_time_anchors").fetchone()[0]
        self.c.execute("SELECT morlet_revise_time_anchor('ship_central',%s,'{\"uncertainty_ns\": 7}')", (aid,))

    def test_target_anchors_the_source_lacks_are_refused_before_writing(self):    # #564
        if self.c.execute("SELECT to_regproc('public.morlet_time_axis_version') IS NULL").fetchone()[0]:
            self.skipTest("server without time anchors (SQL 0.3.13)")
        self.c.execute("SELECT morlet_start_recording('ship_central','spindle',1000,'acc','g',%s,512,256,'zscore','none',0.001)",
                       (json.dumps({"machine": "M1"}),))
        self.anchor("ship_central", "c1", [(0, 7)])
        with self.assertRaisesRegex(ValueError, "anchors of its own"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.assertEqual(self.central(), ("0", 0))

    def interpretation(self):
        if self.c.execute("SELECT to_regproc('public.morlet_enable_interpretation') IS NULL").fetchone()[0]:
            self.skipTest("server without interpretation revisions (SQL 0.3.17)")
        self.c.execute("SELECT morlet_enable_interpretation('ship_edge')")

    def correct(self):
        self.c.execute("SELECT morlet_correct_interpretation('ship_edge','spindle','g','{\"g_per_count\":2}','swap')")

    def test_a_corrected_recording_is_refused_before_writing(self):               # #609
        self.interpretation()                       # enabled, nothing corrected: ships as before
        self.assertEqual(ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)[0]["end"], 10000)
        self.correct()
        self.append(10000, 15000)
        with self.assertRaisesRegex(ValueError, "spindle: corrected unit or calibration"):
            ms.ship(self.c, self.c, "public.ship_edge", "public.ship_central", {}, 3)
        self.assertEqual(self.central(), ("10000", 4))                       # nothing more written

    def test_export_refuses_a_corrected_recording_before_writing(self):          # #609
        try:
            import morlet_export as me
        except ImportError:
            self.skipTest("pyarrow not installed")
        import tempfile
        from pathlib import Path
        self.interpretation()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "x.parquet"
            self.assertEqual(me.export(URL, "public.ship_edge", out)["recordings"], 1)
            out.unlink()
            self.correct()
            with self.assertRaisesRegex(ValueError, "spindle: corrected unit or calibration"):
                me.export(URL, "public.ship_edge", out)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), [])     # no file, no partial file

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
