"""Native evidence contracts: independent schema checks and real PostgreSQL behavior."""
import asyncio
import copy
import json
import os
import sys
import subprocess
import unittest
from unittest.mock import patch

import jsonschema
import numpy as np
import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pathlib import Path
from pydantic import ValidationError

import morletdb
import morlet_mcp as m
import test_investigation as legacy


def invoke(name, request):
    try:
        return asyncio.run(m.mcp.call_tool(name, {"request": request}))
    except ToolError as exc:
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=str(exc))])


def validate(test, name, result):
    test.assertFalse(result.is_error, result.content[0].text)
    tool = next(t for t in asyncio.run(m.mcp.list_tools()) if t.name == name)
    jsonschema.Draft202012Validator.check_schema(tool.output_schema)
    jsonschema.validate(result.structured_content, tool.output_schema)
    test.assertEqual(json.loads(result.content[0].text), result.structured_content)
    return result.structured_content


class Contracts(unittest.TestCase):
    def test_invalid_native_inputs_fail_before_database(self):
        base = {"query": {"collection": "public.signals", "recording_id": "q", "start_sample": 0, "end_sample": 512}}
        invalid = [{"metadata_filter": "{}"}, {"top_k": 21}, {"top_k": True}, {"match_keys": "tool"},
                   {"match_keys": ["tool"]}, {"exclude_labels": ["x"]}, {"unexpected": 1},
                   {"max_candidates": -1}, {"metadata_filter": {"x": float("nan")}}]
        with patch.object(m, "connect", side_effect=AssertionError("invalid input reached database")):
            for extra in invalid:
                with self.subTest(extra=extra):
                    self.assertTrue(invoke("investigate_interval_v2", {**base, **extra}).is_error)
            for end in (0, -1, 513, True, 2**63):
                request = copy.deepcopy(base)
                request["query"]["end_sample"] = end
                self.assertTrue(invoke("investigate_interval_v2", request).is_error)
            for extra in ({"max_points": 9}, {"max_points": True}):
                self.assertTrue(invoke("read_interval_v2", {**base, **extra}).is_error)

    def test_schema_exposes_bounds_and_discriminators(self):
        tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
        schema = tools["investigate_interval_v2"].input_schema
        properties = schema["$defs"]["InvestigationRequest"]["properties"]
        self.assertEqual(properties["top_k"]["maximum"], 20)
        self.assertEqual(properties["metadata_filter"]["type"], "object")
        self.assertEqual(properties["match_keys"]["type"], "array")
        output = tools["read_interval_v2"].output_schema
        self.assertEqual(output["properties"]["data"]["discriminator"]["propertyName"], "kind")
        self.assertIn("samples", output["properties"]["data"]["discriminator"]["mapping"])
        error = asyncio.run(m.describe_operation("investigate_interval_v2", "/error_schema")).definition
        jsonschema.validate({"code": "budget_exceeded", "message": "timeout", "suggestion": "narrow scope"}, error)

    def test_missing_sdk_does_not_disable_discovery(self):
        request = {"query": {"collection": "public.signals", "recording_id": "q", "start_sample": 0, "end_sample": 512}}
        with patch.dict(sys.modules, {"morletdb": None}):
            for tool in ("investigate_interval_v2", "read_interval_v2"):
                self.assertIn('"code": "sdk_unavailable"', invoke(tool, request).content[0].text)
            self.assertTrue(asyncio.run(m.list_operations()).operations)


