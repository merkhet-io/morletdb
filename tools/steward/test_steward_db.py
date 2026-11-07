"""The steward against a real database (needs a superuser DSN and the pg_morlet extension available):

  MORLET_STEWARD_TEST_DSN=postgresql://... python -m unittest discover -s tools/steward -p 'test_steward_db.py'

Creates a database of its own (morlet_steward_test), runs the steward as the morlet_steward role, and checks that
backlogs return to 0 with an audit row for every action, that verification runs and is recorded, that the steward's
role cannot remove samples, that a failing job opens its breaker, and that the disk reserve pauses all upkeep.
"""
import os
import tempfile
import unittest

import psycopg

import policy as P
import steward
import store as S

DSN = os.environ.get("MORLET_STEWARD_TEST_DSN", "")
DB = "morlet_steward_test"


def dsn_for(db, user=None):
    info = psycopg.conninfo.conninfo_to_dict(DSN)
    info["dbname"] = db
    if user:
        info["user"] = user
        info.pop("password", None)
    return psycopg.conninfo.make_conninfo(**info)


@unittest.skipUnless(DSN, "set MORLET_STEWARD_TEST_DSN to run the steward database tests")
class StewardDatabase(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
            c.execute(f"CREATE DATABASE {DB}")
        self.admin = psycopg.connect(dsn_for(DB), autocommit=True)
        a = self.admin
        a.execute("CREATE EXTENSION pg_morlet")
        a.execute("""SELECT morlet_create_stream_collection('public','line');
          SELECT morlet_enable_envelopes('line'); SELECT morlet_enable_rollups('line');
          SELECT morlet_start_recording('line',r,1000,'x','g','{}',512,256,'zscore','none') FROM unnest(ARRAY['r1','r2']) r""")
        for r in ("r1", "r2"):
            for s in range(0, 1_100_000, 262_144):
                n = min(262_144, 1_100_000 - s)
                a.execute("SELECT morlet_append_chunks_raw('line',%s,%s,morlet_float8_le_bytes(ARRAY(SELECT "
                          "round(100*sin(i/9.0))::float8 FROM generate_series(%s,%s) i)),32768)", (r, s, s, s + n - 1))
        # one window fixes the grid (4096, stride 2048); summaries of r2 are missing (an older collection)
        a.execute("SELECT morlet_build_envelopes('line','r1',4096,2048,2)")
        a.execute("UPDATE line_chunks SET summary=NULL WHERE recording_id='r2'")
        S.init(a)
        S.grant(a)
        a.execute("ALTER ROLE morlet_steward LOGIN")
        self.conn = psycopg.connect(dsn_for(DB, "morlet_steward"), autocommit=True)
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.conn.close()
        self.admin.close()
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")

    def steward(self, **cfg):
        return steward.Steward(self.conn, P.Config(load_per_cpu=1000, **cfg), self.tmp)

    def run_until_idle(self, st, ticks=400):
        for _ in range(ticks):
            note = st.tick()
            if note.startswith("idle"):
                return note
        self.fail(f"not idle after {ticks} ticks: {note}")

    def one(self, q, args=()):
        return self.admin.execute(q, args).fetchone()[0]

    def test_backlogs_return_to_zero_with_an_audit_row_for_every_action(self):
        st = self.steward()
        self.run_until_idle(st)
        # envelopes: every full window of both recordings at stride 2048
        want = 2 * ((1_100_000 - 4096) // 2048 + 1)
        self.assertEqual(self.one("SELECT count(*) FROM line_envelopes WHERE window_size=4096"), want)
        self.assertEqual(self.one("SELECT count(*) FROM line_chunks WHERE summary IS NULL"), 0)
        self.assertGreater(self.one("SELECT count(*) FROM line_rollups"), 0)
        self.assertIsNotNone(self.one("SELECT last_pass_finished_at FROM morlet_steward.verify_cursor "
                                      "WHERE collection='public.line'"))
        self.assertEqual(self.one("SELECT last_pass_problems FROM morlet_steward.verify_cursor"), 0)
        self.assertEqual(self.one("SELECT count(*) FROM morlet_steward.actions WHERE outcome IN ('started','failed')"), 0)
        jobs = {r[0] for r in self.admin.execute("SELECT DISTINCT job FROM morlet_steward.actions")}
        self.assertTrue({"envelopes", "chunk_summaries", "rollups", "verify"} <= jobs, jobs)
        self.assertEqual(self.one("SELECT level FROM morlet_steward.overall"), "normal")
        # new samples: only the new work is done
        before = self.one("SELECT count(*) FROM morlet_steward.actions")
        self.admin.execute("SELECT morlet_append_chunks_raw('line','r1',1100000,morlet_float8_le_bytes(ARRAY(SELECT "
                           "i::float8 FROM generate_series(0,9999) i)),32768)")
        self.run_until_idle(st)
        self.assertEqual(self.one("SELECT count(*) FROM line_envelopes WHERE recording_id='r1'"),
                         (1_110_000 - 4096) // 2048 + 1)
        self.assertLess(self.one("SELECT count(*) FROM morlet_steward.actions") - before, 10)

    def test_the_steward_cannot_remove_samples(self):
        for q in ("DELETE FROM line_chunks", "UPDATE line_chunks SET encoded=encoded", "TRUNCATE line_chunks",
                  "DROP TABLE line_chunks"):
            with self.subTest(q=q), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.conn.execute(q)
        self.assertEqual(self.one("SELECT sum(sample_count) FROM line_chunks"), 2_200_000)

    def test_a_failing_job_opens_its_breaker_and_says_so(self):
        self.admin.execute("REVOKE INSERT ON line_envelopes FROM morlet_steward")
        st = self.steward()
        for _ in range(10):
            st.tick()
        self.assertTrue(self.one("SELECT open FROM morlet_steward.breakers WHERE job='envelopes'"))
        self.assertEqual(self.one("SELECT level FROM morlet_steward.status WHERE key='job:envelopes'"), "action_required")
        self.assertEqual(self.one("SELECT count(*) FROM morlet_steward.actions WHERE job='envelopes' AND outcome='failed'"), 3)
        # other jobs went on
        self.assertEqual(self.one("SELECT count(*) FROM line_chunks WHERE summary IS NULL"), 0)
        self.admin.execute("GRANT INSERT ON line_envelopes TO morlet_steward")
        self.admin.execute("GRANT morlet_approver TO CURRENT_USER")
        self.assertEqual(steward.main(["reset", "envelopes", "--dsn", dsn_for(DB)]), 0)
        self.run_until_idle(st)
        self.assertEqual(self.one("SELECT level FROM morlet_steward.overall"), "normal")

    def test_the_disk_reserve_pauses_upkeep_holds_ingest_and_releases_it(self):
        st = self.steward(reserve_bytes=1 << 62)      # more than any disk: the reserve is reached
        st.tick()
        self.assertEqual(self.one("SELECT count(*) FROM morlet_steward.actions WHERE job<>'ingest_hold'"), 0)
        self.assertEqual(self.one("SELECT set_by FROM morlet_ingest_hold"), "morlet_steward")
        with self.assertRaises(psycopg.errors.DiskFull) as e:
            self.admin.execute("SELECT morlet_append_chunk('line','r1',1100000,ARRAY[1,2,3]::float8[])")
        self.assertIn("disk reserve", str(e.exception))
        self.assertEqual(self.one("SELECT (checkpoint->>'end')::bigint FROM line WHERE recording_id='r1'"), 1_100_000)
        st.cfg = P.Config(load_per_cpu=1000)           # space is free again
        st.tick()
        self.assertIsNone(self.admin.execute("SELECT * FROM morlet_ingest_hold").fetchone())
        self.admin.execute("SELECT morlet_append_chunk('line','r1',1100000,ARRAY[1,2,3]::float8[])")
        self.assertEqual(self.one("SELECT count(*) FROM morlet_steward.actions WHERE job='ingest_hold' "
                                  "AND outcome='done'"), 2)

    def test_a_hold_set_by_a_person_stays(self):
        self.admin.execute("SELECT morlet_set_ingest_hold('maintenance window')")
        self.steward().tick()
        self.assertEqual(self.one("SELECT reason FROM morlet_ingest_hold"), "maintenance window")

    def test_the_disk_reserve_pauses_all_upkeep(self):
        st = self.steward(reserve_bytes=1 << 62)      # more than any disk: the reserve is reached
        st.tick()
        self.assertEqual(self.one("SELECT count(*) FROM morlet_steward.actions WHERE job<>'ingest_hold'"), 0)
        cause = self.one("SELECT cause FROM morlet_steward.status WHERE key='disk'")
        self.assertIn("reserve", cause)
        self.assertEqual(steward.main(["check", "--dsn", dsn_for(DB)]), 2)

    def test_interrupted_actions_are_marked_and_status_reports_a_stopped_steward(self):
        aid = S.start_action(self.conn, "envelopes", "public.line", "r1", {})
        self.assertEqual(S.mark_interrupted(self.conn), 1)
        self.assertEqual(self.one("SELECT outcome FROM morlet_steward.actions WHERE action_id=%s", (aid,)), "interrupted")
        self.assertEqual(self.one("SELECT cause FROM morlet_steward.status WHERE key='steward'"), "the steward has never run")
        self.steward().tick()
        self.assertEqual(self.one("SELECT count(*) FROM morlet_steward.status WHERE key='steward'"), 0)
        self.admin.execute("UPDATE morlet_steward.heartbeat SET at=now()-interval '1 hour'")
        self.assertIn("has not reported", self.one("SELECT cause FROM morlet_steward.status WHERE key='steward'"))


if __name__ == "__main__":
    unittest.main()
