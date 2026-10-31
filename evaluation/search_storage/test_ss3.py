#!/usr/bin/env python3
"""SS3 bookkeeping checks (no database)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ss3  # noqa: E402


class Filters(unittest.TestCase):
    def test_levels_use_the_query_station(self):
        self.assertEqual(ss3.level_filter("L1", "PB06.d01.0"), {"station": "PB06", "day": 1})
        self.assertEqual(ss3.level_filter("L4", "PB06.d01.0"), {"tiers": [2]})
        self.assertEqual(ss3.level_filter("L5", "PB06.d01.0"), {})

    def test_containment_check_matches_jsonb_semantics(self):
        md = {"station": "PB01", "day": 3, "tiers": [2, 3]}
        self.assertTrue(ss3.satisfies(md, {"tiers": [2]}))
        self.assertTrue(ss3.satisfies(md, {"station": "PB01", "tiers": [2, 3]}))
        self.assertFalse(ss3.satisfies(md, {"day": 1}))
        self.assertFalse(ss3.satisfies(md, {"tiers": [1]}))


class Threshold(unittest.TestCase):
    def run_decide(self, points):
        with tempfile.TemporaryDirectory() as d:
            levels = {l: {"eligible_windows": {"mean": n}, "E": {"p95_ms": p},
                          "PP": {"recall_mean": 0.8, "p95_ms": 50}, "PI": {"recall_mean": 0.9, "p95_ms": 60}}
                      for l, (n, p) in zip(ss3.LEVELS, points)}
            Path(d, "ss3_morlet_r2.json").write_text(json.dumps({"levels": levels}))
            ss3.decide(Path(d))
            return json.loads(Path(d, "ss3_decision.json").read_text())

    def test_interpolates_the_100_ms_crossing_and_picks_the_better_recall(self):
        r = self.run_decide([(4000, 40), (16000, 80), (60000, 280), (70000, 300), (235000, 1000)])
        self.assertEqual(r["A"], "PI")
        self.assertAlmostEqual(r["T"], 16000 + 20 * 44000 / 200)

    def test_bounds(self):
        self.assertEqual(self.run_decide([(4000, 150)] * 5)["T"], 0.0)
        self.assertEqual(self.run_decide([(4000, 10), (16000, 20), (60000, 30), (70000, 40), (235000, 90)])["T"], 235000)


if __name__ == "__main__":
    unittest.main()
