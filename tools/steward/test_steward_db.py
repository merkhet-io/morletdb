"""The steward against a real database (needs a superuser DSN and the pg_morlet extension available):

  MORLET_STEWARD_TEST_DSN=postgresql://... python -m unittest discover -s tools/steward -p 'test_steward_db.py'

Creates a database of its own (morlet_steward_test), runs the steward as the morlet_steward role, and checks that
backlogs return to 0 with an audit row for every action, that verification runs and is recorded, that the steward's
role cannot remove samples, that a failing job opens its breaker, and that the disk reserve pauses all upkeep.
"""
import json
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

    def test_a_new_collection_gets_envelopes_without_any_built_by_hand(self):
        # soak S1: grids came only from existing envelopes, so a fresh collection never got its first one
        a = self.admin
        a.execute("""SELECT morlet_create_stream_collection('public','fresh'); SELECT morlet_enable_envelopes('fresh');
          SELECT morlet_start_recording('fresh','f1',100,'HHZ','counts','{}',2048,512,'zscore','morlet-energy-lowrate-v1')""")
        a.execute("SELECT morlet_append_chunks_raw('fresh','f1',0,morlet_float8_le_bytes(ARRAY(SELECT "
                  "round(100*sin(i/9.0))::float8 FROM generate_series(0,99999) i)),4096)")
        S.grant(a)
        self.assertEqual(self.one("SELECT count(*) FROM fresh_envelopes"), 0)
        self.run_until_idle(self.steward())
        self.assertEqual(self.one("SELECT count(*) FROM fresh_envelopes WHERE window_size=2048"), (100000 - 2048) // 512 + 1)

    def test_the_steward_cannot_remove_samples(self):
        for q in ("DELETE FROM line_chunks", "UPDATE line_chunks SET encoded=encoded", "TRUNCATE line_chunks",
                  "DROP TABLE line_chunks"):
            with self.subTest(q=q), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.conn.execute(q)
        self.assertEqual(self.one("SELECT sum(sample_count) FROM line_chunks"), 2_200_000)

    def test_verification_gets_its_share_while_the_load_gate_stays_closed(self):
        """Soak S1: on a busy edge device the load never fell below the gate and verification never ran."""
        envelopes = self.one("SELECT count(*) FROM line_envelopes")         # built by hand in the fixture
        st = steward.Steward(self.conn, P.Config(load_per_cpu=0.0, verify_share_every_s=0), self.tmp)
        for _ in range(20):
            note = st.tick()
            if self.one("SELECT last_pass_finished_at IS NOT NULL FROM morlet_steward.verify_cursor "
                        "WHERE collection='public.line'"):
                break
        self.assertIn("minimum share for integrity checks", note)
        self.assertEqual(self.one("SELECT last_pass_problems FROM morlet_steward.verify_cursor "
                                  "WHERE collection='public.line'"), 0)    # missing summaries are backlog, not damage
        jobs = {r[0] for r in self.admin.execute("SELECT DISTINCT job FROM morlet_steward.actions")}
        self.assertEqual(jobs, {"verify"})                                   # the builders still wait for the gate
        self.assertEqual(self.one("SELECT count(*) FROM line_envelopes"), envelopes)

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


@unittest.skipUnless(DSN, "set MORLET_STEWARD_TEST_DSN to run the steward database tests")
class Plans(StewardDatabase):
    """P3: nothing destructive happens without a plan another person approved, unchanged, and that person applies."""

    def setUp(self):
        super().setUp()
        a = self.admin
        a.execute("""SELECT morlet_enable_memory('line');
          SELECT morlet_add_episodes('line','[{"recording_id":"r1","start_sample":0,"end_sample":65536},
            {"recording_id":"r1","start_sample":65536,"end_sample":131072}]')""")
        for who in ("alice", "bob"):
            a.execute(f"DROP ROLE IF EXISTS {who}")
            a.execute(f"CREATE ROLE {who} LOGIN IN ROLE morlet_approver")
            a.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {who}")   # data owners of the collection
        S.init(a)        # grants on the new memory tables and plans
        S.grant(a)
        self.moves = os.path.join(self.tmp, "moves.json")
        eps = [r[0] for r in a.execute("SELECT episode_id FROM line_episodes ORDER BY start_sample")]
        with open(self.moves, "w") as f:
            json.dump({"policy": "recency", "moves": [{"episode_id": e, "level": 3} for e in eps]}, f)

    def tearDown(self):
        super().tearDown()
        with psycopg.connect(DSN, autocommit=True) as c:
            for who in ("alice", "bob"):
                c.execute(f"DROP ROLE IF EXISTS {who}")

    def as_(self, who):
        return dsn_for(DB, who)

    def propose(self):
        self.assertEqual(steward.main(["propose-levels", "line", self.moves, "--dsn", self.as_("alice")]), 0)
        return self.one("SELECT max(plan_id) FROM morlet_steward.plans")

    def test_the_steward_cannot_decide_apply_or_remove(self):
        pid = self.propose()
        for q in (f"SELECT morlet_steward.decide({pid},true)", f"SELECT morlet_steward.apply({pid})",
                  f"UPDATE morlet_steward.plans SET decision='approved' WHERE plan_id={pid}",
                  "DELETE FROM morlet_steward.plans"):
            with self.subTest(q=q), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.conn.execute(q)
        run = self.one("SELECT morlet_start_memory_run('line','x','1')")
        with self.assertRaises(psycopg.Error):
            self.conn.execute(f"SELECT * FROM morlet_set_level('line',{run},%s::jsonb)",
                              (json.dumps(json.load(open(self.moves))["moves"]),))
        self.assertEqual(self.one("SELECT sum(sample_count) FROM line_chunks"), 2_200_000)

    def test_a_plan_is_approved_by_another_person_and_applied_once(self):
        pid = self.propose()
        self.assertGreater(self.one("SELECT (evidence->>'bytes_freed')::bigint FROM morlet_steward.plans WHERE plan_id=%s",
                                    (pid,)), 0)
        self.steward().tick()
        self.assertIn("waits for a decision", self.one("SELECT cause FROM morlet_steward.status WHERE key=%s",
                                                       (f"plan:{pid}",)))
        self.assertEqual(steward.main(["approve", str(pid), "--dsn", self.as_("alice")]), 2)   # the author
        self.assertEqual(self.one("SELECT decision FROM morlet_steward.plans WHERE plan_id=%s", (pid,)), None)
        self.assertEqual(steward.main(["approve", str(pid), "--dsn", self.as_("bob")]), 0)
        self.assertEqual(steward.main(["apply", str(pid), "--dsn", self.as_("bob")]), 0)
        self.assertEqual(self.one("SELECT string_agg(level::text,',' ORDER BY start_sample) FROM line_episodes"), "3,3")
        self.assertLess(self.one("SELECT sum(sample_count) FROM line_chunks"), 2_200_000)
        self.assertEqual(steward.main(["apply", str(pid), "--dsn", self.as_("bob")]), 2)      # once
        self.steward().tick()
        self.assertEqual(self.one("SELECT count(*) FROM morlet_steward.status WHERE key=%s", (f"plan:{pid}",)), 0)

    def test_a_changed_or_expired_plan_is_refused(self):
        pid = self.propose()
        self.assertEqual(steward.main(["approve", str(pid), "--dsn", self.as_("bob")]), 0)
        # changed after approval (even with a matching hash) -> refused
        self.admin.execute("""UPDATE morlet_steward.plans SET payload=jsonb_set(payload,'{policy}','"other"'),
          sha256=encode(sha256(convert_to(jsonb_set(payload,'{policy}','"other"')::text,'UTF8')),'hex') WHERE plan_id=%s""",
                           (pid,))
        self.assertEqual(steward.main(["apply", str(pid), "--dsn", self.as_("bob")]), 2)
        with self.assertRaises(psycopg.errors.CheckViolation):    # and a payload without its hash cannot be stored
            self.admin.execute("UPDATE morlet_steward.plans SET payload='{}' WHERE plan_id=%s", (pid,))
        pid2 = self.propose()
        self.admin.execute("UPDATE morlet_steward.plans SET valid_until=now()-interval '1 s' WHERE plan_id=%s", (pid2,))
        self.assertEqual(steward.main(["approve", str(pid2), "--dsn", self.as_("bob")]), 2)
        self.assertEqual(self.one("SELECT string_agg(level::text,',') FROM line_episodes"), "0,0")

    def connect_as(self, who):
        c = psycopg.connect(self.as_(who), autocommit=True)
        self.addCleanup(c.close)
        return c

    def test_a_proposal_cannot_carry_approval_or_another_identity(self):          # #439
        ins = ("INSERT INTO morlet_steward.plans(kind,collection,payload,sha256{extra}) SELECT 'set_level','line',p,"
               "encode(sha256(convert_to(p::text,'UTF8')),'hex'){vals} FROM (SELECT '{{\"moves\":[]}}'::jsonb p) x")
        for extra, vals in ((",decision,decided_by,approved_sha256", ",'approved','bob',encode(sha256(convert_to(p::text,'UTF8')),'hex')"),
                            (",applied_at,applied_by", ",now(),'bob'"),
                            (",decided_at", ",now()")):
            with self.subTest(extra=extra), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.conn.execute(ins.format(extra=extra, vals=vals))            # as the steward
        pid = self.conn.execute(ins.format(extra=",created_by,created_at,valid_until",
                                           vals=",'bob',now()-interval '1 day',now()+interval '100 years'") +
                                " RETURNING plan_id").fetchone()[0]
        row = self.one("SELECT created_by, valid_until < now() + interval '25 hours', created_at > now() - interval '1 minute' "
                       "FROM morlet_steward.plans WHERE plan_id=%s", (pid,))
        self.assertEqual(row, "morlet_steward")                                   # the author is who inserted it
        self.assertTrue(self.one("SELECT valid_until < now() + interval '25 hours' FROM morlet_steward.plans "
                                 "WHERE plan_id=%s", (pid,)))

    def test_direct_sql_follows_the_same_rules_as_decide_and_apply(self):         # #439
        pid = self.propose()                                                      # alice is the author
        alice, bob = self.connect_as("alice"), self.connect_as("bob")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):            # self-approval
            alice.execute("UPDATE morlet_steward.plans SET decision='approved' WHERE plan_id=%s", (pid,))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):            # applying before approval
            bob.execute("UPDATE morlet_steward.plans SET applied_at=now(), applied_by='bob' WHERE plan_id=%s", (pid,))
        bob.execute("UPDATE morlet_steward.plans SET decision='approved', decided_by='carol', approved_sha256='x' "
                    "WHERE plan_id=%s", (pid,))                                   # recorded as bob, with the real hash
        self.assertEqual(self.one("SELECT decided_by, approved_sha256 = sha256 FROM morlet_steward.plans WHERE plan_id=%s",
                                  (pid,)), "bob")
        self.assertTrue(self.one("SELECT approved_sha256 = sha256 FROM morlet_steward.plans WHERE plan_id=%s", (pid,)))
        for q in ("UPDATE morlet_steward.plans SET decision='rejected' WHERE plan_id=%s",     # decided once
                  "UPDATE morlet_steward.plans SET collection='other' WHERE plan_id=%s",     # the target is fixed
                  "UPDATE morlet_steward.plans SET valid_until=now()+interval '1 year' WHERE plan_id=%s",
                  "DELETE FROM morlet_steward.plans WHERE plan_id=%s"):
            with self.subTest(q=q), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                bob.execute(q, (pid,))
        self.assertEqual(steward.main(["apply", str(pid), "--dsn", self.as_("bob")]), 0)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):            # applied once
            bob.execute("UPDATE morlet_steward.plans SET applied_at=NULL, applied_by=NULL WHERE plan_id=%s", (pid,))
        self.assertEqual(steward.main(["apply", str(pid), "--dsn", self.as_("bob")]), 2)

    def test_a_newer_extension_becomes_one_plan_not_an_update(self):
        installed = self.one("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'")
        real = S.extension_versions
        S.extension_versions = lambda conn: (installed, "999.0.0")  # a newer version the server pretends to have
        try:
            st = self.steward()
            st.tick()
            st.tick()
        finally:
            S.extension_versions = real
        self.assertEqual(self.one("SELECT count(*) FROM morlet_steward.plans WHERE kind='extension_update'"), 1)
        self.assertEqual(self.one("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'"), installed)


if __name__ == "__main__":
    unittest.main()
