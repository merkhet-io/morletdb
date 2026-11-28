"""Discovery contracts through MCP, CLI and caller-scoped PostgreSQL catalogs."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import jsonschema
import psycopg
from psycopg.conninfo import make_conninfo
from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError

import morlet_mcp as m

ROOT = Path(__file__).resolve().parent
URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")


class Discovery(unittest.TestCase):
    def test_index_and_explanation_match_registered_schemas_without_database(self):
        tools = asyncio.run(m.mcp.list_tools())
        with patch.object(m, "connect", side_effect=AssertionError("offline discovery accessed DB")):
            index = asyncio.run(m.list_operations())
            self.assertEqual([t.name for t in index.operations], sorted(t.name for t in tools))
            for tool in tools:
                result = asyncio.run(m.describe_operation(tool.name))
                self.assertEqual(result.definition["input_schema"], tool.input_schema)
                self.assertEqual(result.definition["output_schema"], tool.output_schema)
                self.assertEqual(result.contract_hash, next(x.contract_hash for x in index.operations if x.name == tool.name))
            for name, args in (("list_operations", {}), ("describe_operation", {"name": "read_interval"})):
                result = asyncio.run(m.mcp.call_tool(name, args))
                schema = next(t.output_schema for t in tools if t.name == name)
                jsonschema.Draft202012Validator.check_schema(schema)
                jsonschema.validate(result.structured_content, schema)
                self.assertEqual(json.loads(result.content[0].text), result.structured_content)

    def test_targeted_fields_and_semantics(self):
        result = asyncio.run(m.describe_operation("investigate_interval", "/input_schema/properties/top_k"))
        self.assertEqual(result.definition["default"], 5)
        bounds = asyncio.run(m.describe_operation("investigate_interval", "/semantics/top_k"))
        self.assertEqual(bounds.definition, {"minimum": 1, "maximum": 20, "on_invalid": "error"})
        self.assertEqual(result.contract_hash, bounds.contract_hash)
        fragment = asyncio.run(m.describe_operation("list_operations", "/output_schema/properties/operations"))
        self.assertIn("OperationSummary", fragment.definitions)
        schema = {**fragment.definition, "$defs": fragment.definitions}
        jsonschema.validate([x.model_dump() for x in asyncio.run(m.list_operations()).operations], schema)

    def test_newer_tools_list_and_explain_semantics_over_mcp(self):
        async def run():
            index = (await m.mcp.call_tool("list_operations", {})).structured_content
            listed = {item["name"]: item for item in index["operations"]}
            for name, field, expected in (
                ("explain_match", "contribution_bins", {"minimum": 1, "maximum": 64, "on_invalid": "error"}),
                ("resolve_incident", "window_seconds", {"per_argument_minimum": 0,
                    "per_argument_maximum": 2678400, "finite": True, "both_zero": "error", "on_invalid": "error"}),
            ):
                with self.subTest(name=name):
                    full = (await m.mcp.call_tool("describe_operation", {"name": name})).structured_content
                    selected = (await m.mcp.call_tool("describe_operation",
                        {"name": name, "path": "/semantics"})).structured_content
                    self.assertEqual(selected["definition"], full["definition"]["semantics"])
                    self.assertEqual(selected["definition"][field], expected)
                    self.assertEqual(selected["contract_hash"], listed[name]["contract_hash"])
                    self.assertEqual(full["contract_hash"], selected["contract_hash"])
        with patch.object(m, "connect", side_effect=AssertionError("discovery accessed DB")):
            asyncio.run(run())

    def test_newer_contract_hashes_track_schema_and_semantic_changes(self):
        tools = {t.name: t for t in asyncio.run(m.mcp.list_tools())}
        for name in ("explain_match", "resolve_incident"):
            with self.subTest(name=name):
                before = asyncio.run(m.describe_operation(name)).contract_hash
                schema = copy.deepcopy(tools[name].input_schema)
                schema["properties"]["collection"]["description"] = "Changed schema qualification"
                changed = tools[name].model_copy(update={"input_schema": schema})
                self.assertNotEqual(before, m.discovery.digest(m.discovery.contract(changed)))
                notes = {**m.discovery.SEMANTICS[name], "qualification": "Changed semantic note"}
                with patch.dict(m.discovery.SEMANTICS, {name: notes}):
                    after = asyncio.run(m.describe_operation(name, "/semantics")).contract_hash
                    self.assertNotEqual(before, after)
                    index = asyncio.run(m.list_operations())
                    self.assertEqual(after, next(x.contract_hash for x in index.operations if x.name == name))
                self.assertEqual(before, asyncio.run(m.describe_operation(name)).contract_hash)

    def test_bad_names_and_pointers_are_errors(self):
        for name, path in (("does_not_exist", ""), ("read_interval", "input"),
                           ("read_interval", "/no_such_field"), ("read_interval", "/input_schema/~2"),
                           ("read_interval", "/input_schema/required/-1")):
            with self.subTest(name=name, path=path), self.assertRaises(ToolError):
                asyncio.run(m.describe_operation(name, path))

    def test_hash_changes_with_contract(self):
        tool = next(t for t in asyncio.run(m.mcp.list_tools()) if t.name == "read_interval")
        changed = tool.model_copy(update={"description": tool.description + " changed"})
        self.assertNotEqual(m.discovery.digest(m.discovery.contract(tool)), m.discovery.digest(m.discovery.contract(changed)))

    def test_cli_isolated_and_failure_exit(self):
        args = [sys.executable, "-I", str(ROOT / "morlet_cli.py"), "explain", "read_interval"]
        result = subprocess.run(args + ["--json"], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["name"], "read_interval")
        bad = subprocess.run(args + ["--path", "/missing"], capture_output=True, text=True, timeout=20)
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(bad.stdout, "")
        self.assertIn("error", json.loads(bad.stderr))

    def test_stdio_schemas_and_errors(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=["-I", str(ROOT / "morlet_mcp.py")])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    result = await session.call_tool("describe_operation", {"name": "search_similar"})
                    self.assertFalse(result.is_error)
                    schema = next(t.output_schema for t in tools if t.name == "describe_operation")
                    jsonschema.validate(result.structured_content, schema)
                    bad = await session.call_tool("describe_operation", {"name": "missing"})
                    self.assertTrue(bad.is_error)
        asyncio.run(run())


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class CollectionDiscovery(unittest.TestCase):
    def setUp(self):
        self.schema = "discovery_" + uuid.uuid4().hex[:10]
        self.role = self.schema + "_reader"
        self.conn = psycopg.connect(URL, autocommit=True)
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.addCleanup(self.conn.execute, f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.execute(f"CREATE ROLE {self.role} LOGIN")
        self.addCleanup(self.conn.execute, f"DROP ROLE {self.role}")
        self.addCleanup(self.conn.execute, f"DROP OWNED BY {self.role}")
        self.conn.execute(f"GRANT USAGE ON SCHEMA {self.schema} TO {self.role}")
        self.conn.execute("SELECT morlet_create_stream_collection(%s, 'Mixed Case')", (self.schema,))
        self.collection = f'{self.schema}."Mixed Case"'
        self.conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {self.schema} TO {self.role}")
        self.reader = make_conninfo(URL, user=self.role)

    def describe(self):
        with patch.dict(os.environ, {"DATABASE_URL": self.reader}):
            return m.describe_collection(self.collection)

    def test_grants_are_fresh_and_do_not_claim_coverage(self):
        result = self.describe()
        self.assertEqual(result.collection, self.collection)
        self.assertEqual(result.role, self.role)
        self.assertTrue(result.relations["chunks"].selectable)
        self.assertEqual(result.coverage, "not_checked")
        self.assertIsNone(result.stored_window_sizes)
        self.conn.execute(f'REVOKE SELECT ON {self.schema}."Mixed Case_chunks" FROM {self.role}')
        self.assertFalse(self.describe().relations["chunks"].selectable)
        self.conn.execute(f"REVOKE SELECT ON {self.collection} FROM {self.role}")
        with self.assertRaises(ToolError):
            self.describe()

    def test_no_row_reads_and_read_only_snapshot(self):
        # With no visible rows, SELECT grants must still not be advertised as evidence availability.
        self.conn.execute(f"ALTER TABLE {self.collection} ENABLE ROW LEVEL SECURITY")
        self.conn.execute(f"CREATE POLICY deny_rows ON {self.collection} USING (false)")
        with patch.dict(os.environ, {"DATABASE_URL": self.reader}):
            original = m.discovery.describe_collection
            def inspect(conn, name):
                self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()[0], "on")
                self.assertEqual(conn.execute("SHOW transaction_isolation").fetchone()[0], "repeatable read")
                return original(conn, name)
            with patch.object(m.discovery, "describe_collection", side_effect=inspect):
                result = asyncio.run(m.mcp.call_tool("describe_collection", {"collection": self.collection}))
        schema = next(t.output_schema for t in asyncio.run(m.mcp.list_tools()) if t.name == "describe_collection")
        jsonschema.validate(result.structured_content, schema)
        self.assertEqual(result.structured_content["coverage"], "not_checked")

    def test_catalog_only_under_exclusive_recording_lock(self):
        # A SELECT would block behind this lock; catalog discovery should not need to read the relation.
        with self.conn.transaction():
            self.conn.execute(f"LOCK TABLE {self.collection} IN ACCESS EXCLUSIVE MODE")
            with patch.dict(os.environ, {"MORLET_MCP_TIMEOUT_MS": "500"}):
                self.assertEqual(self.describe().coverage, "not_checked")

    def test_new_features_and_companion_grants_are_not_cached(self):
        self.conn.execute("SELECT morlet_enable_envelopes(%s)", (self.collection,))
        result = self.describe()
        self.assertTrue(result.relations["envelopes"].present)
        self.assertFalse(result.relations["envelopes"].selectable)
        self.conn.execute(f'GRANT SELECT ON {self.schema}."Mixed Case_envelopes" TO {self.role}')
        self.assertTrue(self.describe().relations["envelopes"].selectable)
        self.assertIsNone(self.describe().stored_window_sizes)

    def test_unknown_and_inaccessible_names(self):
        with patch.dict(os.environ, {"DATABASE_URL": self.reader}):
            with self.assertRaises(ToolError):
                m.describe_collection(self.schema + ".missing")
            self.conn.execute(f"REVOKE USAGE ON SCHEMA {self.schema} FROM {self.role}")
            with self.assertRaises(ToolError):
                m.describe_collection(self.collection)


if __name__ == "__main__":
    unittest.main()
