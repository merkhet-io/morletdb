"""Synthetic controls for the C1 harness. They check mechanics, not evidence."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c1  # noqa: E402

CONTEXTS = {1: (1.5, .5, 1), 2: (.75, .5, 1), 3: (.75, .25, 1), 4: (1.5, .25, 1),
            5: (1.5, .5, 2), 6: (1.5, .25, 2), 7: (.75, .25, 2), 8: (.75, .5, 2)}


def synthetic(context_effect, seed=0, runs=12):
    rng = np.random.default_rng(seed)
    level = {ctx: 1 + context_effect * i for i, ctx in enumerate(CONTEXTS.values())}
    out = []
    for case in range(1, 17):
        ctx = CONTEXTS[case if case <= 8 else c1.TWINS[case]]
        for k in range(runs):
            vb = 0.08 + 0.04 * k + rng.normal(0, 0.01)
            base = level[ctx] * (1 + 0.8 * vb)
            out.append({"case": case, "run": k + 1, "time": 3.0 * k, "vb": float(vb), "context": ctx,
                        **{v: float(base * (1 + rng.normal(0, 0.02))) for v in c1.VALUES}})
    return out


class C1(unittest.TestCase):
    def test_conditioning_helps_when_context_shifts_levels(self):
        res = c1.analyse(synthetic(context_effect=0.5), resamples=200)
        self.assertGreater(res["rho_conditioned"], 0.8)
        self.assertGreater(res["delta"], 0.1)
        self.assertEqual(res["decision"], "supported")

    def test_no_gain_without_context_effect(self):
        res = c1.analyse(synthetic(context_effect=0.0), resamples=200)
        self.assertLess(abs(res["delta"]), 0.1)
        self.assertNotEqual(res["decision"], "supported")

    def test_short_history_excludes_the_case(self):
        runs = [r for r in synthetic(0.5) if not (r["case"] == 6 and r["run"] > 1)]
        res = c1.analyse(runs, resamples=50)
        self.assertEqual(res["excluded_cases"], [15])
        self.assertNotIn(15, res["cases"])

    def test_twin_mismatch_is_rejected(self):
        runs = synthetic(0.5)
        for r in runs:
            if r["case"] == 9:
                r["context"] = (0.75, .5, 2)
        with self.assertRaises(ValueError):
            c1.analyse(runs, resamples=10)

    def test_small_history_makes_robust_z_unstable(self):
        # Why the primary score is relative (amendment 1): with 3-4 reference runs, MAD is
        # noisy, and the conditioned robust z loses rank correlation without any context effect.
        res = c1.analyse(synthetic(context_effect=0.0), resamples=10)
        z = res["reported"]["robust_z_rho"]
        self.assertGreater(z["context_free"] - z["conditioned"], 0.2)

    def test_robust_z(self):
        ref = [{v: x for v in c1.VALUES} for x in (1.0, 2.0, 3.0, 4.0, 5.0)]
        run = {v: 6.0 for v in c1.VALUES}
        self.assertAlmostEqual(c1.robust_score(run, ref), 3.0 / 1.4826)


if __name__ == "__main__":
    unittest.main()
