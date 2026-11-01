#!/usr/bin/env python3
"""R2 client-side semantics: exclusion and greedy overlap suppression match pg_morlet's rules."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench as B  # noqa: E402


class Suppress(unittest.TestCase):
    def test_source_interval_is_excluded(self):
        ranked = [("r", 0, 0.0), ("r", 2048, 0.1), ("r", 4096, 0.2), ("s", 0, 0.3)]
        self.assertEqual(B.suppress(ranked, "r", 0), [("r", 4096), ("s", 0)])

    def test_overlaps_are_suppressed_in_rank_order(self):
        ranked = [("s", 2048, 0.0), ("s", 0, 0.1), ("s", 4096, 0.2), ("s", 6144, 0.3), ("t", 0, 0.4)]
        self.assertEqual(B.suppress(ranked, "r", 0), [("s", 2048), ("s", 6144), ("t", 0)])

    def test_stops_at_top_k(self):
        ranked = [(f"x{i}", 0, i) for i in range(20)]
        self.assertEqual(len(B.suppress(ranked, "r", 0)), B.TOP)

    def test_tiers_are_nested(self):
        self.assertEqual(B_load.tiers("PB01", 1), [0, 1, 2, 3])
        self.assertEqual(B_load.tiers("PB02", 1), [1, 2, 3])
        self.assertEqual(B_load.tiers("PB02", 4), [2, 3])
        self.assertEqual(B_load.tiers("PSGCX", 14), [3])


import load as B_load  # noqa: E402

if __name__ == "__main__":
    unittest.main()
