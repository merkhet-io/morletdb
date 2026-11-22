"""MCP explain_match (#573): a thin, read-only tool over Client.explain_match."""
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


def call(**kwargs):
    try:
        return asyncio.run(m.mcp.call_tool("explain_match", kwargs))
    except ToolError as exc:
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=str(exc))])


def wave(shift):
    i = np.arange(8192) + shift
    return np.sin(i / 9.0) + 0.2 * np.cos(i / 2.7)


class Requests(unittest.TestCase):
    ARGS = {"collection": "public.c", "query_recording_id": "q", "query_start": 0, "query_end": 2048,
            "match_recording_id": "m", "match_start": 0}

    def test_registered_read_only(self):
        tool = next(t for t in asyncio.run(m.mcp.list_tools()) if t.name == "explain_match")
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertNotIn("max_lag_fraction", tool.input_schema["properties"])      # always the search's tolerance

    def test_invalid_requests_are_refused(self):
        for change in ({"contribution_bins": 0}, {"contribution_bins": 65}, {"query_end": 0}, {"match_start": -1}):
            with self.subTest(change=change):
                result = call(**{**self.ARGS, **change})
                self.assertTrue(result.is_error)
                self.assertIn('"code": "invalid_input"', result.content[0].text)

    def test_missing_sdk_leaves_other_tools(self):
        with patch.dict(sys.modules, {"morletdb": None}):
            self.assertIn('"code": "sdk_unavailable"', call(**self.ARGS).content[0].text)


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class Database(unittest.TestCase):
    def setUp(self):
        self.schema = "mcpx_" + uuid.uuid4().hex[:10]
        self.coll = f"{self.schema}.signals"
        self.conn = psycopg.connect(URL, autocommit=True)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.execute, f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        db = morletdb.Client(self.conn)
        db.create_collection(self.coll)
        for rid, shift in (("q", 0), ("m", 40)):
            db.ingest(self.coll, rid, wave(shift), sample_rate_hz=1000, channel="x", unit="g", search_window=2048)
        patcher = patch.dict(os.environ, {"DATABASE_URL": URL})
        patcher.start()
        self.addCleanup(patcher.stop)

    def args(self, **change):
        return {"collection": self.coll, "query_recording_id": "q", "query_start": 0, "query_end": 2048,
                "match_recording_id": "m", "match_start": 0, **change}

    def test_explains_the_search_distance(self):
        start, distance = self.conn.execute(
            "SELECT start_sample, distance FROM morlet_search_envelope_interval(%s::regclass,'q',0,2048,top_k=>20) "
            "WHERE recording_id='m' ORDER BY distance LIMIT 1", (self.coll,)).fetchone()
        result = call(**self.args(match_start=start, contribution_bins=4))
        self.assertFalse(result.is_error)
        out = json.loads(result.content[0].text)
        self.assertEqual(out["distance"], distance)
        self.assertEqual(len(out["contributions"]), 4)
        self.assertIn("not a fault's cause", out["interpretation"])
        self.assertNotIn("stored_envelope", out)
        json.dumps(out, allow_nan=False)

    def test_a_missing_match_is_unavailable_evidence(self):
        result = call(**self.args(match_start=7))
        self.assertTrue(result.is_error)
        self.assertIn('"code": "evidence_unavailable"', result.content[0].text)

    def test_an_unknown_collection_is_structured(self):
        result = call(**self.args(collection="public.missing"))
        self.assertIn('"code": "invalid_input"', result.content[0].text)

    def test_a_shifted_hit_is_explained_against_its_shifted_query(self):
        shift = 512
        expected = self.conn.execute(
            "SELECT distance FROM morlet_explain_match(%s::regclass,'q',%s,%s,'m',0)",
            (self.coll, shift, 2048 + shift)).fetchone()[0]
        out = json.loads(call(**self.args(query_shift_samples=shift)).content[0].text)
        self.assertEqual(out["distance"], expected)
        self.assertEqual((out["query"]["start_sample"], out["query"]["end_sample"]), (shift, 2048 + shift))
