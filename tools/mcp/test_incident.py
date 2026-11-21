"""MCP resolve_incident (#580): a thin, bounded, read-only tool over Client.resolve_incident."""
import asyncio
import json
import os
import sys
import unittest
import uuid
from unittest.mock import patch

import numpy as np
import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

import morletdb
import morlet_mcp as m

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")
BASE = 1_772_366_400_000_000_000      # 2026-03-01T12:00:00Z


def call(name="resolve_incident", **kwargs):
    try:
        return asyncio.run(m.mcp.call_tool(name, kwargs))
    except ToolError as exc:
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=str(exc))])


class Requests(unittest.TestCase):
    def test_registered_read_only(self):
        tool = next(t for t in asyncio.run(m.mcp.list_tools()) if t.name == "resolve_incident")
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertEqual(set(tool.input_schema["required"]), {"collection", "asset", "incident_time"})

    def test_a_time_without_a_timezone_is_refused(self):
        for value in ("2026-03-01T12:00:01", "2026-03-01 12:00:01", "yesterday"):
            with self.subTest(value=value):
                result = call(collection="public.signals", asset="P7", incident_time=value)
                self.assertTrue(result.is_error)
                self.assertIn('"code": "invalid_input"', result.content[0].text)
                self.assertIn("timezone", result.content[0].text)

    def test_invalid_operator_mapping_is_actionable(self):
        with patch.dict(os.environ, {"MORLET_MCP_REFERENCE_KEYS": "[1]"}):
            result = call(collection="public.signals", asset="P7", incident_time="2026-03-01T12:00:01Z")
        self.assertIn('"code": "configuration_error"', result.content[0].text)

    def test_missing_sdk_leaves_other_tools(self):
        with patch.dict(sys.modules, {"morletdb": None}):
            result = call(collection="public.signals", asset="P7", incident_time="2026-03-01T12:00:01Z")
        self.assertIn('"code": "sdk_unavailable"', result.content[0].text)


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class OnDatabase(unittest.TestCase):
    def setUp(self):
        self.schema = "mcp_inc_" + uuid.uuid4().hex[:10]
        self.coll = self.schema + ".signals"
        self.conn = psycopg.connect(URL)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.db = morletdb.Client(self.conn)
        self.db.create_collection(self.coll)
        wave = np.round((1 + np.sin(np.arange(4096) / 70)) * np.sin(np.arange(4096) / 5), 4)
        for rid, machine, plant in (("query", "P7", "north"), ("prior", "P7", "north"), ("other", "Q1", "north")):
            self.db.ingest(self.coll, rid, wave, sample_rate_hz=1000, channel="x", unit="V", start_time=BASE,
                           metadata={"machine": machine, "plant": plant, "wo": "WO-17"}, search_window=512)
        self.conn.commit()
        self.env = patch.dict(os.environ, {"DATABASE_URL": URL, "MORLET_MCP_ASSET_KEY": "machine",
                                           "MORLET_MCP_ASSET_IDENTITY_KEYS": "plant",
                                           "MORLET_MCP_REFERENCE_KEYS": '{"work_order":"wo"}'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.conn.rollback()
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()

    def payload(self, **kwargs):
        args = {"collection": self.coll, "asset": "P7", "incident_time": "2026-03-01T13:00:01+01:00",
                "before_s": 0.5, "after_s": 0.012, **kwargs}
        result = call(**args)
        self.assertFalse(result.is_error, result.content[0].text)
        return json.loads(result.content[0].text)

    def test_incident_to_interval_to_investigation(self):
        """The worked example: asset + time -> interval -> investigation, with the operator's keys."""
        result = self.payload(references='{"work_order":"WO-17"}')
        self.assertEqual(result["incident_ns"], BASE + 1_000_000_000)        # the offset is honoured, ns exact
        self.assertEqual([c["ref"]["recording_id"] for c in result["candidates"]], ["prior", "query"])
        c = result["candidates"][1]
        self.assertEqual((c["ref"]["start_sample"], c["ref"]["end_sample"]), (500, 1012))
        self.assertEqual(c["references"]["work_order"]["status"], "verified")
        self.assertNotIn("ambiguous_asset", result["states"])
        self.assertEqual(result["mapping"]["asset_key"], "machine")
        evidence = call("investigate_interval", collection=self.coll, recording_id="query",
                        start_sample=c["ref"]["start_sample"], end_sample=c["ref"]["end_sample"])
        self.assertFalse(evidence.is_error, evidence.content[0].text)

    def test_finding_nothing_is_a_result_with_its_reason(self):
        self.assertEqual(self.payload(asset="NOPE")["states"], ["asset_not_found"])
        self.assertIn("no_recording_at_time", self.payload(incident_time="2026-03-02T12:00:00Z")["states"])

    def test_limits_are_errors(self):
        with patch.dict(os.environ, {"MORLET_MCP_INCIDENT_MAX_RECORDINGS": "1"}):
            result = call(collection=self.coll, asset="P7", incident_time="2026-03-01T12:00:01Z")
        self.assertIn('"code": "budget_exceeded"', result.content[0].text)
        with patch.dict(os.environ, {"MORLET_MCP_INCIDENT_MAX_OUTPUT_BYTES": "100"}):
            result = call(collection=self.coll, asset="P7", incident_time="2026-03-01T12:00:01Z")
        self.assertIn('"code": "response_too_large"', result.content[0].text)
        result = call(collection=self.coll, asset="P7", incident_time="2026-03-01T12:00:01Z",
                      references='{"lot":"L1"}')
        self.assertIn("No metadata key", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
