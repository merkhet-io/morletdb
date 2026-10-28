"""Synthetic checks of the C6 harness: python -I test_c6.py."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c6  # noqa: E402


def bearing(levels, n=1200, degrade_from=None, factor=1.6, seed=0):
    """Measurements under contexts drawn at random; std (and rms) set by the context's level,
    multiplied by `factor` from `degrade_from` (fraction of life) on."""
    rng = np.random.default_rng(seed)
    names = [repr((k, 6, 1, "noise")) for k in range(len(levels))]
    pick = rng.integers(0, len(levels), n)
    std = np.array(levels)[pick] * (1 + 0.02 * rng.standard_normal(n))
    if degrade_from is not None:
        std[int(degrade_from * n):] *= factor
    v = np.ones((n, 8))
    v[:, 0] = 1.6
    v[:, 1] = 0.01
    v[:, 2] = v[:, 3] = std
    v[:, 4] = v[:, 5] = std
    v[:, 6] = 3 * (1 + 0.02 * rng.standard_normal(n))
    v[:, 7] = 3 * (1 + 0.02 * rng.standard_normal(n))
    return {"values": np.stack([v, v]), "context": np.array([names[k] for k in pick]), "t": np.arange(n) * 12.0}


class Resemblance(unittest.TestCase):
    def test_distance_is_the_products(self):
        lib = np.array([[1.6, 0, 2.0, 0, 0, 0, 3.0, 4.0], [1.6, 0, 4.0, 0, 0, 0, 3.0, 2.0],
                        [1.6, 0, 6.0, 0, 0, 0, 3.0, 6.0]])
        sc = c6.scales(lib)
        self.assertEqual(sc, {0: 1.6, 2: 4.0, 6: 3.0, 7: 4.0})
        d = c6.distances(np.array([1.6, 0, 4.0, 0, 0, 0, 3.0, 4.0]), lib, sc)
        self.assertAlmostEqual(d[0], np.sqrt((0.5 ** 2) / 4))
        self.assertAlmostEqual(d[2], np.sqrt((0.5 ** 2 + 0.5 ** 2) / 4))

    def test_needs_three_eligible_contexts(self):
        d = bearing([1.0, 2.0])
        v, ctx = d["values"][0], d["context"]
        sc = c6.scales(v[:150])
        self.assertIsNone(c6.resemblance(v[200], ctx[200], v[:150], ctx[:150], sc))

    def test_resembles_the_nearer_condition(self):
        d = bearing([1.0, 2.0, 3.0])
        v, ctx = d["values"][0], d["context"]
        lib, lc = v[:150], ctx[:150]
        x = v[200].copy()
        own = repr((0, 6, 1, "noise"))
        x[2] = 1.9  # a level-1 context measurement that looks like level 2
        r = c6.resemblance(x, own, lib, lc, c6.scales(lib))
        self.assertGreater(r["S"], 0)
        self.assertEqual(r["other"], repr((1, 6, 1, "noise")))


class Bearing(unittest.TestCase):
    def test_healthy_bearing_passes_gate_without_enrichment(self):
        r = c6.unit(bearing([1.0, 2.0, 3.0]))
        self.assertTrue(r["gate"]["pass"])
        self.assertLess(abs(r["Q1"]["enrichment"]), 0.05)

    def test_context_free_signal_fails_gate(self):
        r = c6.unit(bearing([2.0, 2.0, 2.0]))
        self.assertFalse(r["gate"]["pass"])

    def test_degradation_that_mimics_heavier_load_is_enriched_late(self):
        r = c6.unit(bearing([1.0, 2.0, 3.0], degrade_from=0.8))
        self.assertTrue(r["gate"]["pass"])
        self.assertGreater(r["Q1"]["enrichment"], 0.4)
        self.assertGreater(r["direction"]["late flagged"]["speed_higher"], 0.9)
        self.assertLessEqual(r["Q2"]["late"], r["Q1"]["late"])

    def test_swapped_contexts_are_flagged(self):
        r = c6.unit(bearing([1.0, 2.0, 3.0]))
        self.assertGreater(r["swap"]["flag_rate_swapped"], 0.9)
        self.assertLess(r["swap"]["flag_rate_true"], 0.1)


class Decision(unittest.TestCase):
    def rows(self, e, gate=True):
        return {f"B{i}": {"gate": {"pass": gate}, "Q1": {"enrichment": x}} for i, x in enumerate(e)}

    def test_rule(self):
        self.assertEqual(c6.decide(self.rows([0.3, 0.25, 0.4, 0.35, 0.3]), [f"B{i}" for i in range(5)], "Q1")["verdict"],
                         "supported")
        self.assertEqual(c6.decide(self.rows([0.0, 0.01, -0.02, 0.02, 0.0]), [f"B{i}" for i in range(5)], "Q1")["verdict"],
                         "not supported")
        self.assertEqual(c6.decide(self.rows([0.3] * 4), [f"B{i}" for i in range(4)], "Q1")["verdict"],
                         "not testable here")
        self.assertEqual(c6.decide(self.rows([0.3] * 6, gate=False), [f"B{i}" for i in range(6)], "Q1")["verdict"],
                         "not testable here")


if __name__ == "__main__":
    unittest.main()
