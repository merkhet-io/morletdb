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
        for t in ("label_definitions", "labels", "ctx_values", "ctx_segments", "ctx", "chunks", "segments"):
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
        c.execute("SELECT morlet_add_label('dtest','p10-a',0,2048,'worn','test: wear above the limit')")
        c.execute("SELECT morlet_add_label('dtest','p11-a',0,2048,'s1:replace','{}')")   # a decision, not an outcome

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
        self.assertEqual(st["v"], 4)
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

    def test_a_logged_decision_keeps_its_precedents(self):
        """#418: the nearest labelled precedents of the state, as they were at decision time."""
        state = {"a": decision.decision_state(self.conn, C, self.seg12, neighbors=3, examples=2)}
        ids = decision.log_decision(self.conn, C, [("p12-a", 0, 2048)], "inspect", model="m", question="q",
                                    state=state, extra={"case": "recall"})
        d = next(x for x in decision.decisions(self.conn, C, recording_prefix="p12", answer="inspect")
                 if set(x["label_ids"]) == set(ids))
        self.assertNotIn("state", d)                                  # precedents come without the full state
        self.assertEqual(d["precedents_from"], "examples")
        self.assertEqual([p["recording_id"] for p in d["precedents"]], ["p10-a"])
        self.assertEqual(d["precedents"][0]["labels"], ["worn"])      # decision labels never count
        self.assertEqual(d["v"], 2)
        bare = decision.precedents({"a": decision.decision_state(self.conn, C, self.seg12, neighbors=0)})
        self.assertEqual((bare["precedents"], bare["precedents_from"]), ([], None))
        self.assertIn("examples", bare["precedents_note"])

    def test_own_baseline_is_the_drift_from_the_assets_first_segments(self):
        base = [decision.segment_at(self.conn, C, f"p0{k}-a", 0) for k in (1, 2, 3)]
        st = decision.decision_state(self.conn, C, self.seg12, neighbors=0, baseline=base + [self.seg12])
        ob = st["own_baseline"]
        self.assertEqual(ob["segments"], 3)                   # the segment itself is never its own baseline
        names = self.conn.execute("SELECT morlet_simple_value_names()").fetchone()[0]
        vals = {sid: v for sid, v in self.conn.execute(
            "SELECT segment_id, vals FROM dtest_ctx_values WHERE segment_id = ANY(%s)", (base + [self.seg12],))}
        i = names.index("rms")
        med = sorted(vals[b][i] for b in base)[1]
        rms = next(v for v in ob["values"] if v["name"] == "rms")
        self.assertAlmostEqual(rms["relative_deviation"], (vals[self.seg12][i] - med) / abs(med), places=5)
        self.assertGreater(rms["relative_deviation"], 0)      # the level grows with the part number
        other = decision.segment_at(self.conn, C, "p01-b", 0)
        self.assertEqual(st["v"], 4)
        with self.assertRaises(ValueError):                   # another channel is no baseline
            decision.decision_state(self.conn, C, self.seg12, neighbors=0, baseline=[other])

    def test_examples_are_labelled_earlier_cases_not_decisions(self):
        st = decision.decision_state(self.conn, C, self.seg12, neighbors=0, examples=3)
        ex = st["examples"]
        self.assertEqual([e["recording_id"] for e in ex], ["p10-a"])          # p11-a only has a decision label
        self.assertEqual(ex[0]["labels"], [{"label": "worn", "note": "test: wear above the limit"}])
        self.assertIn("kurtosis", ex[0]["values"])
        self.assertEqual(decision.decision_state(self.conn, C, self.seg12, neighbors=0, examples=3,
                                                 example_labels=["sharp"])["examples"], [])
        self.assertNotIn("examples", decision.decision_state(self.conn, C, self.seg12, neighbors=0))

    def test_values_and_neighbours_equal_the_sql_functions(self):
        """decision_states computes in Python what morlet_compare_to_history and morlet_history_neighbors compute
        in SQL (#403); the state must not change because of that."""
        segs = [decision.segment_at(self.conn, C, rid, 0) for rid in ("p02-a", "p05-b", "p07-a", "p12-a", "p12-b")]
        for opts in ({}, {"exclude_labels": ["worn"]}, {"match_keys": ["machine"]}, {"min_history": 11}):
            states = decision.decision_states(self.conn, C, segs, neighbors=20, **opts)
            for seg, st in zip(segs, states):
                self.assertEqual(st, decision.decision_state(self.conn, C, seg, neighbors=20, **opts))
                rows = {r[0]: r for r in self.conn.execute(
                    "SELECT value_name, value, hist_n, hist_median, relative_deviation, quantile, status "
                    "FROM morlet_compare_to_history('dtest', %s, %s::text[], exclude_labels => %s::text[], "
                    "min_history => %s)", (seg, opts.get("match_keys", []), opts.get("exclude_labels", []),
                                           opts.get("min_history", 3)))}
                self.assertEqual(len(st["values"]), len(rows))
                for v in st["values"]:
                    _n, x, hn, med, rel, qq, status = rows[v["name"]]
                    self.assertEqual(st["history"]["size"], hn)
                    self.assertEqual((v["value"], v["history_median"], v["relative_deviation"], v["history_quantile"]),
                                     tuple(decision._num(y) for y in (x, med, rel, qq)), (seg, opts, v["name"]))
                    self.assertEqual(v["status"], "ok" if status == "no_pooled_spread" else status)
                near = self.conn.execute(
                    "SELECT recording_id, start_sample, distance FROM morlet_history_neighbors('dtest', %s, %s::text[], "
                    "exclude_labels => %s::text[], top_k => 20)",
                    (seg, opts.get("match_keys", []), opts.get("exclude_labels", []))).fetchall()
                self.assertEqual([(n["recording_id"], n["start_sample"], n["distance"]) for n in st["neighbors"]],
                                 [(r, s0, decision._num(d)) for r, s0, d in near])

    def test_examples_search_the_whole_history_and_say_when_there_is_none(self):
        """#404: a labelled case is found however far it ranks, and a missing one is stated, not silently empty."""
        seg = decision.segment_at(self.conn, C, "p02-a", 0)
        st = decision.decision_state(self.conn, C, seg, neighbors=0, examples=1)
        self.assertEqual([e["recording_id"] for e in st["examples"]], ["p10-a"])
        self.assertNotIn("examples_note", st)
        none = decision.decision_state(self.conn, C, seg, neighbors=0, examples=1, example_labels=["sharp"])
        self.assertEqual(none["examples"], [])
        self.assertIn("sharp", none["examples_note"])
        wide = decision.decision_state(self.conn, C, seg, neighbors=0, examples=1, example_match_keys=["machine"])
        self.assertEqual(wide["examples"], st["examples"])
        b = decision.segment_at(self.conn, C, "p02-b", 0)       # channel b has no labelled case at all
        self.assertIn("examples_note", decision.decision_state(self.conn, C, b, neighbors=0, examples=2))

    def test_quantised_step_equals_the_sql_function(self):
        """decision.quantised_step (numpy) gives morlet_quantised_step's answer on grids, near-grids and edge cases."""
        import random
        rnd = random.Random(5)
        cols = [[], [1.0], [1.0, 2.0], [0.0, -0.0, 0.5, 1.0], [1.0, 1.0, 1.0, 2.0, 3.0], [0.1, 0.2, 0.3, 0.7],
                [0.0, 1e-7, 2e-7, 1.0], [0.0, 1.0, 2.5], [float("nan"), 0.0, 0.25, 0.5], [0.0, 1.0, 2e6],
                [0.0, 1.0, 1e6], [float("inf"), 1.0, 2.0, 4.0], [3.0, 1.0, 2.0]]
        for _ in range(60):
            step = rnd.choice([0.001, 0.01, 0.1, 0.25, 1.0, 9.81 / 1024])
            col = [round(rnd.randint(-500, 500) * step, rnd.choice([3, 6, 12])) for _ in range(rnd.randint(3, 400))]
            if rnd.random() < 0.3:
                col.append(rnd.uniform(-1, 1))                      # off the grid
            cols.append(col)
        for col in cols:
            want = self.conn.execute("SELECT morlet_quantised_step(%s::float8[])", (col,)).fetchone()[0]
            got = decision.quantised_step(col)
            self.assertEqual(got, want, col[:8])

    def test_segments_at_equals_segment_at(self):
        pts = [("p03-a", 0), ("p07-b", 100), ("p12-a", 2047)]
        self.assertEqual(decision.segments_at(self.conn, C, pts),
                         [decision.segment_at(self.conn, C, r, s) for r, s in pts])
        with self.assertRaises(ValueError):
            decision.segments_at(self.conn, C, [("p03-a", 0), ("p03-a", 5000)])

    def test_errors_name_the_segment(self):
        with self.assertRaises(ValueError):
            decision.decision_states(self.conn, C, [self.seg12, 10 ** 12])
        with self.assertRaises(ValueError):
            decision.decision_state(self.conn, C, self.seg12, match_keys=["no_such_key"])


    def test_value_rules_v2_in_the_decision_state(self):
        """Study V2 (#387): the decision state follows the collection's value rules, as the SQL functions do."""
        c = self.conn
        if c.execute("SELECT to_regproc('public.morlet_value_rules') IS NULL").fetchone()[0]:
            self.skipTest("server without value rules (SQL 0.3.9)")
        for t in ("labels", "ctx_values", "ctx_segments", "ctx", "chunks", "segments"):
            c.execute(f"DROP TABLE IF EXISTS public.dq_{t} CASCADE")
        c.execute("DROP TABLE IF EXISTS public.dq CASCADE")
        c.execute("SELECT morlet_create_stream_collection('public','dq'); SELECT morlet_enable_context('dq')")
        kinds = {0: "CASE WHEN i < 300 THEN 0 ELSE -0.0383 END",
                 1: "CASE WHEN i < 200 THEN 0 WHEN i < 500 THEN -0.0383 ELSE -2 * 0.0383 END",
                 2: "CASE WHEN i < 100 THEN 0 WHEN i < 200 THEN -0.0383 ELSE -2 * 0.0383 END"}
        for part, kind in ((1, 0), (2, 1), (3, 2), (4, 0), (5, 1), (6, 0), (7, 0)):
            rid = f"q{part}"
            c.execute("SELECT morlet_start_recording('dq',%s,1000,'acc','g','{}',512,256,'zscore','none',1)", (rid,))
            c.execute(f"SELECT morlet_append_chunk('dq',%s,0,ARRAY(SELECT ({kinds[kind]})::float8 "
                      f"FROM generate_series(0,999) i))", (rid,))
            ctx = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": 1000}
                   for k, v in (("machine", "M"), ("part", part))]
            c.execute("SELECT morlet_add_context('dq',%s,%s)", (f"b-{rid}", json.dumps(ctx)))
            c.execute("SELECT morlet_build_context_segments('dq',%s,ARRAY['machine'],ARRAY['part'])", (rid,))
            c.execute("SELECT morlet_build_context_values('dq',%s)", (rid,))
        seg = decision.segment_at(c, "public.dq", "q7", 0)
        for rules, status, rel in (("simple-v2", "zero_median", None), ("simple-v1", "ok", 1.0)):
            c.execute("SELECT morlet_set_value_rules('dq',%s)", (rules,))
            st = decision.decision_state(c, "public.dq", seg, neighbors=5)
            self.assertEqual(st["history"]["value_rules"], rules)
            p75 = next(v for v in st["values"] if v["name"] == "p75")
            self.assertEqual(p75["relative_deviation"], rel)
            sql = c.execute("SELECT status, relative_deviation FROM morlet_compare_to_history('dq',%s,min_history=>3) "
                            "WHERE value_name = 'p75'", (seg,)).fetchone()
            self.assertEqual(sql[1], rel)
            self.assertEqual(p75["status"], "ok" if sql[0] == "no_pooled_spread" else sql[0])
            self.assertEqual(p75["status"], status if rules == "simple-v2" else p75["status"])
            near = c.execute("SELECT recording_id, distance FROM morlet_history_neighbors('dq',%s,top_k=>5)",
                             (seg,)).fetchall()
            self.assertEqual([(n["recording_id"], n["distance"]) for n in st["neighbors"]],
                             [(r, decision._num(d)) for r, d in near])

    def test_states_and_precedents_carry_label_definitions(self):
        """#333: what each label shown means, and which labels nobody defined."""
        c = self.conn
        if c.execute("SELECT to_regproc('public.morlet_define_label') IS NULL").fetchone()[0]:
            self.skipTest("server without the label glossary (SQL 0.3.9)")
        st = decision.decision_state(c, C, self.seg12, neighbors=0, examples=2)
        self.assertEqual(st["undefined_labels"], ["worn"])
        self.assertIsNone(st["label_definitions"]["worn"])
        c.execute("SELECT morlet_define_label('dtest','worn','test: wear above the limit','mm','test fixture')")
        st = decision.decision_state(c, C, self.seg12, neighbors=0, examples=2)
        self.assertNotIn("undefined_labels", st)
        self.assertEqual(st["label_definitions"]["worn"],
                         {"definition": "test: wear above the limit", "unit": "mm", "source": "test fixture"})
        pre = decision.precedents({"a": st})
        self.assertEqual(pre["precedents"][0]["definitions"]["worn"]["unit"], "mm")
    def test_finish_recordings_in_parallel_equals_one_by_one(self):
        """#414: building a cut's channels on several connections gives the same segments and values."""
        import psycopg
        c = self.conn
        for t in ("labels", "ctx_values", "ctx_segments", "ctx", "chunks", "segments"):
            c.execute(f"DROP TABLE IF EXISTS public.fin_{t} CASCADE")
        c.execute("DROP TABLE IF EXISTS public.fin CASCADE")
        c.execute("SELECT morlet_create_stream_collection('public','fin'); SELECT morlet_enable_context('fin')")
        jobs = {"par": [], "seq": []}
        for mode in jobs:
            for ch in range(6):
                rid = f"{mode}-c{ch}"
                c.execute("SELECT morlet_start_recording('fin',%s,1000,%s,'g','{}',512,256,'zscore','none',1)",
                          (rid, f"c{ch}"))
                c.execute("SELECT morlet_append_chunk('fin',%s,0,ARRAY(SELECT (sin(i/(3.0+%s))*(1+%s))::float8 "
                          "FROM generate_series(0,2999) i))", (rid, ch, ch))
                ctx = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": 3000}
                       for k, v in (("machine", "M"), ("cut", f"{mode}/1"))]
                jobs[mode].append({"recording_id": rid, "batch_id": f"b-{rid}", "context": ctx,
                                   "segment_keys": ["machine"], "occurrence_keys": ["cut"]})
        pool = [psycopg.connect(URL, autocommit=True) for _ in range(3)]
        try:
            res = decision.finish_recordings(pool, "public.fin", jobs["par"])
        finally:
            for p in pool:
                p.close()
        self.assertEqual([r["recording_id"] for r in res], [j["recording_id"] for j in jobs["par"]])
        self.assertTrue(all(r["segments"] == 1 and r["values"] == 1 for r in res))
        decision.finish_recordings([c], "public.fin", jobs["seq"])
        vals = dict(c.execute("SELECT s.recording_id, v.vals FROM fin_ctx_values v JOIN fin_ctx_segments s "
                              "USING (segment_id)").fetchall())
        for ch in range(6):
            self.assertEqual(vals[f"par-c{ch}"], vals[f"seq-c{ch}"])
        # an identical retry adds no context rows and builds nothing new
        self.assertEqual(decision.finish_recordings([c], "public.fin", jobs["seq"])[0]["context_rows"], 0)
        with self.assertRaises(ValueError):
            decision.finish_recordings([], "public.fin", jobs["seq"])

    def test_events_inside_the_segment_are_listed(self):
        """#421: alarms and PLC messages stored as context events show up in the state of their segment."""
        c = self.conn
        rows = [{"recording_id": "p12-a", "key": "alarm", "value": {"code": "2041", "text": "spindle overload"},
                 "start_sample": 1005},
                {"recording_id": "p11-a", "key": "alarm", "value": {"code": "17"}, "start_sample": 10}]
        c.execute("SELECT morlet_add_context('dtest','alarms-test',%s)", (json.dumps(rows),))
        st = decision.decision_state(c, C, self.seg12, neighbors=0)
        self.assertEqual([(e["key"], e["value"]["code"], e["sample"]) for e in st["events"]], [("alarm", "2041", 1005)])
        self.assertAlmostEqual(st["events"][0]["offset_s"], 1.005)
        other = decision.decision_state(c, C, decision.segment_at(c, C, "p03-a", 0), neighbors=0)
        self.assertNotIn("events", other)

    def test_the_mcp_tools(self):
        os.environ["DATABASE_URL"] = URL
        r = asyncio.run(m.mcp.call_tool("decision_state", {"collection": C, "recording_id": "p12-a", "sample": 100,
                                                            "neighbors": 2}))
        st = json.loads(r.content[0].text)
        self.assertEqual(st["segment"]["recording_id"], "p12-a")
        r = asyncio.run(m.mcp.call_tool("decision_state", {"collection": C, "recording_id": "p12-a", "sample": 0,
                                                            "baseline_recordings": "p01-a,p02-a@5,p03-a",
                                                            "examples": 2, "example_labels": "worn"}))
        st2 = json.loads(r.content[0].text)
        self.assertEqual(st2["own_baseline"]["segments"], 3)
        self.assertEqual(st2["examples"][0]["recording_id"], "p10-a")
        self.assertEqual(len(st["neighbors"]), 2)
        r = asyncio.run(m.mcp.call_tool("decisions", {"collection": C, "recording_prefix": "p12"}))
        self.assertIn("decisions", json.loads(r.content[0].text))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class HistoryCacheOnADatabase(unittest.TestCase):
    """decision_states with a HistoryCache equals decision_states without one, while the data changes between calls."""

    CC = "public.dcache"

    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.conn = psycopg.connect(URL, autocommit=True)
        for t in ("label_definitions", "labels", "ctx_values", "ctx_segments", "ctx", "chunks", "segments"):
            cls.conn.execute(f"DROP TABLE IF EXISTS public.dcache_{t} CASCADE")
        cls.conn.execute("DROP TABLE IF EXISTS public.dcache CASCADE")
        cls.conn.execute("SELECT morlet_create_stream_collection('public','dcache'); SELECT morlet_enable_context('dcache')")
        for part in range(1, 9):
            cls.add(part)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    @classmethod
    def add(cls, part, values=True, tool=7):
        c = cls.conn
        for ch in ("a", "b"):
            rid = f"p{part:02d}-{ch}"
            c.execute("SELECT morlet_start_recording('dcache',%s,1000,%s,'V','{}',512,256,'zscore','none',1)", (rid, ch))
            c.execute("SELECT morlet_append_chunk('dcache',%s,0,%s::float8[])", (rid, wave(part)))
            ctx = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": 2048}
                   for k, v in (("machine", "M1"), ("tool", tool), ("part", f"P{part}"))]
            c.execute("SELECT morlet_add_context('dcache',%s,%s)", (f"b-{rid}-{tool}", json.dumps(ctx)))
            c.execute("SELECT morlet_build_context_segments('dcache',%s,ARRAY['machine','tool'],ARRAY['part'])", (rid,))
            if values:
                c.execute("SELECT morlet_build_context_values('dcache',%s)", (rid,))

    def same(self, cache, rids=("p08-a", "p08-b"), **opts):
        segs = decision.segments_at(self.conn, self.CC, [(r, 5) for r in rids])
        a = decision.decision_states(self.conn, self.CC, segs, neighbors=3, examples=1, **opts)
        b = decision.decision_states(self.conn, self.CC, segs, neighbors=3, examples=1, history_cache=cache, **opts)
        self.assertEqual(json.dumps(a, sort_keys=True, default=str), json.dumps(b, sort_keys=True, default=str))
        return b

    def test_the_cache_follows_every_change(self):
        cache = decision.HistoryCache()
        n0 = self.same(cache)[0]["history"]["size"]
        self.add(9)                                       # a new cut: fetched alone
        self.assertEqual(self.same(cache, ("p09-a", "p09-b"))[0]["history"]["size"], n0 + 1)
        self.add(10, values=False)                        # segments first, values later
        self.same(cache)
        self.conn.execute("SELECT morlet_build_context_values('dcache','p10-a')")
        self.conn.execute("SELECT morlet_build_context_values('dcache','p10-b')")
        self.same(cache)
        lid = self.conn.execute("SELECT morlet_add_label('dcache','p03-a',0,2048,'worn','test')").fetchone()[0]
        self.same(cache)
        self.same(cache, exclude_labels=["worn"])
        self.conn.execute("SELECT morlet_retract_label('dcache',%s)", (lid,))
        self.same(cache, exclude_labels=["worn"])
        self.conn.execute("SELECT morlet_build_context_segments('dcache','p04-a',ARRAY['machine'],ARRAY['part'])")  # p04-a's segment goes stale
        self.same(cache)
        self.conn.execute("DELETE FROM dcache WHERE recording_id = 'p05-b'")          # a recording deleted
        self.same(cache)
        self.add(11, tool=8)                              # another tool: not in this history
        self.same(cache, match_keys=["machine", "tool"])
        self.same(cache, match_keys=["machine"])
