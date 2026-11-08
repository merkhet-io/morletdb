"""decision.py and the MCP tools decision_state and decisions against a real database (#328).

Needs MORLET_MCP_TEST_DATABASE_URL (a database where the role may create the extension and tables)."""
import asyncio
import json
import math
import os
import unittest

import decision
import morlet_mcp as m

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")
C = "public.dtest"


def wave(part, n=2048):
    """Deterministic cuts: the level grows slowly with the part number; part 12 also has a short spike."""
    x = [(1 + 0.01 * part) * math.sin(i / 7.0) + 0.1 * math.sin(i / 3.1 + part) for i in range(n)]
    if part == 12:
        for i in range(1000, 1012):
            x[i] += 8.0
    return [round(v, 4) for v in x]


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class DecisionOnADatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn = psycopg.connect(URL, autocommit=True)
        c = cls.conn
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        for t in ("labels", "ctx_values", "ctx_segments", "ctx", "chunks", "segments"):
            c.execute(f"DROP TABLE IF EXISTS public.dtest_{t} CASCADE")
        c.execute("DROP TABLE IF EXISTS public.dtest CASCADE")
        c.execute("SELECT morlet_create_stream_collection('public','dtest'); SELECT morlet_enable_context('dtest')")
        for part in range(1, 13):
            for ch in ("a", "b"):
                rid = f"p{part:02d}-{ch}"
                c.execute("SELECT morlet_start_recording('dtest',%s,1000,%s,'V','{}',512,256,'zscore','none',1)",
                          (rid, ch))
                c.execute("SELECT morlet_append_chunk('dtest',%s,0,%s::float8[])", (rid, wave(part)))
                ctx = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": 2048}
                       for k, v in (("machine", "M1"), ("tool", 7), ("part", f"P{part}"))]
                c.execute("SELECT morlet_add_context('dtest',%s,%s)", (f"b-{rid}", json.dumps(ctx)))
                c.execute("SELECT morlet_build_context_segments('dtest',%s,ARRAY['machine','tool'],ARRAY['part'])",
                          (rid,))
                c.execute("SELECT morlet_build_context_values('dtest',%s)", (rid,))
        cls.seg12 = decision.segment_at(c, C, "p12-a", 5)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_summary_score_equals_the_sql_summary(self):
        for rid in ("p03-a", "p07-b", "p12-a"):
            seg = decision.segment_at(self.conn, C, rid, 0)
            st = decision.decision_state(self.conn, C, seg, neighbors=0)
            score, top = self.conn.execute(
                "SELECT score, top_value FROM morlet_compare_summary('dtest', %s, min_history => 3)", (seg,)).fetchone()
            self.assertAlmostEqual(st["summary"]["score"], score, delta=1e-5 * max(1.0, abs(score)))
            self.assertEqual(st["summary"]["top_value"], top)
            self.assertNotIn("score_quantile", st["summary"])
        with_q = decision.decision_state(self.conn, C, self.seg12, neighbors=0, summary_quantile=True)
        self.assertIn("score_quantile", with_q["summary"])

    def test_small_history_is_flagged(self):
        st = decision.decision_state(self.conn, C, self.seg12, neighbors=0)
        self.assertEqual(st["v"], 2)
        self.assertEqual(st["history"]["size"], 11)          # the other 11 parts; its own occurrence is excluded
        self.assertNotIn("small", st["history"])            # default reliable_history 10
        small = decision.decision_state(self.conn, C, self.seg12, neighbors=0, reliable_history=20)
        self.assertTrue(small["history"]["small"])
        self.assertIn("only 11 earlier", small["history"]["note"])
        self.assertNotIn("occurrence", st["segment"])        # part numbers only on request
        self.assertEqual(decision.decision_state(self.conn, C, self.seg12, neighbors=0,
                                                 include_occurrence=True)["segment"]["occurrence"], {"part": "P12"})

    def test_the_spike_shows_in_kurtosis(self):
        st = decision.decision_state(self.conn, C, self.seg12, neighbors=0)
        kurt = next(v for v in st["values"] if v["name"] == "kurtosis")
        self.assertGreater(kurt["relative_deviation"], 1.0)
        self.assertEqual(st["summary"]["top_value"], "kurtosis")

    def test_a_logged_decision_reads_back_with_its_state(self):
        state = decision.decision_state(self.conn, C, self.seg12, neighbors=2)
        ids = decision.log_decision(self.conn, C, [("p12-a", 0, 2048), ("p12-b", 0, 2048)], "inspect",
                                    model="test-model", question="needs_inspection", probability=0.71,
                                    threshold=0.6, state=state)
        self.assertEqual(len(ids), 2)
        found = decision.decisions(self.conn, C, recording_prefix="p12", answer="inspect", with_state=True)
        d = next(x for x in found if set(x["label_ids"]) == set(ids))
        self.assertEqual((d["answer"], d["model"], d["probability"], d["threshold"]), ("inspect", "test-model", 0.71, 0.6))
        self.assertEqual(d["state_sha256"], decision.state_hash(state))
        self.assertEqual(d["state"], state)
        self.assertEqual(sorted(s["recording_id"] for s in d["segments"]), ["p12-a", "p12-b"])
        self.assertFalse(decision.decisions(self.conn, C, recording_prefix="p01", answer="inspect"))
        self.assertNotIn("state", decision.decisions(self.conn, C, recording_prefix="p12")[0])
        with self.assertRaises(ValueError):
            decision.log_decision(self.conn, C, [("p12-a", 0, 2048)], "two words", model="m", question="q")

    def test_the_mcp_tools(self):
        os.environ["DATABASE_URL"] = URL
        r = asyncio.run(m.mcp.call_tool("decision_state", {"collection": C, "recording_id": "p12-a", "sample": 100,
                                                            "neighbors": 2}))
        st = json.loads(r.content[0].text)
        self.assertEqual(st["segment"]["recording_id"], "p12-a")
        self.assertEqual(len(st["neighbors"]), 2)
        r = asyncio.run(m.mcp.call_tool("decisions", {"collection": C, "recording_prefix": "p12"}))
        self.assertIn("decisions", json.loads(r.content[0].text))


if __name__ == "__main__":
    unittest.main()
