"""MCP admission (#541): a cap across processes, and a refusal that is never an empty result."""
import asyncio
import json
import os
import threading
import unittest
from unittest.mock import patch

import psycopg
from mcp.server.mcpserver.exceptions import ToolError

import morlet_mcp as m

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")


class Settings(unittest.TestCase):
    def test_invalid_settings_are_configuration_errors(self):
        for name, value in (("MORLET_MCP_MAX_CONCURRENT", "-1"), ("MORLET_MCP_MAX_CONCURRENT", "x"),
                            ("MORLET_MCP_QUEUE_MS", "60001")):
            with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}), \
                    self.assertRaises(ToolError) as caught:
                with m.admission.admission():
                    pass
            self.assertEqual(json.loads(str(caught.exception))["code"], "configuration_error")

    def test_off_by_default_opens_no_connection(self):
        env = {k: v for k, v in os.environ.items() if k != "MORLET_MCP_MAX_CONCURRENT"}
        with patch.dict(os.environ, env, clear=True), patch.object(psycopg, "connect", side_effect=AssertionError):
            with m.admission.admission():
                pass


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
        other.close()      # a disconnected client's slot comes back without any cleanup
        asyncio.run(m.mcp.call_tool("list_collections", {}))

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
