"""Interpretation revisions (#600): a correction changes what samples mean without changing the samples."""
import os
import tempfile
import threading
import unittest
import uuid

import numpy as np
import psycopg
from psycopg import sql

import morletdb
from morletdb import InvalidInputError

DSN = os.environ.get("MORLET_SDK_TEST_DSN", "")
if not DSN and os.environ.get("MORLET_SDK_REQUIRE_DB") == "1":
    raise RuntimeError("MORLET_SDK_REQUIRE_DB=1 but MORLET_SDK_TEST_DSN is not set")


def wave(n=8192, shift=0):
    i = np.arange(n) + shift
    return np.sin(i / 9.0) + 0.2 * np.cos(i / 2.7) + 0.1 * np.sin(i / 53.0)


class Validation(unittest.TestCase):
    def test_refused_before_any_query(self):
        client = object.__new__(morletdb.Client)
        for bad in ({"unit": 1, "calibration": {}, "reason": "x"}, {"unit": "mV", "calibration": [], "reason": "x"},
                    {"unit": "mV", "calibration": {}, "reason": "  "}):
            with self.subTest(bad=bad), self.assertRaises(InvalidInputError):
                client.correct_interpretation("a.c", "r", **bad)
        with self.assertRaises(InvalidInputError):
            client.interpretation_review("a.c", max_rows=0)


