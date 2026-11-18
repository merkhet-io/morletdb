"""Synthetic checks of the C8 harness (no data, no database): python -I test_c8.py."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c8  # noqa: E402


class Overlap(unittest.TestCase):
    def test_identical_and_disjoint(self):
        a = [f"r{i}" for i in range(12)]
        self.assertEqual(c8.overlap(a, a, 10)["overlap"], 1.0)
        b = [f"s{i}" for i in range(12)]
        o = c8.overlap(a, b, 10)
        self.assertEqual((o["overlap"], o["jaccard"], o["containment"]), (0.0, 0.0, 0.0))

    def test_only_first_k_count(self):
        a = ["x", "y", "z", "w"]
        b = ["y", "x", "w", "z"]
        o = c8.overlap(a, b, 2)
        self.assertEqual((o["overlap"], o["jaccard"]), (1.0, 1.0))
        o = c8.overlap(a, ["x", "w"], 2)
        self.assertEqual((o["overlap"], o["jaccard"]), (0.5, 1 / 3))

    def test_short_list_counts_missing_places(self):
        wide = [f"r{i}" for i in range(50)]
        narrow = wide[:20]
        o = c8.overlap(wide, narrow, 50)
        self.assertEqual(o["overlap"], 20 / 50)
        self.assertEqual(o["jaccard"], 20 / 50)
        self.assertEqual(o["containment"], 1.0)
        self.assertEqual((o["len_a"], o["len_b"]), (50, 20))

    def test_empty_list(self):
        o = c8.overlap([], ["a"], 10)
        self.assertEqual(o["overlap"], 0.0)
        self.assertIsNone(o["containment"])


class Ties(unittest.TestCase):
    def test_tie_at_boundary(self):
        d = [0.1, 0.2, 0.3, 0.3, 0.4]
        self.assertTrue(c8.tie_at(d, 3))
        self.assertFalse(c8.tie_at(d, 2))
        self.assertFalse(c8.tie_at(d, 5))  # no (k+1)-th element


class Outside(unittest.TestCase):
    def test_share_from_other_machine(self):
        meta = {"a": {"machine": "M01"}, "b": {"machine": "M02"}, "c": {"machine": "M01"}, "d": {"machine": "M03"}}
        q = {"machine": "M01"}
        self.assertEqual(c8.outside_share(["a", "b", "c", "d"], 4, q, meta, ["machine"]), 0.5)
        self.assertEqual(c8.outside_share(["a", "b", "c", "d"], 1, q, meta, ["machine"]), 0.0)
        self.assertIsNone(c8.outside_share([], 4, q, meta, ["machine"]))


class Bootstrap(unittest.TestCase):
    def test_constant_values_give_point_interval(self):
        lo, hi = c8.cluster_bootstrap([0.4] * 30, [i % 5 for i in range(30)])
        self.assertAlmostEqual(lo, 0.4)
        self.assertAlmostEqual(hi, 0.4)

    def test_interval_covers_mean_and_is_deterministic(self):
        rng = np.random.default_rng(1)
        v = rng.uniform(size=200)
        g = [i % 20 for i in range(200)]
        iv = c8.cluster_bootstrap(v, g)
        self.assertLessEqual(iv[0], v.mean())
        self.assertGreaterEqual(iv[1], v.mean())
        self.assertEqual(iv, c8.cluster_bootstrap(v, g))


class Verdict(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(c8.verdict(0.1, 0.49), "most neighbours change")
        self.assertEqual(c8.verdict(0.4, 0.79), "neighbours change")
        self.assertEqual(c8.verdict(0.9, 1.0), "little change")
        self.assertEqual(c8.verdict(0.85, 0.95), "intermediate")
        self.assertEqual(c8.verdict(0.3, 0.85), "intermediate")


class Ranking(unittest.TestCase):
    def test_expected_ranking_rule(self):
        names = ["duration_s", "mean", "std", "rms", "p75", "peak_abs", "crest", "kurtosis"]
        rng = np.random.default_rng(3)
        hist = [(f"r{i}", list(rng.uniform(1, 2, size=8))) for i in range(9)]
        q = list(rng.uniform(1, 2, size=8))
        got = c8.expected_ranking(q, hist, names, "simple-v1")
        V = np.array([v for _, v in hist])
        idx = [0, 2, 6, 7]
        med = np.median(V, axis=0)[idx]
        d = np.sqrt(np.mean(((np.array(q)[idx] - V[:, idx]) / np.abs(med)) ** 2, axis=1))
        self.assertEqual([r for r, _ in got], [hist[i][0] for i in np.argsort(d, kind="stable")])

    def test_zero_median_value_is_skipped(self):
        names = ["duration_s", "mean", "std", "rms", "p75", "peak_abs", "crest", "kurtosis"]
        hist = [(f"r{i}", [1.0, 0, 1.0 + i, 1.0, 0, 1, 0.0, 3.0]) for i in range(5)]  # crest median 0
        got = c8.expected_ranking([1.0, 0, 3.0, 1, 0, 1, 99.0, 3.0], hist, names, "simple-v1")
        self.assertEqual(got[0][0], "r2")
        self.assertAlmostEqual(got[0][1], 0.0)

    def test_v2_floor_on_quantised_history(self):
        names = ["duration_s", "mean", "std", "rms", "p75", "peak_abs", "crest", "kurtosis"]
        V = np.ones((6, 8))
        V[:, 6] = [-1.0, 0.0, 1.0, 0.0, -1.0, 1.0]  # quantised, step 1, median 0 -> floor 2
        self.assertEqual(c8.floors(V, names, "simple-v2")[6], 2.0)
        self.assertEqual(c8.floors(V, names, "simple-v1")[6], 0.0)


class Analyse(unittest.TestCase):
    def test_pipeline_on_synthetic_neighbours(self):
        meta = {f"c{c}-{k:03d}|vib_x": {"cutter": f"c{c}", "cut": k} for c in (1, 2) for k in range(1, 61)}
        queries = []
        for rid, m in meta.items():
            same = [r for r, mm in meta.items() if mm["cutter"] == m["cutter"] and r != rid]
            other = [r for r, mm in meta.items() if mm["cutter"] != m["cutter"]]
            queries.append({"recording_id": rid, "metadata": m, "neighbours": {
                "C": [[r, float(i)] for i, r in enumerate(same[:51])],
                "P": [[r, float(i)] for i, r in enumerate((same[:5] + other)[:51])]}})
        out = c8.analyse_dataset({"dataset": "phm", "recording_metadata": meta, "queries": queries})
        r10 = out["pairs"]["P-C@10"]
        self.assertAlmostEqual(r10["overlap"]["mean"], 0.5)
        self.assertAlmostEqual(r10["outside_share_of_wider"]["mean"], 0.5)
        self.assertEqual(r10["verdict"], "neighbours change")
        self.assertEqual(out["context_choice"], "matters")
        self.assertEqual(out["short_lists"]["C@10"]["short_lists"], 0)


if __name__ == "__main__":
    unittest.main()
