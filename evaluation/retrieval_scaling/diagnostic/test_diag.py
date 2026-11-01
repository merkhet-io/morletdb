#!/usr/bin/env python3
"""R2-D harness checks without a database."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag as D  # noqa: E402


class Plan(unittest.TestCase):
    def test_walk_finds_nested_scan(self):
        plan = {"Node Type": "Limit", "Plans": [{"Node Type": "Gather Merge", "Plans": [
            {"Node Type": "Sort", "Plans": [{"Node Type": "Parallel Seq Scan", "Relation Name": "r2_hv"}]}]}]}
        self.assertEqual([n["Node Type"] for n in D.plan_nodes(plan)], ["Limit", "Gather Merge", "Sort", "Parallel Seq Scan"])

    def test_exact_sql_breaks_ties_and_has_no_subquery_scan(self):
        self.assertIn("ORDER BY d, h.recording_id, h.start_sample", D.E_SQL)
        self.assertNotIn("SELECT v FROM", D.E_SQL)


if __name__ == "__main__":
    unittest.main()
