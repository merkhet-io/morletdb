"""Investigation contract, including real transaction boundaries and caller-scoped evidence."""
import json
import os
import unittest
import uuid
from unittest.mock import patch

import numpy as np
import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

import morletdb
from morletdb import InvalidInputError, InvestigationLimitError
from morletdb._investigation import _Reader

DSN = os.environ.get("MORLET_SDK_TEST_DSN", "")
if not DSN and os.environ.get("MORLET_SDK_REQUIRE_DB") == "1":
    raise RuntimeError("MORLET_SDK_REQUIRE_DB=1 but MORLET_SDK_TEST_DSN is not set")


class Validation(unittest.TestCase):
    def test_invalid_settings_need_no_connection(self):
        client = object.__new__(morletdb.Client)
        for args in ({"timeout_ms": True}, {"top_k": 0}, {"top_k": 101}, {"max_lag_fraction": float("nan")},
                     {"segment_id": -1}, {"match_keys": "tool"}, {"exclude_labels": ("fault",)},
                     {"metadata_filter": {"bad": object()}}, {"metadata_filter": []},
                     {"max_lag_fraction": 0.6}, {"max_output_bytes": 0}):
            with self.subTest(args=args), self.assertRaises(InvalidInputError):
                client.investigate("signals", "query", 0, 512, **args)
        for start, end in ((-1, 512), (True, 512), (0.5, 512), (0, 513), (0, 65536)):
            with self.subTest(start=start, end=end), self.assertRaises(InvalidInputError):
                client.investigate("signals", "query", start, end)


