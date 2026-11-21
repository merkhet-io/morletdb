"""Agent-facing investigation contract, through registered MCP calls and the stdio transport."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

import morletdb
from morletdb._investigation import _Reader
import morlet_mcp as m

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")


def call(**kwargs):
    try:
        return asyncio.run(m.mcp.call_tool("investigate_interval", kwargs))
    except ToolError as exc:
        # The direct server API raises; the stdio transport serializes this as an error result.
        # test_isolated_stdio_transport exercises that boundary independently.
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=str(exc))])


class Errors(unittest.TestCase):
    def test_probe_candidates_do_not_claim_complete_coverage(self):
        # the states and the coverage summary are the SDK's; present() passes them through (#577 review)
        states = ["no_temporal_cutoff", "no_matches_returned", "approximate_candidates_possible", "coverage_unknown"]
        report = SimpleNamespace(to_dict=lambda: {
            "coverage": [], "coverage_status": "unknown", "search": {"mode": "probes", "matches": []},
            "context": None, "evidence_states": list(states),
            "coverage_summary": {"samples_by_state": {}, "range_count": 0, "ranges_included": False}})
        result = m._sibling("investigation").present(report, False)
        self.assertEqual(result["scope"]["candidate_completeness"], "unknown")
        self.assertEqual(result["evidence_states"], states)
        self.assertNotIn("coverage_summary", result)

    def test_registration_schema_and_readonly_annotation(self):
        tool = next(t for t in asyncio.run(m.mcp.list_tools()) if t.name == "investigate_interval")
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertFalse(tool.annotations.destructive_hint)
        self.assertEqual(tool.input_schema["properties"]["top_k"]["default"], 5)

    def test_invalid_requests_are_tool_errors_with_recovery(self):
        base = dict(collection="public.signals", recording_id="query", start_sample=0, end_sample=512)
        for options in ({"top_k": 21}, {"metadata_filter": "[]"}, {"max_candidates": 10001}):
            with self.subTest(options=options):
                result = call(**base, **options)
                self.assertTrue(result.is_error)
                self.assertIn('"code": "invalid_input"', result.content[0].text)
                self.assertIn('"suggestion"', result.content[0].text)

    def test_missing_sdk_does_not_disable_other_tools(self):
        with patch.dict(sys.modules, {"morletdb": None}):
            result = call(collection="public.signals", recording_id="q", start_sample=0, end_sample=512)
        self.assertTrue(result.is_error)
        self.assertIn('"code": "sdk_unavailable"', result.content[0].text)
        self.assertTrue(any(t.name == "search_similar" for t in asyncio.run(m.mcp.list_tools())))

    def test_invalid_operator_limit_is_actionable(self):
        with patch.dict(os.environ, {"MORLET_MCP_INVESTIGATION_TIMEOUT_MS": "0"}):
            result = call(collection="public.signals", recording_id="q", start_sample=0, end_sample=512)
        self.assertTrue(result.is_error)
        self.assertIn('"code": "configuration_error"', result.content[0].text)


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class InvestigationOnDatabase(unittest.TestCase):
    def setUp(self):
        self.schema = "mcp_inv_" + uuid.uuid4().hex[:10]
        self.coll = self.schema + ".signals"
        self.conn = psycopg.connect(URL)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.db = morletdb.Client(self.conn)
        self.db.create_collection(self.coll)
        t = np.arange(1024)
        self.wave = np.round((1 + np.sin(t / 70)) * np.sin(t / 5), 4)
        for rid in ("query", "prior", "secret"):
            self.db.ingest(self.coll, rid, self.wave, sample_rate_hz=1000, channel="x", unit="V",
                           metadata={"site": "secret" if rid == "secret" else "public"}, search_window=512)
        self.conn.commit()
        self.env = patch.dict(os.environ, {"DATABASE_URL": URL})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.conn.rollback()
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()

    def request(self, **kwargs):
        return call(collection=self.coll, recording_id="query", start_sample=0, end_sample=512, **kwargs)

    def payload(self, **kwargs):
        result = self.request(**kwargs)
        self.assertFalse(result.is_error, result.content[0].text)
        return json.loads(result.content[0].text)

    def test_compact_result_matches_sdk_and_has_explicit_states(self):
        result = self.payload()
        direct = self.db.investigate(self.coll, "query", 0, 512, top_k=5).to_dict()
        self.assertEqual(result["search"], direct["search"])
        self.assertEqual(result["query_stats"], direct["query_stats"])
        self.assertEqual(result["provenance"]["isolation"], "repeatable read")
        self.assertIn("context_not_requested", result["evidence_states"])
        self.assertIn("no_temporal_cutoff", result["evidence_states"])
        self.assertNotIn("ranges", result["scope"])
        self.assertEqual(result["scope"]["samples_by_state"], {"searchable": 3072})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True, indent=2).encode()), 64000)
        detailed = self.payload(detailed_coverage=True)
        self.assertTrue(detailed["scope"]["ranges_included"])
        self.assertEqual(len(detailed["scope"]["ranges"]), detailed["scope"]["range_count"])

    def test_operator_limits_reach_the_sdk_and_are_reported(self):                # #577 review
        seen = {}
        real = morletdb.Client.investigate

        def spy(client, *args, **kwargs):
            seen.update(kwargs)
            return real(client, *args, **kwargs)
        env = {"MORLET_MCP_INVESTIGATION_MAX_RECORDINGS": "50", "MORLET_MCP_INVESTIGATION_MAX_COVERAGE_RANGES": "70",
               "MORLET_MCP_INVESTIGATION_MAX_HISTORY": "90"}
        with patch.dict(os.environ, env), patch.object(morletdb.Client, "investigate", spy):
            result = self.payload(max_candidates=100)
        self.assertEqual((seen["max_recordings"], seen["max_coverage_ranges"], seen["max_history"],
                          seen["max_candidates"], seen["coverage_ranges"]), (50, 70, 90, 100, False))
        self.assertEqual({k: result["tool_limits"][k] for k in ("max_recordings", "max_coverage_ranges", "max_history")},
                         {"max_recordings": 50, "max_coverage_ranges": 70, "max_history": 90})
        with patch.dict(os.environ, {"MORLET_MCP_INVESTIGATION_MAX_RECORDINGS": "1"}):
            result = self.request()
        self.assertIn('"code": "budget_exceeded"', result.content[0].text)
        self.assertTrue(self.request(max_history=0).is_error)

    def test_the_server_timeout_is_read_as_connect_reads_it(self):              # #577 review
        for value in ("0", "600000"):
            with self.subTest(value=value), patch.dict(os.environ, {"MORLET_MCP_TIMEOUT_MS": value}):
                self.assertEqual(self.payload()["tool_limits"]["timeout_ms"], 10000)

    def test_the_size_bound_counts_what_is_sent(self):                          # #577 review
        result = self.payload()
        text = len(json.dumps(result, ensure_ascii=True, indent=2).encode())
        with patch.dict(os.environ, {"MORLET_MCP_INVESTIGATION_MAX_OUTPUT_BYTES": str(text + 50)}):
            refused = self.request()
        self.assertTrue(refused.is_error)                  # the indented copy alone fits; with the structured copy not
        self.assertIn('"code": "response_too_large"', refused.content[0].text)

    def test_an_sdk_size_limit_is_response_too_large(self):                     # #577 review
        def big(*_a, **_k):
            raise morletdb.InvestigationLimitError("investigation exceeds max_output_bytes; narrow scope or reduce top_k")
        with patch.object(morletdb.Client, "investigate", big):
            result = self.request()
        self.assertIn('"code": "response_too_large"', result.content[0].text)

    def test_readonly_is_set_before_discovery(self):
        original = m.checked
        def checked(conn, collection):
            self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()[0], "on")
            self.assertEqual(conn.execute("SHOW transaction_isolation").fetchone()[0], "repeatable read")
            return original(conn, collection)
        with patch.object(m, "checked", checked):
            self.payload()

    def test_timeout_and_size_limits_are_errors_not_empty_matches(self):
        original = _Reader.search
        def slow(reader, *args, **kwargs):
            reader._execute("SELECT pg_sleep(1)")
            return original(reader, *args, **kwargs)
        with patch.dict(os.environ, {"MORLET_MCP_INVESTIGATION_TIMEOUT_MS": "50"}), patch.object(_Reader, "search", slow):
            result = self.request()
        self.assertTrue(result.is_error)
        self.assertIn('"code": "budget_exceeded"', result.content[0].text)
        with patch.dict(os.environ, {"MORLET_MCP_INVESTIGATION_MAX_OUTPUT_BYTES": "100"}):
            result = self.request()
        self.assertTrue(result.is_error)
        self.assertIn('"code": "response_too_large"', result.content[0].text)
        self.payload()  # failures released the connection; a subsequent normal call works

    def test_discovery_consumes_the_same_deadline(self):
        original = m.checked
        def slow(conn, collection):
            conn.execute("SELECT pg_sleep(1)")
            return original(conn, collection)
        with patch.dict(os.environ, {"MORLET_MCP_INVESTIGATION_TIMEOUT_MS": "50"}), patch.object(m, "checked", slow):
            result = self.request()
        self.assertTrue(result.is_error)
        self.assertIn('"code": "budget_exceeded"', result.content[0].text)

    def test_default_request_respects_lower_operator_candidate_cap(self):
        with patch.dict(os.environ, {"MORLET_MCP_INVESTIGATION_MAX_CANDIDATES": "1"}):
            result = self.request()
        self.assertTrue(result.is_error)
        self.assertIn('"code": "budget_exceeded"', result.content[0].text)

    def test_unknown_and_unindexed_coverage_are_distinct(self):
        original = _Reader._has
        def has(reader, signature):
            return False if "morlet_coverage(" in signature else original(reader, signature)
        with patch.object(_Reader, "_has", has):
            result = self.payload()
        self.assertIn("coverage_unknown", result["evidence_states"])
        self.conn.execute(f"DELETE FROM {self.coll}_envelopes")
        self.conn.commit()
        result = self.payload()
        self.assertIn("unsearched_ranges", result["evidence_states"])
        self.assertIn("no_matches_returned", result["evidence_states"])
        self.assertNotIn("coverage_unknown", result["evidence_states"])

    def add_context(self):
        self.conn.execute("SELECT morlet_enable_context(%s::regclass)", (self.coll,))
        for rid in ("query", "prior", "secret"):
            rows = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": 1024}
                    for k, v in (("tool", 7), ("part", rid))]
            self.conn.execute("SELECT morlet_add_context(%s::regclass,%s,%s)", (self.coll, rid, Jsonb(rows)))
            self.conn.execute("SELECT morlet_build_context_segments(%s::regclass,%s,ARRAY['tool'],ARRAY['part'])", (self.coll, rid))
            self.conn.execute("SELECT morlet_build_context_values(%s::regclass,%s)", (self.coll, rid))
        self.conn.execute("SELECT morlet_add_label(%s::regclass,'prior',0,1024,'reviewed','observed')", (self.coll,))
        sid = self.conn.execute(f"SELECT segment_id FROM {self.coll}_ctx_segments WHERE recording_id='query'").fetchone()[0]
        self.conn.commit()
        return sid

    def test_context_states_and_neighbor_citations(self):
        sid = self.add_context()
        result = self.payload(segment_id=sid)
        self.assertIn("insufficient_history", result["evidence_states"])
        self.assertIn("undefined_labels", result["evidence_states"])
        self.assertIn("labelled_history_not_exhaustively_searched", result["evidence_states"])
        for neighbor in result["context"]["neighbors"]:
            self.assertEqual(neighbor["ref"]["collection"], self.coll)
            self.assertEqual(neighbor["ref"]["recording_id"], neighbor["recording_id"])

    def test_concurrent_ingest_and_label_revision_do_not_mix_snapshots(self):
        sid = self.add_context()
        original = _Reader.search
        def concurrent(reader, *args, **kwargs):
            with psycopg.connect(URL) as writer:
                morletdb.Client(writer).ingest(self.coll, "late", self.wave, sample_rate_hz=1000,
                                               channel="x", unit="V", search_window=512)
                writer.execute(f"UPDATE {self.coll}_labels SET retracted=true")
            return original(reader, *args, **kwargs)
        with patch.object(_Reader, "search", concurrent):
            result = self.payload(segment_id=sid, top_k=20)
        self.assertNotIn("late", {h["ref"]["recording_id"] for h in result["search"]["matches"]})
        self.assertTrue(any(h["labels"] for h in result["context"]["neighbors"]))
        self.assertFalse(any(h["labels"] for h in self.payload(segment_id=sid)["context"]["neighbors"]))

    def check_rls(self, first):
        role = self.schema + "_reader"
        self.conn.execute(f"CREATE ROLE {role} LOGIN")
        try:
            self.conn.execute(f"ALTER TABLE {self.coll} ENABLE ROW LEVEL SECURITY")
            self.conn.execute(f"CREATE POLICY scope ON {self.coll} USING (metadata->>'site'='public')")
            if first:
                self.conn.execute("SELECT morlet_enable_row_security(%s::regclass)", (self.coll,))
            sid = self.add_context()
            if not first:
                self.conn.execute("SELECT morlet_enable_row_security(%s::regclass)", (self.coll,))
            self.conn.execute(f"GRANT USAGE ON SCHEMA {self.schema} TO {role}")
            self.conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {self.schema} TO {role}")
            self.conn.commit()
            with patch.dict(os.environ, {"DATABASE_URL": make_conninfo(URL, user=role)}):
                result = self.payload(segment_id=sid, detailed_coverage=True)
                self.assertNotIn("secret", {h["ref"]["recording_id"] for h in result["search"]["matches"]})
                self.assertNotIn("secret", {r["recording_id"] for r in result["scope"]["ranges"]})
                self.assertTrue(all(v["hist_n"] == 1 for v in result["context"]["comparisons"]))
                denied = call(collection=self.coll, recording_id="secret", start_sample=0, end_sample=512)
                self.assertTrue(denied.is_error)
                self.assertIn('"code": "evidence_unavailable"', denied.content[0].text)
        finally:
            self.conn.rollback()
            self.conn.execute(f"DROP OWNED BY {role}")
            self.conn.execute(f"DROP ROLE {role}")
            self.conn.commit()

    def test_rls_before_context(self):
        self.check_rls(True)

    def test_rls_after_context(self):
        self.check_rls(False)

    def test_isolated_stdio_transport(self):
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("smoke_stdio.py")),
                                 self.coll, "query", "0", "512"], capture_output=True, text=True,
                                timeout=30, env={**os.environ, "DATABASE_URL": URL})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("investigate_interval: {", result.stdout)
        self.assertIn('investigate_interval: ERROR', result.stdout)
        self.assertIn('"code": "invalid_input"', result.stdout)


if __name__ == "__main__":
    unittest.main()