@unittest.skipUnless(DSN, "set MORLET_SDK_TEST_DSN to run the interpretation integration tests")
class Interpretation(unittest.TestCase):
    def setUp(self):
        self.schema = "interp_" + uuid.uuid4().hex[:10]
        self.role = "interp_role_" + uuid.uuid4().hex[:10]
        self.c = f"{self.schema}.rec"
        self.admin = psycopg.connect(DSN, autocommit=True)
        self.admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(self.role)))
        self.db = morletdb.Client.connect(DSN)
        self.db.create_collection(self.c)
        self.meta = {"asset": "p1", "note": "a", "calibration": {"mv_per_count": 1}}
        for rid, shift in (("r1", 0), ("r2", 700)):
            self.db.ingest(self.c, rid, wave(shift=shift), sample_rate_hz=1000.0, channel="accel", unit="mV",
                           metadata=self.meta, search_window=512)
        self.db.connection.commit()

    def tearDown(self):
        self.db.connection.rollback()
        self.db.connection.close()
        self.admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(self.role)))
        self.admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(self.role)))
        self.admin.close()

    def commit(self):
        self.db.connection.commit()

    def label(self, rid="r1", text="below_limit"):
        return self.db.connection.execute("SELECT public.morlet_add_label(%s::regclass,%s,0,512,%s)",
                                          (self.c, rid, text)).fetchone()[0]

    def test_export_refuses_corrected_recordings_before_writing(self):
        # #609: a file copy would carry the corrected values without their history
        self.enable_all()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "copy.morlet")
            self.assertEqual(len(self.db.export_file(self.c, path, ["r1"])), 1)      # nothing corrected yet
            self.correct()
            self.commit()
            other = os.path.join(tmp, "other.morlet")
            with self.assertRaisesRegex(morletdb.UnsupportedError, "r1: corrected unit or calibration"):
                self.db.export_file(self.c, other)                                    # r1 among all recordings
            self.assertFalse(os.path.exists(other))                                   # not even created
            self.db.connection.rollback()
            self.assertEqual(len(self.db.export_file(self.c, other, ["r2"])), 1)      # the others still export
            self.commit()
            # a role that cannot read the history cannot tell, so nothing is exported
            self.admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(self.schema),
                                                                              sql.Identifier(self.role)))
            self.admin.execute(sql.SQL("GRANT SELECT ON {}.rec, {}.rec_chunks TO {}").format(
                sql.Identifier(self.schema), sql.Identifier(self.schema), sql.Identifier(self.role)))
            self.db.connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.role)))
            third = os.path.join(tmp, "third.morlet")
            with self.assertRaisesRegex(morletdb.UnsupportedError, "cannot read"):
                self.db.export_file(self.c, third, ["r2"])
            self.assertFalse(os.path.exists(third))
            self.db.connection.rollback()

    def enable_all(self):
        self.db.connection.execute("SELECT public.morlet_enable_context(%s::regclass)", (self.c,))
        self.db.enable_interpretation(self.c)
        self.commit()

    def correct(self, factor=10, rid="r1", unit="mV"):
        revision = self.db.correct_interpretation(self.c, rid, unit=unit, calibration={"mv_per_count": factor},
                                                  reason="certificate 42")
        self.commit()
        return revision

    def test_correction_keeps_samples_records_history_and_flags_labels(self):
        self.enable_all()
        before = self.db.read(self.c, "r1", 0, 8192).samples.tobytes()
        old = self.label()
        self.commit()
        self.assertEqual(self.db.interpretation_review(self.c), [])
        self.assertEqual(self.correct(), 1)
        self.assertEqual(self.db.read(self.c, "r1", 0, 8192).samples.tobytes(), before)
        rec = self.db.recording(self.c, "r1")
        self.assertEqual(rec.metadata["calibration"], {"mv_per_count": 10})
        self.assertEqual(rec.metadata["note"], "a")
        history = self.db.interpretation_history(self.c, "r1")
        self.assertEqual([(h["revision"], h["previous_calibration"], h["calibration"], h["reason"]) for h in history],
                         [(1, {"mv_per_count": 1}, {"mv_per_count": 10}, "certificate 42")])
        due = self.db.interpretation_review(self.c)
        self.assertEqual([(d["label_id"], d["status"], d["label_revision"], d["current_revision"]) for d in due],
                         [(old, "stale", 0, 1)])
        new = self.label(text="rechecked")     # written under the new revision: not due
        self.commit()
        self.assertEqual([d["label_id"] for d in self.db.interpretation_review(self.c)], [old])
        self.assertNotEqual(old, new)

    def test_identical_retry_is_a_no_op(self):
        self.enable_all()
        self.assertEqual((self.correct(), self.correct()), (1, 1))
        self.assertEqual(len(self.db.interpretation_history(self.c, "r1")), 1)

    def test_note_edit_needs_no_revision_and_flags_nothing(self):
        self.enable_all()
        self.label()
        self.db.connection.execute(sql.SQL("UPDATE {}.rec SET metadata=jsonb_set(metadata,'{{note}}','\"b\"')").format(
            sql.Identifier(self.schema)))
        self.commit()
        self.assertEqual(self.db.interpretation_history(self.c, "r1"), [])
        self.assertEqual(self.db.interpretation_review(self.c), [])

    def test_direct_sql_cannot_bypass_the_history(self):
        self.enable_all()
        self.correct()
        t = sql.Identifier(self.schema, "rec_interpretation")
        rec = sql.Identifier(self.schema, "rec")
        for statement in (sql.SQL("UPDATE {} SET metadata=jsonb_set(metadata,'{{calibration,mv_per_count}}','3')").format(rec),
                          sql.SQL("UPDATE {} SET unit='V'").format(rec),
                          sql.SQL("UPDATE {} SET reason='rewritten'").format(t),
                          sql.SQL("DELETE FROM {}").format(t),
                          sql.SQL("TRUNCATE {}").format(t)):
            with self.subTest(statement=statement.as_string(self.db.connection)), self.assertRaises(psycopg.Error):
                self.db.connection.execute(statement)
            self.db.connection.rollback()
        # a history row without the matching change fails at commit
        self.db.connection.execute(sql.SQL("INSERT INTO {}(recording_id,revision,previous_unit,unit,calibration,reason) "
                                           "VALUES ('r1',2,'x','mV','{{\"mv_per_count\":99}}','forged')").format(t))
        with self.assertRaises(psycopg.Error):
            self.db.connection.commit()
        self.db.connection.rollback()
        self.assertEqual(len(self.db.interpretation_history(self.c, "r1")), 1)

    def test_label_cannot_claim_or_rewrite_its_revision(self):
        self.enable_all()
        label = self.label()
        self.commit()
        self.correct()
        self.db.connection.execute(sql.SQL("UPDATE {} SET interpretation_revision=1").format(
            sql.Identifier(self.schema, "rec_labels")))
        self.commit()
        self.assertEqual([d["label_id"] for d in self.db.interpretation_review(self.c)], [label])

    def test_both_enablement_orders_and_labels_from_before(self):
        self.db.connection.execute("SELECT public.morlet_enable_context(%s::regclass)", (self.c,))
        earlier = self.label()
        self.commit()
        self.db.enable_interpretation(self.c)         # context first
        self.commit()
        self.correct()
        self.assertEqual([(d["label_id"], d["status"]) for d in self.db.interpretation_review(self.c)],
                         [(earlier, "unversioned")])
        other = f"{self.schema}.other"
        self.db.create_collection(other)
        self.db.ingest(other, "r1", wave(), sample_rate_hz=1000.0, channel="accel", unit="mV", metadata=self.meta,
                       search_window=512)
        self.db.enable_interpretation(other)          # interpretation first
        self.db.connection.execute("SELECT public.morlet_enable_context(%s::regclass)", (other,))
        label = self.db.connection.execute("SELECT public.morlet_add_label(%s::regclass,'r1',0,512,'x')", (other,)).fetchone()[0]
        self.commit()
        self.db.correct_interpretation(other, "r1", unit="mV", calibration={"mv_per_count": 2}, reason="r")
        self.commit()
        self.assertEqual([(d["label_id"], d["status"]) for d in self.db.interpretation_review(other)], [(label, "stale")])

    def grant(self, *extra):
        role = sql.Identifier(self.role)
        schema = sql.Identifier(self.schema)
        self.admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role))
        self.admin.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(schema, role))
        for table, privilege in extra:
            self.admin.execute(sql.SQL("GRANT {} ON {} TO {}").format(sql.SQL(privilege),
                                                                      sql.Identifier(self.schema, table), role))

    def test_non_owner_without_update_cannot_correct(self):
        self.enable_all()
        self.grant()
        self.commit()
        conn = self.db.connection
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role)))
        with self.assertRaises(psycopg.Error):
            conn.execute("SELECT public.morlet_correct_interpretation(%s::regclass,'r1','mV','{\"mv_per_count\":5}','x')",
                         (self.c,))
        conn.rollback()
        self.assertEqual(self.db.interpretation_history(self.c, "r1"), [])

    def test_labeller_without_history_access_records_unversioned(self):
        self.db.connection.execute("SELECT public.morlet_enable_context(%s::regclass)", (self.c,))
        self.db.enable_interpretation(self.c)
        self.commit()
        self.grant(("rec_labels", "INSERT"))
        self.admin.execute(sql.SQL("REVOKE SELECT ON {} FROM {}").format(
            sql.Identifier(self.schema, "rec_interpretation"), sql.Identifier(self.role)))
        conn = self.db.connection
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role)))
        label = self.label()
        self.assertEqual(conn.execute("SELECT count(*) FROM public.morlet_interpretation_revisions(%s::regclass,ARRAY['r1'])",
                                      (self.c,)).fetchone()[0], 0)
        conn.commit()
        self.correct()
        self.assertEqual([(d["label_id"], d["status"]) for d in self.db.interpretation_review(self.c)],
                         [(label, "unversioned")])

    def test_row_security_in_both_orders(self):
        self.db.connection.execute("SELECT public.morlet_enable_row_security(%s::regclass)", (self.c,))
        self.db.enable_interpretation(self.c)
        self.commit()
        self.correct(rid="r2")
        t = sql.Identifier(self.schema, "rec_interpretation")
        self.assertTrue(self.admin.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass",
                                           (f"{self.schema}.rec_interpretation",)).fetchone()[0])
        self.grant()
        self.admin.execute(sql.SQL("CREATE POLICY only_r1 ON {} FOR SELECT TO {} USING (recording_id='r1')").format(
            sql.Identifier(self.schema, "rec"), sql.Identifier(self.role)))
        self.admin.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(sql.Identifier(self.schema, "rec")))
        conn = self.db.connection
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.role)))
        self.assertEqual(conn.execute(sql.SQL("SELECT count(*) FROM {}").format(t)).fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT * FROM public.morlet_interpretation_revisions(%s::regclass,ARRAY['r1','r2'])",
                                      (self.c,)).fetchall(), [("r1", 0)])
        conn.rollback()
        other = f"{self.schema}.later"                # history first, row security second
        self.db.create_collection(other)
        self.db.enable_interpretation(other)
        self.db.connection.execute("SELECT public.morlet_enable_row_security(%s::regclass)", (other,))
        self.commit()
        self.assertTrue(self.admin.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass",
                                           (f"{self.schema}.later_interpretation",)).fetchone()[0])

    def test_deleting_a_recording_removes_its_history(self):
        self.enable_all()
        self.correct()
        rec, t = sql.Identifier(self.schema, "rec"), sql.Identifier(self.schema, "rec_interpretation")
        self.db.connection.execute(sql.SQL("DELETE FROM {} WHERE recording_id='r1'").format(rec))
        self.commit()
        self.assertEqual(self.db.connection.execute(sql.SQL("SELECT count(*) FROM {}").format(t)).fetchone()[0], 0)
        self.db.correct_interpretation(self.c, "r2", unit="mV", calibration={"mv_per_count": 4}, reason="r")
        self.db.connection.execute(sql.SQL("DELETE FROM {} WHERE recording_id='r2'").format(rec))
        self.commit()                                 # corrected and deleted in one transaction

    def test_concurrent_corrections_take_consecutive_revisions(self):
        self.enable_all()
        errors = []

        def run(factor):
            try:
                with psycopg.connect(DSN) as conn:
                    conn.execute("SELECT public.morlet_correct_interpretation(%s::regclass,'r1','mV',%s::jsonb,'race')",
                                 (self.c, '{"mv_per_count":%d}' % factor))
            except psycopg.Error as exc:
                errors.append(exc)
        threads = [threading.Thread(target=run, args=(f,)) for f in range(2, 10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        history = self.db.interpretation_history(self.c, "r1")
        self.assertEqual([h["revision"] for h in history], list(range(1, 9)))
        for before, after in zip(history, history[1:]):
            self.assertEqual(after["previous_calibration"], before["calibration"])
        self.assertEqual(self.db.recording(self.c, "r1").metadata["calibration"], history[-1]["calibration"])

    def test_appending_continues_after_a_correction(self):
        self.enable_all()
        self.correct()
        more = wave(1024, shift=8192)
        self.db.append(self.c, "r1", 8192, more)
        self.commit()
        self.assertEqual(self.db.read(self.c, "r1", 8192, 9216).samples.tobytes(), more.tobytes())
        self.assertEqual(len(self.db.interpretation_history(self.c, "r1")), 1)

    def test_triggers_hold_no_database_local_oids(self):
        # pg_dump keeps trigger arguments verbatim; OIDs change on restore (review of #605)
        self.enable_all()
        rows = self.admin.execute("SELECT tgname, tgnargs FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN "
                                  "(SELECT oid FROM pg_class WHERE relnamespace=%s::regnamespace) "
                                  "AND tgname LIKE 'morlet_%%interpretation%%'", (self.schema,)).fetchall()
        self.assertEqual(len(rows), 6)
        self.assertEqual({n for _, n in rows}, {0})

    def test_staged_revisions_must_form_one_chain(self):
        # review of #605: revision 2 staged before revision 1 was applied
        self.enable_all()
        t = sql.Identifier(self.schema, "rec_interpretation")
        conn = self.db.connection
        conn.execute(sql.SQL("INSERT INTO {}(recording_id,revision,previous_unit,unit,calibration,reason) "
                             "VALUES ('r1',1,'x','mV','{{\"mv_per_count\":2}}','one')").format(t))
        with self.assertRaises(psycopg.Error):
            conn.execute(sql.SQL("INSERT INTO {}(recording_id,revision,previous_unit,unit,calibration,reason) "
                                 "VALUES ('r1',2,'x','mV','{{\"mv_per_count\":3}}','two')").format(t))
        conn.rollback()
        self.assertEqual(self.db.interpretation_history(self.c, "r1"), [])

    def test_evidence_separates_a_calibration_change_from_a_note(self):
        self.enable_all()
        with tempfile.TemporaryDirectory() as tmp:
            case = os.path.join(tmp, "case")
            self.commit()
            bundle = self.db.export_evidence(self.c, "r1", 0, 512, include_samples=True)
            self.commit()
            bundle.write(case)
            query = next(i for i in bundle.manifest["intervals"] if i["id"] == "query")
            self.assertEqual(query["specification"]["interpretation_revision"], 0)
            self.assertEqual(bundle.manifest["investigation"]["provenance"]["interpretation_revisions"]["r1"], 0)

            def status():
                self.commit()
                out = next(i["status"] for i in self.db.resolve_evidence(case) if i["id"] == "query")
                self.commit()
                return out
            self.assertEqual(status(), "unchanged")
            self.db.connection.execute(sql.SQL("UPDATE {} SET metadata=jsonb_set(metadata,'{{note}}','\"b\"') "
                                               "WHERE recording_id='r1'").format(sql.Identifier(self.schema, "rec")))
            self.assertEqual(status(), "metadata_changed")
            self.correct(10)
            self.assertEqual(status(), "interpretation_changed")
            self.correct(1)                           # the value is back, but it is a different revision
            self.assertEqual(status(), "interpretation_changed")
            self.grant()                              # a reader who cannot see the history: unverified, not changed
            self.admin.execute(sql.SQL("REVOKE SELECT ON {} FROM {}").format(
                sql.Identifier(self.schema, "rec_interpretation"), sql.Identifier(self.role)))
            fresh = os.path.join(tmp, "fresh")
            self.commit()
            self.db.export_evidence(self.c, "r2", 0, 512, include_samples=True).write(fresh)
            self.commit()
            conn = self.db.connection
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.role)))
            conn.commit()
            try:
                statuses = {i["status"] for i in self.db.resolve_evidence(fresh)}
                self.commit()
            finally:
                conn.execute("RESET ROLE")
                conn.commit()
            self.assertEqual(statuses, {"interpretation_unverified"})


if __name__ == "__main__":
    unittest.main()