@unittest.skipUnless(legacy.URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class EvidenceOnDatabase(unittest.TestCase):
    setUp = legacy.InvestigationOnDatabase.setUp
    tearDown = legacy.InvestigationOnDatabase.tearDown
    add_context = legacy.InvestigationOnDatabase.add_context

    def request(self, **kwargs):
        return invoke("investigate_interval_v2", {"query": {"collection": self.coll, "recording_id": "query",
                                  "start_sample": 0, "end_sample": 512}, **kwargs})

    def payload(self, **kwargs):
        return validate(self, "investigate_interval_v2", self.request(**kwargs))

    def read_result(self, recording_id="query", start=0, end=512, **kwargs):
        return invoke("read_interval_v2", {"query": {"collection": self.coll, "recording_id": recording_id,
                                           "start_sample": start, "end_sample": end}, **kwargs})

    test_readonly_is_set_before_discovery = legacy.InvestigationOnDatabase.test_readonly_is_set_before_discovery
    test_timeout_and_size_limits_are_errors_not_empty_matches = legacy.InvestigationOnDatabase.test_timeout_and_size_limits_are_errors_not_empty_matches
    test_unknown_and_unindexed_coverage_are_distinct = legacy.InvestigationOnDatabase.test_unknown_and_unindexed_coverage_are_distinct
    test_concurrent_ingest_and_label_revision_do_not_mix_snapshots = legacy.InvestigationOnDatabase.test_concurrent_ingest_and_label_revision_do_not_mix_snapshots

    def test_investigation_preserves_legacy_evidence(self):
        for detailed in (False, True):
            result = self.payload(detailed_coverage=detailed)
            old = json.loads(legacy.call(collection=self.coll, recording_id="query", start_sample=0, end_sample=512,
                                         detailed_coverage=detailed).content[0].text)
            for key in ("search", "query_stats", "recording", "evidence_states", "tool_limits"):
                actual = copy.deepcopy(result[key])
                if key == "search":
                    if "interpretation_revisions" not in old[key]:
                        self.assertIsNone(actual.pop("interpretation_revisions"))
                    for hit, previous in zip(actual["matches"], old[key]["matches"]):
                        if "interpretation_revision" not in previous:
                            self.assertIsNone(hit.pop("interpretation_revision"))
                self.assertEqual(actual, old[key])
            self.assertEqual(result["kind"], "investigation")
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["version"], 2)
            self.assertEqual(result["scope"]["ranges"], old["scope"].get("ranges"))
            self.assertLessEqual(m._sibling("investigation").wire_size(result), result["tool_limits"]["max_output_bytes"])

    def test_revision_fields_and_independent_history_cap(self):
        result = self.payload()
        result["provenance"]["interpretation_revisions"] = {"query": 2}
        result["search"]["interpretation_revisions"] = {"query": 2}
        result["search"]["matches"][0]["interpretation_revision"] = 3
        validated = m.evidence_contracts.InvestigationEvidence.model_validate(result).model_dump(mode="json")
        self.assertEqual(validated["provenance"]["interpretation_revisions"], {"query": 2})
        self.assertEqual(validated["search"]["matches"][0]["interpretation_revision"], 3)
        with patch.dict(os.environ, {"MORLET_MCP_INVESTIGATION_MAX_HISTORY": "1"}):
            failure = self.request(max_history=2)
        self.assertTrue(failure.is_error)
        self.assertIn('"code": "invalid_input"', failure.content[0].text)

    def test_context_arrays_preserve_commas(self):
        sid = self.add_context()
        self.conn.execute(f"UPDATE {self.coll}_ctx_segments SET context=jsonb_build_object('tool,kind', 7)")
        self.conn.execute(f"UPDATE {self.coll}_labels SET label='reviewed,checked'")
        self.conn.execute("SELECT morlet_define_label(%s::regclass,'reviewed,checked','Checked occurrence')", (self.coll,))
        self.conn.commit()
        result = self.payload(segment_id=sid, match_keys=["tool,kind"])
        self.assertTrue(result["context"]["label_definitions"])
        self.assertEqual(result["settings"]["match_keys"], ["tool,kind"])
        excluded = self.payload(segment_id=sid, match_keys=["tool,kind"], exclude_labels=["reviewed,checked"])
        self.assertEqual(excluded["settings"]["exclude_labels"], ["reviewed,checked"])
        self.assertFalse(any(n["labels"] for n in excluded["context"]["neighbors"]))

    def test_probe_and_unknown_coverage_validation(self):
        payload = self.payload()
        payload["search"]["mode"] = "probes"
        with self.assertRaises(ValidationError):
            m.evidence_contracts.InvestigationEvidence.model_validate(payload)
        payload["scope"]["candidate_completeness"] = "unknown"
        payload["scope"]["status"] = "unknown"
        with self.assertRaises(ValidationError):
            m.evidence_contracts.InvestigationEvidence.model_validate(payload)
        payload["evidence_states"] += ["coverage_unknown", "approximate_candidates_possible"]
        m.evidence_contracts.InvestigationEvidence.model_validate(payload)

    def test_invalid_result_is_a_contract_error(self):
        with patch.object(m.evidence_contracts, "investigation_result", side_effect=ValueError("bad server output")):
            result = self.request()
        self.assertTrue(result.is_error)
        self.assertIn('"code": "contract_error"', result.content[0].text)

    def check_rls(self, first):
        role = self.schema + "_native_reader"
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
            with patch.dict(os.environ, {"DATABASE_URL": psycopg.conninfo.make_conninfo(legacy.URL, user=role)}):
                result = self.payload(segment_id=sid, detailed_coverage=True)
                self.assertNotIn("secret", {h["ref"]["recording_id"] for h in result["search"]["matches"]})
                self.assertNotIn("secret", {r["recording_id"] for r in result["scope"]["ranges"]})
                validate(self, "read_interval_v2", self.read_result())
                denied = self.read_result(recording_id="secret")
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

    def test_read_samples_signed_zero_and_unequal_summary_buckets(self):
        self.db.ingest(self.coll, "zeros", np.array([-0.0, 0.0, 1.0, -1.0]), sample_rate_hz=1000, channel="x", unit="V")
        self.conn.commit()
        result = validate(self, "read_interval_v2", self.read_result(recording_id="zeros", end=4))
        self.assertEqual(result["data"]["kind"], "samples")
        self.assertTrue(np.signbit(result["data"]["values"][0]))
        self.assertFalse(np.signbit(result["data"]["values"][1]))
        summary = validate(self, "read_interval_v2", self.read_result(start=3, end=516, max_points=10))
        self.assertEqual(summary["data"]["kind"], "min_max_summary")
        self.assertNotIn("values", summary["data"])
        expected_edges = [3, 106, 209, 312, 414, 516]
        for bucket, lo, hi in zip(summary["data"]["buckets"], expected_edges, expected_edges[1:]):
            self.assertEqual((bucket["start_sample"], bucket["end_sample"]), (lo, hi))
            self.assertEqual(bucket["min"], float(self.wave[lo:hi].min()))
            self.assertEqual(bucket["max"], float(self.wave[lo:hi].max()))

    def test_read_limits_and_snapshot(self):
        original = morletdb.Client.read
        def concurrent(db, *args, **kwargs):
            result = original(db, *args, **kwargs)
            with psycopg.connect(legacy.URL) as writer:
                writer.execute(f"DELETE FROM {self.coll} WHERE recording_id='query'")
            return result
        with patch.object(morletdb.Client, "read", concurrent):
            result = validate(self, "read_interval_v2", self.read_result())
        self.assertEqual(result["stats"]["sample_count"], 512)
        self.assertTrue(self.read_result().is_error)
        with patch.dict(os.environ, {"MORLET_MCP_READ_MAX_OUTPUT_BYTES": "100"}):
            error = self.read_result(recording_id="prior")
            self.assertTrue(error.is_error)
            self.assertIn('"code": "response_too_large"', error.content[0].text)

    def test_read_timeout_and_unsupported_statistics_are_errors(self):
        def slow(db, *args, **kwargs):
            db._execute("SELECT pg_sleep(1)")  # SDK translates QueryCanceled; retain its budget semantics.
        with patch.object(morletdb.Client, "read", slow), patch.dict(os.environ, {"MORLET_MCP_TIMEOUT_MS": "50"}):
            error = self.read_result()
            self.assertTrue(error.is_error)
            self.assertIn('"code": "budget_exceeded"', error.content[0].text)
        with patch.object(morletdb.Client, "stats", side_effect=morletdb.UnsupportedError("old SQL")):
            error = self.read_result()
            self.assertTrue(error.is_error)
            self.assertIn('"code": "unsupported"', error.content[0].text)

    def test_summary_only_evidence_is_not_an_empty_success(self):
        from psycopg.types.json import Jsonb
        self.conn.execute("SELECT morlet_enable_memory(%s::regclass)", (self.coll,))
        self.conn.execute("SELECT morlet_add_episodes(%s::regclass,%s)", (self.coll, Jsonb([
            {"recording_id": "prior", "start_sample": 0, "end_sample": 1024}])))
        run = self.conn.execute("SELECT morlet_start_memory_run(%s::regclass,'test','1')", (self.coll,)).fetchone()[0]
        self.conn.execute("SELECT * FROM morlet_set_level(%s::regclass,%s,%s)",
                          (self.coll, run, Jsonb([{"episode_id": 1, "level": 3}])))
        self.conn.execute("SELECT morlet_finish_memory_run(%s::regclass,%s,'finished')", (self.coll, run))
        self.conn.commit()
        result = self.payload(detailed_coverage=True)
        self.assertIn("unsearched_ranges", result["evidence_states"])
        self.assertIn("summarized", result["scope"]["samples_by_state"])
        self.assertTrue(self.read_result(recording_id="prior").is_error)

    def test_native_tools_over_stdio(self):
        async def run():
            params = StdioServerParameters(command=sys.executable,
                args=["-I", str(Path(__file__).with_name("morlet_mcp.py"))], env={"DATABASE_URL": legacy.URL})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {t.name: t for t in (await session.list_tools()).tools}
                    query = {"collection": self.coll, "recording_id": "query", "start_sample": 0, "end_sample": 512}
                    for name, request in (("investigate_interval_v2", {"query": query, "metadata_filter": {"site": "public"}}),
                                          ("read_interval_v2", {"query": query, "max_points": 10})):
                        result = await session.call_tool(name, {"request": request})
                        self.assertFalse(result.is_error, result.content[0].text)
                        jsonschema.validate(result.structured_content, tools[name].output_schema)
                    invalid = await session.call_tool("investigate_interval_v2", {"request": {"query": query, "top_k": 21}})
                    self.assertTrue(invalid.is_error)
                    missing = await session.call_tool("read_interval_v2", {"request": {"query": {**query, "recording_id": "missing"}}})
                    self.assertTrue(missing.is_error)
                    self.assertIn('"code": "evidence_unavailable"', missing.content[0].text)
        asyncio.run(run())

    def test_selective_cli_executes_the_native_contract(self):
        query = {"collection": self.coll, "recording_id": "query", "start_sample": 0, "end_sample": 512}
        cli = [sys.executable, "-I", str(Path(__file__).with_name("morlet_cli.py")), "call"]
        def run(name, arguments):
            return subprocess.run(cli + [name, "--input", "-"], input=json.dumps(arguments), text=True,
                                  capture_output=True, timeout=20, env={**os.environ, "DATABASE_URL": legacy.URL})
        result = run("investigate_interval_v2", {"request": {"query": query}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["kind"], "investigation")
        self.assertNotIn("content", json.loads(result.stdout))  # Do not duplicate structured payload as text.
        for name, arguments in (("execute_sql", {"sql": "DROP TABLE signals"}),
                                ("read_interval_v2", {"request": {"query": query, "max_points": 2}})):
            failure = run(name, arguments)
            self.assertNotEqual(failure.returncode, 0)
            self.assertEqual(failure.stdout, "")


if __name__ == "__main__":
    unittest.main()
