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
        self.assertEqual(sorted(t.name for t in tools), ["compare_intervals", "coverage", "describe_interval", "find_onsets",
                                                         "list_collections", "list_recordings", "locate_time",
                                                         "read_interval", "search_from_time", "search_similar",
                                                         "trend"])
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

    def test_band_power_sums_to_variance(self):
        rng = np.random.default_rng(1)
        x = rng.normal(0, 2.0, 4096)
        s = m.stats(x, 100.0)
        self.assertAlmostEqual(sum(s["band_power"].values()), s["std"] ** 2, places=6)

    def test_custom_bands_and_min_frequency(self):
        rate = 100.0
        t = np.arange(4096) / rate
        x = 10 * np.sin(2 * np.pi * 0.2 * t) + np.sin(2 * np.pi * 12 * t)      # strong slow wave, weak 12 Hz
        self.assertAlmostEqual(m.stats(x, rate)["dominant_frequency_hz"], 0.2, delta=0.05)
        s = m.stats(x, rate, m.parse_bands("10-15,15-20", rate), 1.0)
        self.assertAlmostEqual(s["dominant_frequency_hz"], 12.0, delta=0.1)
        self.assertEqual(list(s["band_energy_fraction"]), ["10-15 Hz", "15-20 Hz"])
        self.assertIsNone(m.parse_bands(" ", rate))
        with self.assertRaises(ValueError):
            m.parse_bands("10-60", rate)                                       # above Nyquist

    def test_bandpass_removes_the_slow_wave(self):
        rate = 100.0
        t = np.arange(4096) / rate
        y = m.bandpass(10 * np.sin(2 * np.pi * 0.2 * t) + np.sin(2 * np.pi * 12 * t), rate, 2.0, 0.0)
        self.assertAlmostEqual(float(y.std()), 1 / np.sqrt(2), places=2)

    def test_sta_lta_triggers_at_a_burst(self):
        rate = 100.0
        rng = np.random.default_rng(2)
        x = rng.normal(0, 1, 6000)
        x[4000:4300] *= 20
        r = m.sta_lta(x, rate, 1.0, 30.0)
        self.assertEqual(float(r[:3099].max()), 0.0)                          # windows still filling
        first = int(np.flatnonzero(r >= 4)[0])
        self.assertTrue(4000 <= first < 4100, first)

    def test_merge_hits_keeps_best_and_drops_overlaps(self):
        h = lambda r, s, d: {"recording_id": r, "start_sample": s, "end_sample": s + 100, "distance": d}
        runs = [[h("a", 0, 0.3), h("b", 500, 0.2)],
                [h("a", 50, 0.1), h("b", 1000, 0.25), h("q", 120, 0.05)]]
        kept = m.merge_hits(runs, "q", 100, 300)                 # q 120 overlaps the query span
        self.assertEqual([(k["recording_id"], k["start_sample"]) for k in kept], [("a", 50), ("b", 500), ("b", 1000)])

    def test_background_is_robust_to_one_event(self):
        rng = np.random.default_rng(3)
        before, after = rng.normal(0, 1, 4000), rng.normal(0, 1, 4000)
        after[1000:1100] *= 50                                    # one loud event in one window
        bg = m.background(before, after, 400, 100.0, None)
        self.assertEqual(bg["windows"], 20)
        self.assertAlmostEqual(bg["std"], 1.0, delta=0.15)
        with self.assertRaises(ValueError):
            m.background(before[:500], np.zeros(0), 400, 100.0, None)

    def test_frame_window(self):
        self.assertEqual(m.frame_window(10_000, 100.0, 4096), 10_000 - round(m.FRAME_LEAD_S * 100))
        self.assertEqual(m.frame_window(50, 100.0, 4096), 0)

    def test_change_point_needs_a_sustained_rise(self):
        rng = np.random.default_rng(4)
        v = list(rng.normal(10, 0.1, 100))
        v[40] = 50                                                  # a single spike is not a change
        v[70:] = [12 + 0.1 * i for i in range(30)]                   # sustained rise from point 70
        i, med, _ = m.change_point(v, 20)
        self.assertEqual(i, 70)
        self.assertAlmostEqual(med, 10, delta=0.1)
        self.assertIsNone(m.change_point(list(rng.normal(10, 0.1, 50)), 10)[0])


if __name__ == "__main__":
    unittest.main()
