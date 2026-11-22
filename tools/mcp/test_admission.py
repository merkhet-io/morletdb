"""MCP admission (#541): a cap across processes, and a refusal that is never an empty result."""
import asyncio
import json
import os
import threading
import unittest
from unittest.mock import patch
import uuid

import numpy as np
import psycopg
from mcp.server.mcpserver.exceptions import ToolError

import morletdb
import morlet_mcp as m

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")


class Settings(unittest.TestCase):
    def test_invalid_settings_are_configuration_errors(self):
        for env in ({"MORLET_MCP_MAX_CONCURRENT": "-1"}, {"MORLET_MCP_MAX_CONCURRENT": "x"},
                    {"MORLET_MCP_MAX_CONCURRENT": "2", "MORLET_MCP_QUEUE_MS": "60001"}):
            with self.subTest(env=env), patch.dict(os.environ, env), \
                    self.assertRaises(ToolError) as caught:
                with m.admission.admission():
                    pass
            self.assertEqual(json.loads(str(caught.exception))["code"], "configuration_error")

    def test_a_disabled_gate_ignores_blank_and_queue_settings(self):
        for env in ({"MORLET_MCP_MAX_CONCURRENT": ""}, {"MORLET_MCP_MAX_CONCURRENT": "0", "MORLET_MCP_QUEUE_MS": "bad"}):
            with self.subTest(env=env), patch.dict(os.environ, env), patch.object(psycopg, "connect",
                                                                                  side_effect=AssertionError):
                with m.admission.admission():
                    pass

    def test_off_by_default_opens_no_connection(self):
        env = {k: v for k, v in os.environ.items() if k != "MORLET_MCP_MAX_CONCURRENT"}
        with patch.dict(os.environ, env, clear=True), patch.object(psycopg, "connect", side_effect=AssertionError):
            with m.admission.admission():
                pass


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class NestedFailure(unittest.TestCase):
    def test_search_from_time_does_not_turn_a_load_failure_into_a_short_recording(self):
        schema = "adm_" + uuid.uuid4().hex[:10]
        with psycopg.connect(URL, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA {schema}")
            db = morletdb.Client(conn)
            db.create_collection(f"{schema}.signals")
            db.ingest(f"{schema}.signals", "r", np.sin(np.arange(8192) / 9.0), sample_rate_hz=1000, channel="x", unit="g",
                      start_time=1_772_366_400_000_000_000, search_window=4096)
        self.addCleanup(lambda: psycopg.connect(URL, autocommit=True).execute(f"DROP SCHEMA {schema} CASCADE").connection.close())
        args = {"collection": f"{schema}.signals", "utc_time": "2026-03-01T12:00:04Z"}
        with patch.dict(os.environ, {"DATABASE_URL": URL}), \
                patch.object(m, "find_onsets", side_effect=ToolError("too many connections for role")), \
                self.assertRaises(ToolError) as caught:
            asyncio.run(m.mcp.call_tool("search_from_time", args))
        self.assertIn("too many connections", str(caught.exception))


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class Cap(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"DATABASE_URL": URL, "MORLET_MCP_MAX_CONCURRENT": "2",
                                          "MORLET_MCP_QUEUE_MS": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self, slot):
        """Another process's call: a separate session holding one slot."""
        conn = psycopg.connect(URL, autocommit=True)
        self.addCleanup(conn.close)
        self.assertTrue(conn.execute("SELECT pg_try_advisory_lock(%s,%s)", (m.admission.SLOT_CLASS, slot)).fetchone()[0])
        return conn

    def test_the_call_above_the_cap_is_refused_and_says_so(self):
        self.hold(0)
        other = self.hold(1)
        with self.assertRaises(ToolError) as caught:
            asyncio.run(m.mcp.call_tool("list_collections", {}))
        refusal = json.loads(str(caught.exception).split(": ", 1)[-1] if not str(caught.exception).startswith("{")
                             else str(caught.exception))
        self.assertEqual(refusal["code"], "busy")
        self.assertIn("not a negative result", refusal["message"])
        other.close()      # a disconnected client's slot comes back without any cleanup, once its backend has exited
        with patch.dict(os.environ, {"MORLET_MCP_QUEUE_MS": "5000"}):
            asyncio.run(m.mcp.call_tool("list_collections", {}))

    def test_a_waiter_holds_no_connection_between_attempts(self):
        self.hold(0)
        self.hold(1)
        opened = []
        real = psycopg.connect
        def counted(*args, **kwargs):
            conn = real(*args, **kwargs)
            opened.append(conn)
            return conn
        seen = []
        real_sleep = m.admission.time.sleep
        def sleeping(seconds):
            seen.append(all(c.closed for c in opened))
            real_sleep(seconds)
        with patch.dict(os.environ, {"MORLET_MCP_QUEUE_MS": "350"}), patch.object(psycopg, "connect", counted), \
                patch.object(m.admission.time, "sleep", sleeping), self.assertRaises(ToolError):
            with m.admission.admission():
                pass
        self.assertTrue(seen)
        self.assertTrue(all(seen))

    def test_an_unreachable_database_is_not_an_empty_result(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost:1/none"}), \
                self.assertRaises(ToolError) as caught:
            with m.admission.admission():
                pass
        refusal = json.loads(str(caught.exception))
        self.assertEqual(refusal["code"], "database_error")
        self.assertIn("not a negative result", refusal["message"])

    def test_a_call_waits_for_a_slot_within_the_queue_time(self):
        self.hold(0)
        other = self.hold(1)
        timer = threading.Timer(0.2, other.close)
        timer.start()
        self.addCleanup(timer.cancel)
        with patch.dict(os.environ, {"MORLET_MCP_QUEUE_MS": "5000"}), m.admission.admission():
            pass

    def test_nested_admission_takes_one_slot(self):
        self.hold(0)
        with m.admission.admission(), m.admission.admission():
            pass

    def test_slot_is_released_after_a_failing_call(self):
        with self.assertRaises(RuntimeError), m.admission.admission():
            raise RuntimeError("tool failed")
        self.hold(0)
        self.hold(1)


if __name__ == "__main__":
    unittest.main()
