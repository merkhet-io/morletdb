"""morlet_mcp.py without a database: tool registration, read-only annotations, helpers, error reporting."""
import asyncio
import json
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


@unittest.skipUnless(os.environ.get("MORLET_MCP_TEST_DATABASE_URL"), "needs MORLET_MCP_TEST_DATABASE_URL (SQL 0.3.6)")
class TrendPerSegmentOnADatabase(unittest.TestCase):
    """Machining cuts with an idle lead-in: the trend of the cut phase, not of the lead-in (#385)."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        url = os.environ["MORLET_MCP_TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as conn:
            for t in ("labels", "ctx_values", "ctx_segments", "ctx", "chunks", "segments"):
                conn.execute(f"DROP TABLE IF EXISTS public.mcp_trend_{t} CASCADE")
            conn.execute("DROP TABLE IF EXISTS public.mcp_trend CASCADE")
            conn.execute("SELECT morlet_create_stream_collection('public','mcp_trend')")
            conn.execute("SELECT morlet_enable_context('mcp_trend')")
            for cut in range(4):
                rid = f"cut{cut}"
                meta = '{"spindle": "S1", "start": "2026-01-01T00:%02d:00Z"}' % cut
                conn.execute("SELECT morlet_start_recording('mcp_trend',%s,1000,'acc','g',%s,512,256,'zscore','none')",
                             (rid, meta))
                # 1,000 quiet lead-in samples, then 3,000 cut samples whose level grows with the cut number
                conn.execute("SELECT morlet_append_chunks_raw('mcp_trend',%s,0,morlet_float8_le_bytes(ARRAY("
                             "SELECT round(CASE WHEN i < 1000 THEN 2 ELSE 100 * (1 + %s) END * sin(i / 5.0))::float8 "
                             "FROM generate_series(0, 3999) i)),1000)", (rid, cut))
                ctx = [{"recording_id": rid, "key": "phase", "value": "idle", "start_sample": 0, "end_sample": 1000},
                       {"recording_id": rid, "key": "phase", "value": "cut", "start_sample": 1000, "end_sample": 4000}]
                conn.execute("SELECT morlet_add_context('mcp_trend',%s,%s)", (f"b-{rid}", json.dumps(ctx)))
                conn.execute("SELECT morlet_build_context_segments('mcp_trend',%s,ARRAY['phase'],ARRAY[]::text[])",
                             (rid,))
        os.environ["DATABASE_URL"] = url

    @staticmethod
    def column(res, name):
        rows = res["table_csv"].splitlines()
        i = rows[0].split(",").index(name)
        return [r.split(",")[i] for r in rows[1:]]

    def test_default_trend_measures_the_lead_in(self):
        res = m.trend("public.mcp_trend", '{"spindle": "S1"}', samples_per_point=512)
        self.assertEqual(self.column(res, "start_sample"), ["0"] * 4)
        self.assertNotIn("segment_context", res)

    def test_segment_context_measures_the_cut(self):
        res = m.trend("public.mcp_trend", '{"spindle": "S1"}', samples_per_point=512,
                      segment_context='{"phase": "cut"}')
        self.assertEqual(res["segment_context"], {"phase": "cut"})
        self.assertEqual(self.column(res, "start_sample"), ["1000"] * 4)
        rms = [float(x) for x in self.column(res, "rms")]
        self.assertEqual(rms, sorted(rms))                 # the cut's level grows from cut to cut
        self.assertGreater(rms[0], 50)                    # the cut, not the quiet lead-in

    def test_segment_context_steps_within_a_segment_and_reports_missing_ones(self):
        res = m.trend("public.mcp_trend", '{"spindle": "S1"}', samples_per_point=1000, step_seconds=1.0,
                      segment_context='{"phase": "cut"}')
        self.assertEqual(self.column(res, "start_sample")[:3], ["1000", "2000", "3000"])
        with self.assertRaises(Exception):
            m.trend("public.mcp_trend", '{"spindle": "S1"}', segment_context='{"phase": "retract"}')
        with self.assertRaises(Exception):
            m.trend("public.mcp_trend", '{"spindle": "S1"}', segment_context='["cut"]')


@unittest.skipUnless(os.environ.get("MORLET_MCP_TEST_DATABASE_URL"), "needs MORLET_MCP_TEST_DATABASE_URL (SQL 0.3.6)")
class TimeQualityOnADatabase(unittest.TestCase):
    """#393: locate_time says how its time axis is derived and how far off it may be."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        url = os.environ["MORLET_MCP_TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as conn:
            for t in ("chunks", "segments"):
                conn.execute(f"DROP TABLE IF EXISTS public.mcp_clock_{t} CASCADE")
            conn.execute("DROP TABLE IF EXISTS public.mcp_clock CASCADE")
            conn.execute("SELECT morlet_create_stream_collection('public','mcp_clock')")
            metas = {"steady": {"start": "2026-02-01T10:00:00Z"},
                     "drifting": {"start": "2026-02-01T11:00:00Z",
                                  "time_axis": {"max_offset_s": 0.168, "fitted_rate_hz": 99.0}}}
            for rid, meta in metas.items():
                conn.execute("SELECT morlet_start_recording('mcp_clock',%s,100,'acc','g',%s,512,256,'zscore','none')",
                             (rid, json.dumps(meta)))
                conn.execute("SELECT morlet_append_chunks_raw('mcp_clock',%s,0,morlet_float8_le_bytes(ARRAY("
                             "SELECT sin(i/5.0)::float8 FROM generate_series(0,999) i)),1000)", (rid,))
        os.environ["DATABASE_URL"] = url

    def test_steady_recording_has_a_basis_and_no_uncertainty(self):
        m_ = m.locate_time("public.mcp_clock", "2026-02-01T10:00:02Z")["matches"]
        self.assertEqual([(h["recording_id"], h["sample"], h["time_basis"]) for h in m_],
                         [("steady", 200, "start + rate")])
        self.assertNotIn("time_uncertainty_s", m_[0])
        self.assertNotIn("time_note", m_[0])

    def test_drifting_recording_carries_its_uncertainty(self):
        h = m.locate_time("public.mcp_clock", "2026-02-01T11:00:05Z")["matches"][0]
        self.assertEqual((h["recording_id"], h["sample"]), ("drifting", 500))
        self.assertEqual((h["time_uncertainty_s"], h["time_uncertainty_samples"], h["fitted_rate_hz"]),
                         (0.168, 16.8, 99.0))
        self.assertIn("16.8 samples", h["time_note"])


