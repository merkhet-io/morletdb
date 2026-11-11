"""morlet_mcp.py without a database: tool registration, read-only annotations, helpers, error reporting."""
import asyncio
from datetime import datetime, timezone
import os
import subprocess
import sys
import unittest

import numpy as np

import morlet_mcp as m


class Server(unittest.TestCase):
    def test_start_time_reads_start_ns_like_the_server(self):
        self.assertEqual(m.start_time({"start_ns": 1772409600000000000}), datetime(2026, 3, 2, tzinfo=timezone.utc))
        self.assertEqual(m.start_time({"start": "2026-03-01T00:00:00Z"}), datetime(2026, 3, 1, tzinfo=timezone.utc))
        self.assertIsNone(m.start_time({}))

    def test_tools_are_registered_read_only(self):
        tools = asyncio.run(m.mcp.list_tools())
        self.assertEqual(sorted(t.name for t in tools), ["compare_intervals", "coverage", "decision_state", "decisions", "describe_interval", "envelope_spectrum",
                                                         "find_onsets", "latest",
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

    def test_constant_query_is_flagged(self):
        self.assertIn("constant", m.query_note(np.full(4096, 3.5)))
        self.assertIsNone(m.query_note(np.sin(np.arange(4096) / 7.0)))
        self.assertIsNone(m.query_note(np.r_[np.zeros(4095), 1e-9]))   # tiny variation still has an envelope

    def test_instructions_make_no_universal_threshold_claims(self):
        self.assertNotIn("is strong", m.INSTRUCTIONS)
        self.assertIn("not a probability", m.INSTRUCTIONS)
        self.assertIn("constant window", m.INSTRUCTIONS)

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

    def test_envelope_finds_the_impact_rate(self):
        rate, n = 20000.0, 20480
        t = np.arange(n) / rate
        rng = np.random.default_rng(5)
        impacts = np.zeros(n)
        impacts[(np.arange(0, 1.0, 1 / 236.4) * rate).astype(int)] = 1.0      # 236.4 impacts per second
        ring = np.exp(-np.arange(200) / 30.0) * np.sin(2 * np.pi * 4000 * np.arange(200) / rate)
        x = np.convolve(impacts, ring)[:n] + 0.05 * rng.normal(size=n)
        env = m.envelope(x, rate, 3000, 5000)
        spec = np.abs(np.fft.rfft(env * np.hanning(n)))
        f = np.fft.rfftfreq(n, 1 / rate)
        band = (f > 50) & (f < 500)
        self.assertAlmostEqual(float(f[band][np.argmax(spec[band])]), 236.4, delta=2.0)


class Scope(unittest.TestCase):
    """Search scope (#215): what a search searched, and what exists but was not searched."""

    def test_states_are_clipped_to_the_searched_bounds_and_ranked(self):
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ranges = [("A", 0, 1000, "searchable"), ("A", 1000, 5000, "summarized"), ("A", 5000, 6000, "searchable"),
                  ("B", 0, 3000, "not_indexed"), ("C", 0, 9000, "gone")]
        s = m.summarize_scope(ranges, {"A": (500, 5500), "B": (0, 3000)}, 100.0, {"A": t0, "B": None})
        self.assertEqual(s["seconds"], {"not_indexed": 30.0, "searchable": 10.0, "summarized": 40.0})
        self.assertFalse(s["complete"])
        self.assertEqual(s["not_searched_seconds"], 70.0)
        self.assertEqual([(p["recording_id"], p["state"]) for p in s["not_searched_periods"]],
                         [("A", "summarized"), ("B", "not_indexed")])
        self.assertEqual(s["not_searched_periods"][0]["start_utc"], "2026-01-01T00:00:10Z")
        self.assertIsNone(s["not_searched_periods"][1]["start_utc"])

    def test_a_tail_shorter_than_a_window_does_not_make_the_scope_incomplete(self):
        ranges = [("A", 0, 39936, "searchable"), ("A", 39936, 40000, "not_indexed")]
        s = m.summarize_scope(ranges, {"A": (0, 40000)}, 100.0, {}, window=512)
        self.assertTrue(s["complete"])
        self.assertEqual(s["seconds"]["shorter_than_window"], 0.64)
        self.assertFalse(m.summarize_scope(ranges, {"A": (0, 40000)}, 100.0, {}, window=None)["complete"])

    def test_a_fully_searchable_scope_is_complete(self):
        s = m.summarize_scope([("A", 0, 100, "searchable")], {"A": (0, 100)}, 10.0, {})
        self.assertTrue(s["complete"])
        self.assertEqual(s["not_searched_periods"], [])

    def test_instructions_ask_for_the_scope_with_negative_results(self):
        self.assertIn("Not found is not the same as not searched", m.INSTRUCTIONS)
        self.assertEqual(set(m.NOT_SEARCHED), {"not_indexed", "not_searchable", "represented", "summarized", "gone",
                                               "missing"})


@unittest.skipUnless(os.environ.get("MORLET_MCP_TEST_DATABASE_URL"), "needs MORLET_MCP_TEST_DATABASE_URL (SQL 0.3.6)")
class ScopeOnADatabase(unittest.TestCase):
    """A search over a collection with a period out of search returns that period in its scope."""

    def test_search_reports_the_period_it_did_not_search(self):
        import psycopg
        url = os.environ["MORLET_MCP_TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS public.mcp_scope CASCADE")
            for t in ("chunks", "segments", "envelopes", "episodes", "ledger", "memory_runs", "kept_summaries"):
                conn.execute(f"DROP TABLE IF EXISTS public.mcp_scope_{t} CASCADE")
            conn.execute("""
                SELECT morlet_create_stream_collection('public','mcp_scope'); SELECT morlet_enable_chunk_summaries('mcp_scope');
                SELECT morlet_start_recording('mcp_scope','A',100,'x','g','{"start":"2026-01-01T00:00:00Z"}',512,256,'zscore','none');
                SELECT morlet_append_chunks_raw('mcp_scope','A',0,morlet_float8_le_bytes(ARRAY(
                  SELECT round(100*sin(i/9.)*(1+(i%5000)/1000.))::float8 FROM generate_series(0,39999) i)),1000);
                SELECT morlet_enable_envelopes('mcp_scope'); SELECT morlet_build_envelopes('mcp_scope','A',512,256,100000);
                SELECT morlet_enable_memory('mcp_scope');
                SELECT morlet_add_episodes('mcp_scope','[{"recording_id":"A","start_sample":10000,"end_sample":30000}]');""")
            run = conn.execute("SELECT morlet_start_memory_run('mcp_scope','test','1')").fetchone()[0]
            conn.execute("SELECT * FROM morlet_set_level('mcp_scope',%s,'[{\"episode_id\":1,\"level\":3}]')", (run,))
            conn.execute("SELECT morlet_finish_memory_run('mcp_scope',%s,'finished')", (run,))
        os.environ["DATABASE_URL"] = url
        r = m.search_similar("public.mcp_scope", "A", 0, 512)
        scope = r["scope"]
        self.assertFalse(scope["complete"])
        self.assertEqual(scope["recordings"], 1)
        self.assertGreater(scope["searched_windows"], 0)
        period = scope["not_searched_periods"][0]
        self.assertEqual(period["state"], "summarized")
        self.assertTrue(10000 <= period["start_sample"] < period["end_sample"] <= 30000)
        self.assertIn("summarized", scope["note"])
        self.assertEqual([p["state"] for p in scope["not_searched_periods"]], ["summarized"])
        self.assertTrue(all(not (10512 <= h["start_sample"] and h["end_sample"] <= 29488) for h in r["hits"]))
        m.SCOPE_TIMEOUT_MS = 1
        try:
            slow = m.search_similar("public.mcp_scope", "A", 0, 512)
        finally:
            m.SCOPE_TIMEOUT_MS = 5000
        self.assertIsNone(slow["scope"]["complete"])       # unknown, never complete
        self.assertIn("unverified", slow["scope"]["note"])
        self.assertEqual(len(slow["hits"]), len(r["hits"]))
        cov = m.coverage("public.mcp_scope")["groups"][0]["search_states_s"]
        self.assertGreater(cov["summarized"], 0)
        self.assertGreater(cov["searchable"], 0)


if __name__ == "__main__":
    unittest.main()


class IsolatedMode(unittest.TestCase):
    def test_server_module_imports_under_python_isolated_mode(self):
        """`python -I` drops the script directory from sys.path; the server must still find decision.py."""
        here = os.path.dirname(os.path.abspath(__file__))
        code = ("import importlib.util as u; s = u.spec_from_file_location('m', %r); m = u.module_from_spec(s); "
                "s.loader.exec_module(m); print(sorted(t.name for t in __import__('asyncio').run(m.mcp.list_tools()))[2])"
                % os.path.join(here, "morlet_mcp.py"))
        r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd="/", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "decision_state")
