#!/usr/bin/env python3
"""Pairing, split-isolation and exact-search checks for the F1 gate (synthetic data only)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402


def synthetic():
    rng = np.random.default_rng(0)
    return {f"b{i}": np.cumsum(rng.normal(0, 0.01, 80 + 7 * i)) + i for i in range(5)}


class Pairing(unittest.TestCase):
    def test_continuation_is_the_future_of_its_own_context(self):
        traj = synthetic()
        C, D, ids = gate.gallery(traj, ["b1", "b3"], 10, 5)
        for row, (b, t) in enumerate(ids):
            y = traj[b]
            np.testing.assert_allclose(C[row], y[t - 4:t + 1] - y[t])
            self.assertAlmostEqual(D[row], y[t + 10] - y[t])
            self.assertLessEqual(t + 10, len(y) - 1)
            self.assertGreaterEqual(t, gate.MIN_T)

    def test_origins_are_shared_by_every_method(self):
        traj = synthetic()
        train = ["b0", "b2", "b3"]
        n = len(gate.origins(len(traj["b1"]), 10))
        for m, cfg in (("persistence", None), ("ar", 3), ("knn", (5, 1))):
            self.assertEqual(len(gate.predict(m, cfg, traj, train, "b1", 10)), n)


class Isolation(unittest.TestCase):
    def test_query_bearing_never_trains_or_retrieves(self):
        traj = synthetic()
        for m, cfg in (("ar", 2), ("knn", (5, 1))):
            with self.assertRaises(AssertionError):
                gate.predict(m, cfg, traj, ["b0", "b1"], "b1", 10)

    def test_identical_twin_in_gallery_is_found_exactly(self):
        # A gallery bearing equal to the query gives zero error with k = 1: exact search and pairing.
        traj = synthetic()
        traj["twin"] = traj["b2"].copy()
        pred = gate.predict("knn", (30, 1), traj, ["twin", "b0"], "b2", 10)
        y = traj["b2"]
        np.testing.assert_allclose(pred, y[gate.origins(len(y), 10) + 10], atol=1e-12)

    def test_shuffled_control_breaks_the_twin(self):
        traj = synthetic()
        traj["twin"] = traj["b2"].copy()
        pred = gate.predict("shuffled", (30, 1), traj, ["twin", "b0"], "b2", 10, np.random.default_rng(1))
        y = traj["b2"]
        self.assertGreater(np.abs(pred - y[gate.origins(len(y), 10) + 10]).mean(), 1e-3)


class Baselines(unittest.TestCase):
    def test_ar_recovers_a_linear_trend(self):
        traj = {f"b{i}": 0.01 * np.arange(100) + i for i in range(3)}
        pred = gate.predict("ar", 2, traj, ["b0", "b1"], "b2", 10)
        y = traj["b2"]
        np.testing.assert_allclose(pred, y[gate.origins(100, 10) + 10], atol=1e-6)


if __name__ == "__main__":
    unittest.main()
