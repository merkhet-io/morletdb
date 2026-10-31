#!/usr/bin/env python3
"""BL1 correctness controls (synthetic; no database)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probes as P  # noqa: E402

RNG = np.random.default_rng(11)
N = 410


def env():
    return P.zscore(np.convolve(np.abs(RNG.normal(size=N + 49)), np.ones(50) / 50, mode="valid"))


class Identity(unittest.TestCase):
    def test_probe_inner_product_is_the_lag_term_at_every_lag(self):
        q, g = env(), env()
        for lag in range(-30, 31):
            self.assertAlmostEqual(float(P.shifted(q, lag) @ g), P.lagged_sum(q, g, lag), places=9)

    def test_max_over_all_probes_is_the_distance(self):
        q, g = env(), env()
        Q = P.probe_matrix(q, P.GRIDS["G61"])
        self.assertAlmostEqual(1 - (g @ Q).max() / N, P.distance(q, g), places=12)

    def test_cosine_would_not_be_comparable_across_probes(self):
        q = env()
        norms = {l: np.linalg.norm(P.shifted(q, l)) for l in (-30, 0, 30)}
        self.assertNotAlmostEqual(norms[0], norms[30], places=3)


class ShiftedMatch(unittest.TestCase):
    def setUp(self):
        self.q = env()
        self.G = np.stack([env() for _ in range(300)])
        self.keys = np.arange(len(self.G) + 1)

    def gallery_with(self, l0):
        target = P.zscore(P.shifted(self.q, -l0) + 0.05 * RNG.normal(size=N))   # best aligned at lag l0
        return np.vstack([self.G, target]), len(self.G)

    def test_full_grid_finds_every_shift(self):
        Q = P.probe_matrix(self.q, P.GRIDS["G61"])
        for l0 in (-30, -17, -1, 0, 4, 29, 30):
            G, t = self.gallery_with(l0)
            self.assertIn(t, P.union_probes(G, Q, 1, self.keys), l0)

    def test_coarse_grid_finds_on_grid_shifts(self):
        Q = P.probe_matrix(self.q, P.GRIDS["G7"])
        for l0 in (-30, -10, 0, 20):
            G, t = self.gallery_with(l0)
            self.assertIn(t, P.union_probes(G, Q, 1, self.keys), l0)

    def test_full_grid_union_contains_exact_top_ten(self):
        G = np.vstack([self.G, np.stack([P.zscore(P.shifted(self.q, -l) + RNG.normal(size=N)) for l in range(-30, 31, 3)])])
        keys = np.arange(len(G))
        exact = np.array([P.distance(self.q, g) for g in G])
        top10 = set(np.lexsort((keys, exact))[:10].tolist())
        union = set(P.union_probes(G, P.probe_matrix(self.q, P.GRIDS["G61"]), 10, keys))
        self.assertTrue(top10 <= union)


class Ordering(unittest.TestCase):
    def test_ties_break_by_key_and_include_all_tied(self):
        s = np.array([1.0, 3.0, 3.0, 3.0, 0.5])
        self.assertEqual(P.topk(s, 2, np.array([4, 3, 1, 2, 0])).tolist(), [2, 3])

    def test_union_deduplicates(self):
        G = np.stack([env() for _ in range(50)])
        q = env()
        u = P.union_probes(G, P.probe_matrix(q, P.GRIDS["G13"]), 20, np.arange(50))
        self.assertEqual(len(u), len(set(u)))

    def test_exclusion_and_suppression(self):
        ranked = [("r", 0, 0.0), ("s", 2048, 0.1), ("s", 0, 0.2), ("s", 4096, 0.3), ("t", 0, 0.4)]
        self.assertEqual(P.suppress(ranked, "r", 0), [("s", 2048), ("t", 0)])

    def test_decode_matches_float16_then_zscore(self):
        v = np.linspace(-2, 3, N)
        b = bytes([1]) + v.astype("<f2").tobytes()
        np.testing.assert_allclose(P.decode(b), P.zscore(v.astype(np.float16).astype(np.float64)))


if __name__ == "__main__":
    unittest.main()