@unittest.skipUnless(DSN, "set MORLET_SDK_TEST_DSN to run the investigation integration tests")
class Investigation(unittest.TestCase):
    def setUp(self):
        self.schema = "investigate_" + uuid.uuid4().hex[:10]
        self.conn = psycopg.connect(DSN)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.db = morletdb.Client(self.conn)
        self.coll = self.schema + ".signals"
        self.db.create_collection(self.coll)
        t = np.arange(1024)
        self.wave = np.round((1 + np.sin(t / 70)) * np.sin(t / 5), 4)
        for rid in ("query", "prior", "hidden"):
            self.db.ingest(self.coll, rid, self.wave, sample_rate_hz=1000, channel="x", unit="V",
                           metadata={"site": "secret" if rid == "hidden" else "public"}, search_window=512)
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute("RESET ROLE")
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()

    def run_query(self, **kwargs):
        return self.db.investigate(self.coll, "query", 0, 512, **kwargs)

    def test_matches_stats_and_scope_match_sql(self):
        result = self.run_query(metadata_filter={"site": "public"})
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.IDLE)
        direct = self.db.search(self.coll, "query", 0, 512, metadata_filter={"site": "public"})
        self.assertEqual(result.search, direct)
        self.assertEqual(result.query_stats, self.db.stats(self.coll, "query", 0, 512))
        self.assertEqual({r.recording_id for r in result.coverage}, {"query", "prior"})
        self.assertEqual(result.coverage_status, "available")
        self.assertEqual(result.provenance["isolation"], "repeatable read")
        self.assertEqual(result.version, 1)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_rejects_existing_write_transaction_without_committing(self):
        self.conn.execute(f"UPDATE {self.coll} SET metadata=metadata || '{{\"pending\":true}}' WHERE recording_id='query'")
        with self.assertRaisesRegex(InvalidInputError, "existing transaction"):
            self.run_query()
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.INTRANS)
        self.assertTrue(self.db.recording(self.coll, "query").metadata["pending"])
        self.conn.rollback()
        self.assertNotIn("pending", self.db.recording(self.coll, "query").metadata)

    def test_rejects_read_committed_readonly(self):
        self.conn.execute("BEGIN READ ONLY")
        with self.assertRaisesRegex(InvalidInputError, "REPEATABLE READ"):
            self.run_query()
        self.assertEqual(self.conn.execute("SELECT 1").fetchone()[0], 1)

    def test_existing_snapshot_and_timeout_preserved_on_success_and_failure(self):
        self.conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.conn.execute("SET LOCAL statement_timeout='2s'")
        self.run_query()
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.INTRANS)
        self.assertEqual(self.conn.execute("SHOW statement_timeout").fetchone()[0], "2s")
        with self.assertRaises(InvestigationLimitError):
            self.run_query(max_output_bytes=1)
        self.assertEqual(self.conn.execute("SHOW statement_timeout").fetchone()[0], "2s")
        self.assertEqual(self.conn.execute("SELECT 1").fetchone()[0], 1)

    def test_limits_fail_without_partial_result(self):
        for kwargs in ({"max_recordings": 1}, {"max_coverage_ranges": 1},
                       {"max_candidates": 1}, {"max_output_bytes": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvestigationLimitError):
                self.run_query(**kwargs)
            self.assertEqual(self.conn.info.transaction_status, TransactionStatus.IDLE)

    def test_timeout_cancels_sql_and_preserves_outer_transaction(self):
        self.conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.conn.execute("SET LOCAL statement_timeout='5s'")
        def slow(reader, *args):
            reader._execute("SELECT pg_sleep(1)")
        with patch("morletdb._investigation._collect", slow):
            with self.assertRaises(InvestigationLimitError):
                self.run_query(timeout_ms=50)
        self.assertEqual(self.conn.execute("SHOW statement_timeout").fetchone()[0], "5s")

    def test_deadline_is_shared_across_stages(self):
        def slow(reader, *args):
            reader._execute("SELECT pg_sleep(0.15)")
            reader._execute("SELECT pg_sleep(0.15)")
            self.fail("each stage must not get a fresh investigation timeout")
        with patch("morletdb._investigation._collect", slow), self.assertRaises(InvestigationLimitError):
            self.run_query(timeout_ms=250)
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.IDLE)

    def test_autocommit_connection_is_preserved(self):
        self.conn.autocommit = True
        self.run_query()
        self.assertTrue(self.conn.autocommit)
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.IDLE)

    def test_probe_router_works_in_readonly_snapshot(self):
        if not self.conn.execute("SELECT 1 FROM pg_available_extensions WHERE name='vector'").fetchone():
            self.skipTest("pgvector is unavailable for the optional probe-router check")
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.conn.execute("SELECT morlet_enable_envelope_probes(%s::regclass,512)", (self.coll,))
        self.conn.commit()
        result = self.run_query()
        self.assertEqual(result.search.mode, "probes")
        self.assertTrue(any("small galleries may route to exact" in n for n in result.notes))
        direct = self.db.search(self.coll, "query", 0, 512)
        self.assertEqual(result.search, direct)
        self.conn.rollback()
        with self.assertRaises(InvestigationLimitError):
            self.run_query(max_candidates=1)

    def test_serializable_caller_snapshot_remains_open(self):
        self.conn.execute("BEGIN ISOLATION LEVEL SERIALIZABLE READ ONLY")
        result = self.run_query()
        self.assertEqual(result.provenance["isolation"], "serializable")
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.INTRANS)

    def test_snapshot_does_not_see_append_between_stages(self):
        original = _Reader.search
        def concurrent(reader, *args, **kwargs):
            with psycopg.connect(DSN) as writer:
                morletdb.Client(writer).ingest(self.coll, "late", self.wave,
                    sample_rate_hz=1000, channel="x", unit="V", search_window=512)
            return original(reader, *args, **kwargs)
        with patch.object(_Reader, "search", concurrent):
            result = self.run_query(top_k=100)
        self.assertNotIn("late", {m.ref.recording_id for m in result.search.matches})
        self.assertNotIn("late", {r.recording_id for r in result.coverage})
        self.assertIn("late", {m.ref.recording_id for m in self.run_query(top_k=100).search.matches})

    def test_unknown_coverage_is_explicit(self):
        original = _Reader._has
        def has(reader, signature):
            return False if "morlet_coverage(" in signature else original(reader, signature)
        with patch.object(_Reader, "_has", has):
            result = self.run_query()
        self.assertEqual(result.coverage_status, "unknown")
        self.assertEqual(result.coverage, [])
        self.assertTrue(any("unknown" in n for n in result.notes))

    def test_empty_search_does_not_hide_unindexed_recordings(self):
        self.conn.execute(f"DELETE FROM {self.coll}_envelopes")
        self.conn.commit()
        result = self.run_query()
        self.assertEqual(result.search.matches, [])
        self.assertEqual({r.state for r in result.coverage}, {"not_indexed"})
        self.assertTrue(any("not directly searchable" in n for n in result.notes))

    def test_missing_query_samples_raise_instead_of_returning_partial_report(self):
        self.conn.execute(f"DELETE FROM {self.coll}_chunks WHERE recording_id='query'")
        self.conn.commit()
        with self.assertRaises(morletdb.MorletError):
            self.run_query()
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.IDLE)

    def add_context(self):
        self.conn.execute("SELECT morlet_enable_context(%s::regclass)", (self.coll,))
        for rid in ("query", "prior", "hidden"):
            rows = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": 1024}
                    for k, v in (("tool", 7), ("part", rid))]
            self.conn.execute("SELECT morlet_add_context(%s::regclass,%s,%s)", (self.coll, rid, Jsonb(rows)))
            self.conn.execute("SELECT morlet_build_context_segments(%s::regclass,%s,ARRAY['tool'],ARRAY['part'])",
                              (self.coll, rid))
            self.conn.execute("SELECT morlet_build_context_values(%s::regclass,%s)", (self.coll, rid))
        self.conn.execute("SELECT morlet_add_label(%s::regclass,'prior',0,1024,'reviewed','observed')", (self.coll,))
        self.conn.execute("SELECT morlet_define_label(%s::regclass,'reviewed','reviewed example')", (self.coll,))
        sid = self.conn.execute(f"SELECT segment_id FROM {self.coll}_ctx_segments WHERE recording_id='query'").fetchone()[0]
        self.conn.commit()
        return sid

    def test_context_comparison_and_label_definitions(self):
        sid = self.add_context()
        result = self.run_query(segment_id=sid, min_history=3)
        self.assertEqual(result.context.context, {"tool": 7})
        self.assertEqual(result.context.ref.end_sample, 1024)
        self.assertEqual(result.context.label_definitions[0]["definition"], "reviewed example")
        self.assertTrue(all(r["hist_n"] == 2 for r in result.context.comparisons))
        self.assertTrue(any("Insufficient" in note for note in result.notes))
        cur = self.conn.execute("SELECT * FROM morlet_compare_to_history(%s::regclass,%s,min_history=>3)",
                                (self.coll, sid))
        names = [c.name for c in cur.description]
        self.assertEqual(result.context.comparisons, [dict(zip(names, row)) for row in cur.fetchall()])

    def test_snapshot_keeps_label_revision_consistent(self):
        sid = self.add_context()
        original = _Reader.search
        def concurrent(reader, *args, **kwargs):
            with psycopg.connect(DSN) as writer:
                writer.execute(f"UPDATE {self.coll}_labels SET retracted=true WHERE label='reviewed'")
                writer.execute("SELECT morlet_define_label(%s::regclass,'reviewed','revised definition')", (self.coll,))
            return original(reader, *args, **kwargs)
        with patch.object(_Reader, "search", concurrent):
            result = self.run_query(segment_id=sid)
        self.assertEqual(result.context.label_definitions[0]["definition"], "reviewed example")
        self.assertTrue(any(n["labels"] for n in result.context.neighbors))
        self.assertEqual(self.run_query(segment_id=sid).context.label_definitions, [])

    def test_segment_must_belong_to_query(self):
        self.add_context()
        sid = self.conn.execute(f"SELECT segment_id FROM {self.coll}_ctx_segments WHERE recording_id='prior'").fetchone()[0]
        self.conn.commit()
        with self.assertRaisesRegex(InvalidInputError, "contain the entire"):
            self.run_query(segment_id=sid)

    def test_non_owner_rls_and_role_changes(self):
        self.check_rls(context_first=True)

    def test_rls_enabled_before_context(self):
        self.check_rls(context_first=False)

    def check_rls(self, context_first):
        sid = self.add_context() if context_first else None
        role = self.schema + "_reader"
        self.conn.execute(f"CREATE ROLE {role}")
        try:
            self.conn.execute(f"GRANT USAGE ON SCHEMA {self.schema} TO {role}")
            self.conn.execute(f"ALTER TABLE {self.coll} ENABLE ROW LEVEL SECURITY")
            self.conn.execute(f"CREATE POLICY scope ON {self.coll} USING (metadata->>'site'='public')")
            self.conn.execute("SELECT morlet_enable_row_security(%s::regclass)", (self.coll,))
            if not context_first:
                sid = self.add_context()
            self.conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {self.schema} TO {role}")
            self.conn.commit()
            owner = self.run_query(segment_id=sid)
            self.assertIn("hidden", {r.recording_id for r in owner.coverage})
            self.conn.execute(f"SET ROLE {role}")
            self.conn.commit()
            result = self.run_query(segment_id=sid)
            self.assertNotIn("hidden", {r.recording_id for r in result.coverage})
            self.assertNotIn("hidden", {m.ref.recording_id for m in result.search.matches})
            self.assertTrue(all(r["hist_n"] == 1 for r in result.context.comparisons))
            self.assertNotIn("hidden", {n["recording_id"] for n in result.context.neighbors})
            with self.assertRaises(morletdb.NotFoundError):
                self.db.investigate(self.coll, "hidden", 0, 512)
        finally:
            self.conn.rollback()
            self.conn.execute("RESET ROLE")
            self.conn.execute(f"DROP OWNED BY {role}")
            self.conn.execute(f"DROP ROLE {role}")
            self.conn.commit()


if __name__ == "__main__":
    unittest.main()
