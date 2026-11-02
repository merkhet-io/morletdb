"""morlet_mcp.py without a database: tool registration, read-only annotations, helpers, error reporting."""
import asyncio
from datetime import datetime, timezone
import os
import unittest

import numpy as np

import morlet_mcp as m


class Server(unittest.TestCase):
    def test_tools_are_registered_read_only(self):
        tools = asyncio.run(m.mcp.list_tools())
        self.assertEqual(sorted(t.name for t in tools), ["describe_interval", "list_collections", "list_recordings",
                                                         "locate_time", "read_interval", "search_similar"])
        for t in tools:
            self.assertTrue(t.annotations.read_only_hint, t.name)
            self.assertFalse(t.annotations.destructive_hint, t.name)
            self.assertTrue(t.description, t.name)

    def test_database_errors_are_tool_errors(self):
        os.environ["DATABASE_URL"] = "host=127.0.0.1 port=1 dbname=none connect_timeout=1"
        r = None
        try:
            r = asyncio.run(m.mcp.call_tool("list_collections", {}))
        except Exception as exc:                      # the SDK reports ToolError as an error result or raises it
            self.assertIn("Error executing tool", str(exc))
            return
        self.assertTrue(r.is_error)


class Helpers(unittest.TestCase):
    def test_start_time_and_offsets(self):
        t0 = m.start_time({"start": "2025-03-01T00:00:00Z"})
        self.assertEqual(t0, datetime(2025, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(m.at(t0, 100.0, 360000), "2025-03-01T01:00:00Z")
        self.assertEqual(m.start_time({"starttime": "2021-03-01T12:00:00.5"}).second, 0)
        self.assertIsNone(m.start_time({"day": 1}))
        self.assertIsNone(m.at(None, 100.0, 5))

    def test_stats_of_a_sine(self):
        rate = 1000.0
        t = np.arange(4096) / rate
        x = 3.0 * np.sin(2 * np.pi * 50 * t)
        s = m.stats(x, rate)
        self.assertAlmostEqual(s["rms"], 3 / np.sqrt(2), places=2)
        self.assertAlmostEqual(s["crest_factor"], np.sqrt(2), places=2)
        self.assertAlmostEqual(s["kurtosis"], 1.5, places=1)
        self.assertAlmostEqual(s["dominant_frequency_hz"], 50.0, delta=0.5)
        self.assertAlmostEqual(sum(s["band_energy_fraction"].values()), 1.0, places=6)

    def test_constant_signal_has_no_spectrum(self):
        s = m.stats(np.full(100, 2.0), 100.0)
        self.assertEqual((s["std"], s["kurtosis"], s["crest_factor"]), (0.0, None, 1.0))
        self.assertNotIn("dominant_frequency_hz", s)


if __name__ == "__main__":
    unittest.main()