@unittest.skipUnless(os.environ.get("MORLET_MCP_TEST_DATABASE_URL"), "needs MORLET_MCP_TEST_DATABASE_URL (SQL 0.3.6)")
class SeriesProductionOnADatabase(unittest.TestCase):
    """#384: one recording of three parts machined alike; the same cut on the other parts, not the query's own."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        url = os.environ["MORLET_MCP_TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as conn:
            for t in ("labels", "ctx_values", "ctx_segments", "ctx", "chunks", "segments", "envelopes"):
                conn.execute(f"DROP TABLE IF EXISTS public.mcp_cyc_{t} CASCADE")
            conn.execute("DROP TABLE IF EXISTS public.mcp_cyc CASCADE")
            conn.execute("SELECT morlet_create_stream_collection('public','mcp_cyc')")
            conn.execute("SELECT morlet_enable_context('mcp_cyc')")
            conn.execute("SELECT morlet_start_recording('mcp_cyc','line',1000,'acc','g',"
                         "'{\"start\": \"2026-01-01T00:00:00Z\"}',512,256,'zscore','none')")
            # three parts of 4,000 samples, each the same cut with a little part-specific noise
            conn.execute("SELECT morlet_append_chunks_raw('mcp_cyc','line',0,morlet_float8_le_bytes(ARRAY("
                         "SELECT round(100*sin((i%4000)/9.0)*(1+((i%4000)/1500))+((i/4000)+1)*sin(i/1.7))::float8 "
                         "FROM generate_series(0,11999) i)),4000)")
            ctx = [{"recording_id": "line", "key": "part", "value": f"P{k + 1}", "start_sample": 4000 * k,
                    "end_sample": 4000 * (k + 1)} for k in range(3)]
            ctx.append({"recording_id": "line", "key": "program", "value": "O100", "start_sample": 0,
                        "end_sample": 12000})
            ctx += [{"recording_id": "line", "key": "block", "value": blk, "start_sample": 4000 * k + off,
                     "end_sample": 4000 * k + off + 2000} for k in range(3) for blk, off in (("N10", 0), ("N20", 2000))]
            conn.execute("SELECT morlet_add_context('mcp_cyc','parts',%s)", (json.dumps(ctx),))
            conn.execute("SELECT morlet_build_context_segments('mcp_cyc','line',ARRAY['program','block'],"
                         "ARRAY['part'])")
            conn.execute("SELECT morlet_enable_envelopes('mcp_cyc')")
            conn.execute("SELECT morlet_build_envelopes('mcp_cyc','line',512,256,100000)")
        os.environ["DATABASE_URL"] = url

    def test_exclude_occurrence_keeps_the_same_cut_on_other_parts(self):
        q = (4000 + 1024, 4000 + 1536)                  # inside part P2
        plain = m.search_similar("public.mcp_cyc", "line", *q, top_k=5, evidence=False)
        occ = m.search_similar("public.mcp_cyc", "line", *q, top_k=5, evidence=False, exclude="occurrence")
        part = lambda s: s // 4000 + 1                   # noqa: E731
        self.assertTrue(any(part(h["start_sample"]) == 2 for h in plain["hits"]))   # without: its own part shows up
        self.assertTrue(occ["hits"])
        self.assertTrue(all(part(h["start_sample"]) != 2 for h in occ["hits"]))
        self.assertEqual({part(h["start_sample"]) for h in occ["hits"]}, {1, 3})      # the other parts remain
        self.assertGreater(occ["query"]["excluded_hits"], 0)
        rec = m.search_similar("public.mcp_cyc", "line", *q, top_k=5, evidence=False, exclude="recording")
        self.assertEqual(rec["hits"], [])                # "recording" would drop the other parts too

    def test_exclude_occurrence_needs_a_segment(self):
        with self.assertRaises(Exception):
            m.search_similar("public.mcp_parts", "p0.x", 0, 512, exclude="occurrence")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.environ.get("MORLET_MCP_TEST_DATABASE_URL"), "needs MORLET_MCP_TEST_DATABASE_URL (SQL 0.3.6)")
class FieldFindingsOnADatabase(unittest.TestCase):
    """Snapshot data from series production: 3 parts, 2 channels each, recorded at the same time (#384, #386)."""

    @classmethod
    def setUpClass(cls):
        import psycopg
        url = os.environ["MORLET_MCP_TEST_DATABASE_URL"]
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS public.mcp_parts CASCADE")
            for t in ("chunks", "segments", "envelopes"):
                conn.execute(f"DROP TABLE IF EXISTS public.mcp_parts_{t} CASCADE")
            conn.execute("SELECT morlet_create_stream_collection('public','mcp_parts')")
            for part in range(3):
                for ch in ("x", "y"):
                    rid = f"p{part}.{ch}"
                    meta = '{"part": %d, "channel": "%s", "start": "2026-01-01T00:%02d:00Z"}' % (part, ch, part)
                    conn.execute("SELECT morlet_start_recording('mcp_parts',%s,100,'acc','g',%s,512,256,'zscore','none')",
                                 (rid, meta))
                    conn.execute("SELECT morlet_append_chunks_raw('mcp_parts',%s,0,morlet_float8_le_bytes(ARRAY("
                                 "SELECT round(100*sin(i/(7.+%s))*(1+(i%%700)/300.))::float8 "
                                 "FROM generate_series(0,1999) i)),1000)", (rid, part))
            conn.execute("SELECT morlet_enable_envelopes('mcp_parts')")
            for part in range(3):
                for ch in ("x", "y"):
                    conn.execute("SELECT morlet_build_envelopes('mcp_parts',%s,512,256,100000)", (f"p{part}.{ch}",))
        os.environ["DATABASE_URL"] = url

    def test_coverage_counts_simultaneous_channels_once(self):
        g = m.coverage("public.mcp_parts")["groups"][0]
        self.assertEqual(g["recordings"], 6)
        self.assertEqual(g["recorded_blocks"], 3)
        self.assertEqual(g["recordings_per_block"], 2.0)
        self.assertEqual(g["schedule"], "snapshots")
        self.assertAlmostEqual(g["typical_start_interval_s"], 60.0)
        self.assertLessEqual(g["covered_fraction"], 1.0)
        self.assertAlmostEqual(g["covered_s"], 60.0)

    def test_locate_time_explains_a_gap(self):
        r = m.locate_time("public.mcp_parts", "2026-01-01T00:00:30Z")
        self.assertEqual(r["matches"], [])
        self.assertIn("gap", r["note"])
        self.assertEqual(r["previous"]["seconds_before"], 10.0)
        self.assertEqual(r["next"]["seconds_after"], 30.0)
        self.assertIn("after the last", m.locate_time("public.mcp_parts", "2026-01-02T00:00:00Z")["note"])
        self.assertEqual(len(m.locate_time("public.mcp_parts", "2026-01-01T00:01:05Z")["matches"]), 2)

    def test_latest_names_the_limit_the_caller_passed(self):
        with self.assertRaises(Exception) as cm:
            m.latest("public.mcp_parts", limit=2)
        self.assertIn("limit (2)", str(cm.exception))
        self.assertNotIn("max_recordings", str(cm.exception))

    def test_search_can_exclude_the_query_recording_or_part(self):
        plain = m.search_similar("public.mcp_parts", "p1.x", 512, 1024, top_k=20)
        self.assertNotIn("exclude", plain["query"])
        r = m.search_similar("public.mcp_parts", "p1.x", 512, 1024, top_k=20, exclude="recording")
        self.assertTrue(r["hits"])
        self.assertTrue(all(h["recording_id"] != "p1.x" for h in r["hits"]))
        self.assertEqual(r["query"]["excluded_hits"],
                         sum(h["recording_id"] == "p1.x" for h in
                             m.search_similar("public.mcp_parts", "p1.x", 512, 1024, top_k=100)["hits"]))
        r = m.search_similar("public.mcp_parts", "p1.x", 512, 1024, top_k=20, exclude="metadata:part")
        self.assertTrue(r["hits"])
        self.assertTrue(all(not h["recording_id"].startswith("p1.") for h in r["hits"]))
        with self.assertRaises(Exception):
            m.search_similar("public.mcp_parts", "p1.x", 512, 1024, exclude="part")
        with self.assertRaises(Exception):
            m.search_similar("public.mcp_parts", "p1.x", 512, 1024, exclude="metadata:shift")


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
