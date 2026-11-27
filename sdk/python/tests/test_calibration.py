"""Calibration sets against a disposable PostgreSQL database; no model dependencies."""
import hashlib
import os
import subprocess
from pathlib import Path
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import psycopg
from psycopg.types.json import Jsonb
import morletdb

DSN = os.environ.get('MORLET_SDK_TEST_DSN')


@unittest.skipUnless(DSN, 'set MORLET_SDK_TEST_DSN for native calibration tests')
class Calibration(unittest.TestCase):
    def setUp(self):
        self.schema = 'cal_' + uuid.uuid4().hex[:10]
        self.conn = psycopg.connect(DSN)
        self.conn.execute('CREATE EXTENSION IF NOT EXISTS pg_morlet')
        self.conn.execute(f'CREATE SCHEMA {self.schema}')
        self.coll = self.schema + '.signals'
        self.table = self.coll + '_calibration_sets'
        self.role = self.schema + '_editor'
        self.approver = self.schema + '_approver'
        self.conn.execute(f'CREATE ROLE {self.role}')
        self.conn.execute(f'CREATE ROLE {self.approver}')
        self.db = morletdb.Client(self.conn)
        self.db.create_collection(self.coll)
        self.conn.execute('SELECT morlet_enable_context(%s::regclass)', (self.coll,))
        self.members = []
        for rid in ('a', 'b'):
            self.conn.execute("SELECT morlet_start_recording(%s::regclass,%s,1000,'x','g',%s)",
                              (self.coll, rid, Jsonb({'tenant': rid})))
            self.conn.execute("SELECT morlet_append_chunk(%s::regclass,%s,0,ARRAY['-0'::float8,0,1,2])",
                              (self.coll, rid))
            lid = self.conn.execute("SELECT morlet_add_label(%s::regclass,%s,0,4,'normal')",
                                    (self.coll, rid)).fetchone()[0]
            self.members.append(dict(recording_id=rid, start_sample=0, end_sample=4, label_id=lid))
        self.db.enable_calibration_sets(self.coll, approver_role=self.approver)
        self.conn.commit()
        self.conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute('RESET ROLE')
        self.conn.execute(f'DROP SCHEMA {self.schema} CASCADE')
        self.conn.execute(f'DROP ROLE {self.role}')
        self.conn.execute(f'DROP ROLE {self.approver}')
        self.conn.commit()
        self.conn.close()

    def put(self, members=None, **kw):
        return self.db.put_calibration_set(self.coll, 'commissioning', 1,
                                          self.members if members is None else members, **kw)

    def approve(self):
        digest = self.db.get_calibration_set(self.coll, 'commissioning', 1)['manifest_sha256']
        return self.db.approve_calibration_set(self.coll, 'commissioning', 1, expected_digest=digest, reason='reviewed')

    def resolve(self):
        return self.db.resolve_calibration_set(self.coll, 'commissioning', 1)

    def fails(self, action, pattern):
        with self.assertRaisesRegex(Exception, pattern):
            with self.conn.transaction():
                action()

    def test_frozen_manifest_retries_append_and_signed_zero(self):
        draft = self.put()
        self.assertEqual(draft, self.put(list(reversed(self.members))))
        self.assertEqual(draft['manifest_sha256'], hashlib.sha256(draft['manifest_text'].encode()).hexdigest())
        raw = self.conn.execute('SELECT morlet_read_interval_raw(%s::regclass,\'a\',0,4)', (self.coll,)).fetchone()[0]
        self.assertEqual(draft['manifest']['members'][0]['sample_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(bytes(raw[:8]), bytes(raw[8:16]))
        approved = self.approve()
        self.assertTrue(approved['approved'])
        self.assertEqual(approved, self.approve())
        self.conn.execute("SELECT morlet_append_chunk(%s::regclass,'a',4,ARRAY[3::float8])", (self.coll,))
        self.assertFalse(self.resolve()['needs_review'])
        for stmt in (f"UPDATE {self.table} SET approved=false", f"DELETE FROM {self.table}",
                     f"TRUNCATE {self.table}", f"UPDATE {self.table} SET manifest='{{}}'"):
            self.fails(lambda: self.conn.execute(stmt), 'immutable|cannot be removed')
        self.assertEqual(approved, self.db.get_calibration_set(self.coll, 'commissioning', 1))

    def refs_table(self):
        name = self.coll + '_calibration_refs'
        return name if self.conn.execute('SELECT to_regclass(%s)', (name,)).fetchone()[0] else None

    def needs_refs(self):
        if not self.refs_table():
            self.skipTest('server without calibration references (SQL 0.3.18)')

    def visible_after_reuse(self, rls_first):
        # #621: recording b is deleted and a new recording b is created for tenant a. The tenant-a reader must not
        # see the revision captured from the old b, through direct SQL or the SDK.
        if rls_first:
            self.conn.execute(f'DROP TABLE {self.table}')
            self.conn.execute(f'DROP TABLE {self.refs_table()}')
            self.conn.execute('SELECT morlet_enable_row_security(%s::regclass)', (self.coll,))
            self.db.enable_calibration_sets(self.coll, approver_role=self.approver)
        self.put([self.members[1]]); self.approve()
        self.grant_tenants()
        self.conn.commit()
        self.conn.execute(f'SET LOCAL ROLE {self.role}')
        before = self.conn.execute(f'SELECT count(*) FROM {self.table}').fetchone()[0]
        self.conn.execute('RESET ROLE'); self.conn.commit()
        self.conn.execute(f"DELETE FROM {self.coll} WHERE recording_id='b'")
        self.conn.execute("SELECT morlet_start_recording(%s::regclass,'b',1000,'x','g',%s)",
                          (self.coll, Jsonb({'tenant': 'a'})))
        self.conn.commit()
        self.conn.execute(f'SET LOCAL ROLE {self.role}')
        after = self.conn.execute(f'SELECT count(*) FROM {self.table}').fetchone()[0]
        self.fails(lambda: self.db.get_calibration_set(self.coll, 'commissioning', 1), 'unavailable')
        return before, after

    def test_evidence_does_not_follow_a_reused_recording_id(self):
        self.needs_refs()
        self.assertEqual((0, 0), self.visible_after_reuse(rls_first=False))
        # Nobody but a deletion changes a reference: the writer cannot restore or remove a cut one.
        for stmt in (f"UPDATE {self.refs_table()} SET live=recording_id", f"DELETE FROM {self.refs_table()}",
                     f"TRUNCATE {self.refs_table()}"):
            self.fails(lambda: self.conn.execute(stmt), 'maintained by MorletDB')
        self.conn.execute('RESET ROLE')
        self.assertEqual([('b', None)], self.conn.execute(f'SELECT recording_id,live FROM {self.refs_table()}').fetchall())
        self.assertEqual('unavailable', self.resolve()['members'][0]['status'])

    def test_evidence_does_not_follow_a_reused_recording_id_with_row_security_first(self):
        self.needs_refs()
        self.assertEqual((0, 0), self.visible_after_reuse(rls_first=True))
        self.conn.execute('RESET ROLE')

    def test_a_new_revision_on_the_new_recording_is_visible(self):
        self.needs_refs()
        self.grant_tenants(); self.conn.commit()
        self.conn.execute(f"DELETE FROM {self.coll} WHERE recording_id='b'")
        self.conn.execute("SELECT morlet_start_recording(%s::regclass,'b',1000,'x','g',%s)",
                          (self.coll, Jsonb({'tenant': 'a'})))
        self.conn.execute("SELECT morlet_append_chunk(%s::regclass,'b',0,ARRAY[5::float8,6,7,8])", (self.coll,))
        lid = self.conn.execute("SELECT morlet_add_label(%s::regclass,'b',0,4,'normal')", (self.coll,)).fetchone()[0]
        self.conn.commit()
        self.conn.execute(f'SET LOCAL ROLE {self.role}')
        self.db.put_calibration_set(self.coll, 'fresh', 1, [dict(recording_id='b', start_sample=0, end_sample=4, label_id=lid)])
        self.assertEqual([('fresh',)], self.conn.execute(f'SELECT set_id FROM {self.table}').fetchall())
        self.conn.execute('RESET ROLE')

    def interpretation_then_revert(self):
        self.conn.execute(f"UPDATE {self.coll} SET metadata=metadata || %s WHERE recording_id='a'",
                          (Jsonb({'calibration': {'g_per_count': 1}}),))
        self.conn.execute('SELECT morlet_enable_interpretation(%s::regclass)', (self.coll,))
        lid = self.conn.execute("SELECT morlet_add_label(%s::regclass,'a',0,4,'normal')", (self.coll,)).fetchone()[0]
        self.members[0]['label_id'] = lid
        self.put(); self.approve(); self.conn.commit()

    def test_a_reverted_correction_still_needs_review(self):
        # #620: two corrections restore the original calibration; the label was written under revision 0.
        self.needs_refs()
        self.interpretation_then_revert()
        member = self.db.get_calibration_set(self.coll, 'commissioning', 1)['manifest']['members']
        self.assertEqual([(0, 0)], [(m['interpretation_revision'], m['label_revision'])
                                    for m in member if m['recording_id'] == 'a'])
        for factor in (2, 1):
            self.db.correct_interpretation(self.coll, 'a', unit='g', calibration={'g_per_count': factor},
                                           reason='test correction')
            self.conn.commit()
        result = self.resolve()
        self.assertEqual((True, {'a': 'changed', 'b': 'unchanged'}),
                         (result['needs_review'], {m['recording_id']: m['status'] for m in result['members']}))
        self.conn.commit()

    def test_history_behind_extra_row_policies_is_unknown(self):
        self.needs_refs()
        self.interpretation_then_revert()
        self.conn.execute(f'ALTER TABLE {self.coll}_interpretation ENABLE ROW LEVEL SECURITY')
        self.conn.execute(f"CREATE POLICY hide_some ON {self.coll}_interpretation USING (reason <> 'secret')")
        self.conn.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA {self.schema} TO {self.role}')
        self.conn.execute(f'GRANT USAGE ON SCHEMA {self.schema} TO {self.role}')
        self.conn.execute(f'GRANT INSERT,UPDATE ON {self.table}, {self.refs_table()} TO {self.role}')
        self.conn.commit()
        self.conn.execute(f'SET LOCAL ROLE {self.role}')
        self.db.put_calibration_set(self.coll, 'own', 1, [self.members[0]])
        own = self.db.get_calibration_set(self.coll, 'own', 1)['manifest']['members'][0]
        self.assertEqual('unknown', own['interpretation_revision'])
        self.conn.execute('RESET ROLE')

    def test_references_cannot_be_forged_or_cut_by_hand(self):
        self.needs_refs()
        self.put(); self.grant_tenants(); self.conn.commit()
        key = self.conn.execute(f'SELECT ref_key FROM {self.table}').fetchone()[0]
        self.conn.execute(f'SET LOCAL ROLE {self.role}')
        self.fails(lambda: self.conn.execute(f"UPDATE {self.refs_table()} SET live=NULL"), 'maintained by MorletDB')
        self.fails(lambda: self.conn.execute(f"INSERT INTO {self.refs_table()} VALUES (gen_random_uuid(),'a','a')"),
                   'maintained by MorletDB')
        self.conn.execute('RESET ROLE')
        self.assertEqual(2, self.conn.execute(f'SELECT count(*) FROM {self.refs_table()} WHERE ref_key=%s AND live IS NOT NULL',
                                              (key,)).fetchone()[0])

    def test_unreadable_history_is_unverified_not_unchanged(self):
        self.needs_refs()
        self.interpretation_then_revert()
        self.grant_tenants()
        self.conn.execute(f'REVOKE SELECT ON {self.coll}_interpretation FROM {self.role}')
        self.conn.commit()
        self.conn.execute(f'SET LOCAL ROLE {self.role}')
        self.db.put_calibration_set(self.coll, 'own', 1, [self.members[0]])
        own = self.db.get_calibration_set(self.coll, 'own', 1)['manifest']['members'][0]
        self.assertEqual('unknown', own['interpretation_revision'])
        result = self.db.resolve_calibration_set(self.coll, 'own', 1)
        self.assertEqual((True, ['unverified']), (result['needs_review'], [m['status'] for m in result['members']]))
        self.conn.execute('RESET ROLE')

    def statuses(self):
        r = self.resolve()
        return r['needs_review'], [m['status'] for m in r['members']]

    def test_enabling_interpretation_later_leaves_approved_members_unchanged(self):
        # #612 review: enabling #600 adds a column to the labels; an approved set must not look changed
        if not self.db._has('public.morlet_enable_interpretation(regclass)'):
            self.skipTest('server without interpretation revisions (SQL 0.3.17)')
        self.put(); self.approve(); self.conn.commit()
        self.conn.execute('SELECT morlet_enable_interpretation(%s::regclass)', (self.coll,)); self.conn.commit()
        self.assertEqual(self.statuses(), (False, ['unchanged', 'unchanged']))
        self.conn.commit()

    def test_a_calibration_correction_after_approval_needs_review(self):
        if not self.db._has('public.morlet_enable_interpretation(regclass)'):
            self.skipTest('server without interpretation revisions (SQL 0.3.17)')
        self.conn.execute('SELECT morlet_enable_interpretation(%s::regclass)', (self.coll,)); self.conn.commit()
        self.put(); self.approve(); self.conn.commit()
        self.conn.execute("SELECT morlet_correct_interpretation(%s::regclass,'a','g',%s,'sensor swap')",
                          (self.coll, Jsonb({'g_per_count': 2})))
        self.conn.commit()
        self.assertEqual(self.statuses(), (True, ['changed', 'unchanged']))
        self.conn.commit()

    def test_correction_requires_refresh_then_new_revision(self):
        original = self.put()
        self.conn.execute(f"UPDATE {self.coll} SET metadata=metadata || '{{\"scale\":10}}' WHERE recording_id='a'")
        self.fails(self.approve, 'draft evidence changed')
        refreshed = self.put(replace_draft=True)
        self.assertNotEqual(original['manifest_sha256'], refreshed['manifest_sha256'])
        approved = self.approve()
        self.conn.execute(f"UPDATE {self.coll}_labels SET retracted=true WHERE recording_id='a'")
        self.assertTrue(self.resolve()['needs_review'])
        self.assertEqual('unavailable', self.resolve()['members'][0]['status'])
        self.assertEqual(approved['manifest'], self.db.get_calibration_set(self.coll, 'commissioning', 1)['manifest'])

    def test_missing_raw_is_not_negative_search(self):
        self.put(); self.approve()
        self.conn.execute(f"DELETE FROM {self.coll}_chunks WHERE recording_id='a'")
        self.assertEqual('unavailable', self.resolve()['members'][0]['status'])

    def test_direct_sql_generated_fields_and_validation(self):
        self.put()
        self.conn.execute(f"UPDATE {self.table} SET manifest='{{}}',recording_ids=ARRAY['forged'],created_by='forged',manifest_sha256='forged'")
        actual = self.db.get_calibration_set(self.coll, 'commissioning', 1)
        self.assertNotEqual('forged', actual['created_by'])
        self.assertEqual(['a', 'b'], actual['recording_ids'])
        self.assertEqual('morlet-calibration-v1', actual['manifest']['format'])
        self.fails(lambda: self.put([self.members[0]]), 'conflicting calibration retry')
        for members in ([], [self.members[0]] * 2, [dict(self.members[0], end_sample=1000002)],
                        [dict(self.members[0], label_id=None)], [dict(self.members[0], recording_id='hidden')]):
            self.fails(lambda: self.put(members, replace_draft=True), 'member|samples|fields')
        self.fails(lambda: self.put(decision_at='2020-01-01'), 'as-of calibration is unsupported')

    def grant_tenants(self):
        self.conn.execute(f'GRANT USAGE ON SCHEMA {self.schema} TO {self.role}, {self.approver}')
        self.conn.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA {self.schema} TO {self.role}, {self.approver}')
        self.conn.execute(f'GRANT INSERT,UPDATE,DELETE,TRUNCATE ON {self.table} TO {self.role}, {self.approver}')
        if self.refs_table():
            self.conn.execute(f'GRANT INSERT,UPDATE,DELETE,TRUNCATE ON {self.refs_table()} TO {self.role}, {self.approver}')
        self.conn.execute(f'ALTER TABLE {self.coll} ENABLE ROW LEVEL SECURITY')
        self.conn.execute(f"CREATE POLICY tenant ON {self.coll} TO {self.role},{self.approver} USING(metadata->>'tenant'='a')")
        self.conn.execute('SELECT morlet_enable_row_security(%s::regclass)', (self.coll,))

    def test_direct_rls_and_approval_authority(self):
        self.put()
        self.db.put_calibration_set(self.coll, 'own', 1, [self.members[0]])
        self.grant_tenants()
        self.conn.execute(f'SET LOCAL ROLE {self.role}')
        self.assertEqual([('own',)], self.conn.execute(f'SELECT set_id FROM {self.table}').fetchall())
        self.fails(lambda: self.db.get_calibration_set(self.coll, 'commissioning', 1), 'unavailable')
        self.fails(lambda: self.conn.execute(f"UPDATE {self.table} SET approved=true,approval_reason='fake'"), 'membership')
        self.fails(lambda: self.db.put_calibration_set(self.coll, 'steal', 1, self.members), 'unavailable')
        self.fails(lambda: self.conn.execute(f"INSERT INTO {self.table}(set_id,revision,members,approved) VALUES ('fake',1,%s,true)",
                                            (Jsonb([self.members[0]]),)), 'create a draft')
        self.conn.execute(f'SET LOCAL ROLE {self.approver}')
        digest = self.db.get_calibration_set(self.coll, 'own', 1)['manifest_sha256']
        approved = self.db.approve_calibration_set(self.coll, 'own', 1, expected_digest=digest, reason='reviewed')
        self.assertEqual(self.approver, approved['approved_by'])
        self.conn.execute(f'SET LOCAL ROLE {self.role}')
        self.fails(lambda: self.conn.execute(f"UPDATE {self.table} SET members='[]' WHERE set_id='own'"), 'immutable')
        self.conn.execute('RESET ROLE')

    def test_rls_enabled_before_calibration(self):
        # Both enablement orders, not just the helper called after table creation.
        self.conn.execute(f'DROP TABLE {self.table}')
        self.conn.execute('SELECT morlet_enable_row_security(%s::regclass)', (self.coll,))
        self.db.enable_calibration_sets(self.coll, approver_role=self.approver)
        self.assertTrue(self.conn.execute('SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass',
                                          (self.table,)).fetchone()[0])
        self.assertEqual(self.table, self.db.enable_calibration_sets(self.coll, approver_role=self.approver))
        self.fails(lambda: self.db.enable_calibration_sets(self.coll, approver_role=self.role), 'another authority')

    def test_rollback_and_retry_at_commit_boundary(self):
        self.put()
        self.conn.rollback()
        self.assertEqual(0, self.conn.execute(f'SELECT count(*) FROM {self.table}').fetchone()[0])
        self.put(); self.conn.commit()
        self.approve(); self.conn.rollback()
        self.assertFalse(self.db.get_calibration_set(self.coll, 'commissioning', 1)['approved'])
        approved = self.approve(); self.conn.commit()
        self.assertEqual(approved, self.approve())

    def test_approval_serializes_with_stale_editor(self):
        self.put(); self.conn.commit()
        with psycopg.connect(DSN) as other:
            other.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            other.execute(f'SELECT * FROM {self.table}').fetchall()  # establish old snapshot
            self.approve(); self.conn.commit()
            with self.assertRaises(psycopg.errors.SerializationFailure):
                other.execute(f"UPDATE {self.table} SET members=members")
            other.rollback()

    def test_simultaneous_approval_waits_for_edit_and_retries(self):
        self.put(); self.conn.commit()
        self.conn.execute(f'UPDATE {self.table} SET members=members')
        ready = Event()
        def competing():
            with psycopg.connect(DSN, options='-c statement_timeout=5000') as other:
                other.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                db = morletdb.Client(other)
                digest = db.get_calibration_set(self.coll, 'commissioning', 1)['manifest_sha256']
                ready.set()
                return db.approve_calibration_set(self.coll, 'commissioning', 1, expected_digest=digest, reason='reviewed')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(competing)
            self.assertTrue(ready.wait(5))
            self.conn.commit()
            try:
                result = future.result(timeout=10)
                self.assertTrue(result['approved'])
            except Exception as exc:
                self.assertTrue(isinstance(exc.__cause__, psycopg.errors.SerializationFailure) or
                                isinstance(exc, psycopg.errors.SerializationFailure), repr(exc))
                self.assertTrue(self.approve()['approved'])

    def test_read_committed_writes_refused(self):
        self.conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED
        self.fails(self.put, 'REPEATABLE READ')

    def test_reviewed_digest_and_new_revision(self):
        original = self.put()
        self.put([self.members[0]], replace_draft=True)
        self.fails(lambda: self.db.approve_calibration_set(self.coll, 'commissioning', 1,
                   expected_digest=original['manifest_sha256'], reason='reviewed'), 'digest changed')
        approved = self.approve()
        second = self.db.put_calibration_set(self.coll, 'commissioning', 2, self.members)
        self.assertFalse(second['approved'])
        self.assertEqual(approved, self.db.get_calibration_set(self.coll, 'commissioning', 1))

    def test_real_compaction_reports_unavailable(self):
        self.put(); approved = self.approve()
        self.conn.execute('SELECT morlet_enable_memory(%s::regclass)', (self.coll,))
        episode = dict(recording_id='a', start_sample=0, end_sample=4)
        eid = self.conn.execute('SELECT morlet_add_episodes(%s::regclass,%s)',
                                (self.coll, Jsonb([episode]))).fetchone()[0]
        run = self.conn.execute("SELECT morlet_start_memory_run(%s::regclass,'test','1','{}',false)",
                                (self.coll,)).fetchone()[0]
        self.conn.execute('SELECT * FROM morlet_set_level(%s::regclass,%s,%s)',
                          (self.coll, run, Jsonb([dict(episode_id=eid, level=3)])))
        self.assertTrue(self.resolve()['needs_review'])
        self.assertEqual('unavailable', self.resolve()['members'][0]['status'])
        self.assertEqual(approved['manifest'], self.db.get_calibration_set(self.coll, 'commissioning', 1)['manifest'])

    def test_dump_restore_preserves_approved_revision(self):
        self.put(); approved = self.approve(); self.conn.commit()
        pg_config = os.environ.get('PG_CONFIG', 'pg_config')
        bindir = Path(subprocess.check_output([pg_config, '--bindir'], text=True).strip())
        dump = subprocess.check_output([str(bindir / 'pg_dump'), '--dbname', DSN, '--schema', self.schema])
        self.conn.execute(f'DROP SCHEMA {self.schema} CASCADE'); self.conn.commit()
        subprocess.run([str(bindir / 'psql'), '--dbname', DSN, '-X', '-v', 'ON_ERROR_STOP=1'],
                       input=dump, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        self.assertEqual(approved, self.db.get_calibration_set(self.coll, 'commissioning', 1))
        self.assertFalse(self.resolve()['needs_review'])
        self.fails(lambda: self.conn.execute(f'UPDATE {self.table} SET approved=false'), 'immutable')

    def test_session_timezone_does_not_change_evidence(self):
        original = self.put()
        self.conn.execute("SET LOCAL TIME ZONE 'Pacific/Honolulu'")
        self.assertFalse(self.resolve()['needs_review'])
        approved = self.approve()
        self.assertEqual(original['manifest_sha256'], approved['manifest_sha256'])

    def test_concurrent_identical_create_can_retry_whole_transaction(self):
        original = self.put()
        ready = Event()
        def competing():
            with psycopg.connect(DSN, options='-c statement_timeout=5000') as other:
                other.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                other.execute(f'SELECT count(*) FROM {self.table}').fetchone()
                ready.set()
                return morletdb.Client(other).put_calibration_set(self.coll, 'commissioning', 1, self.members)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(competing)
            self.assertTrue(ready.wait(5))
            self.conn.commit()
            with self.assertRaises(Exception) as caught:
                future.result(timeout=10)
            self.assertIsInstance(caught.exception.__cause__, psycopg.errors.SerializationFailure)
        with psycopg.connect(DSN) as other:
            other.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            retry = morletdb.Client(other).put_calibration_set(self.coll, 'commissioning', 1, self.members)
            self.assertEqual(original, retry)
