"""Synthetic controls for the C3 harness. They check mechanics, not evidence."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c3  # noqa: E402


def rows(n, rng, bad_std=1.0, kurt_noise=0.05, bad_frac=0.0):
    out = []
    for i in range(n):
        bad = i < int(bad_frac * n)
        r = {v: 1.0 for v in c3.VALUES}
        for a in "xyz":
            r[f"std_{a}"] = (bad_std if bad else 1.0) * (1 + 0.02 * rng.normal())
            r[f"rms_{a}"] = r[f"std_{a}"]
            r[f"kurtosis_{a}"] = 3 * (1 + kurt_noise * rng.normal())
            r[f"crest_{a}"] = 4 * (1 + 0.02 * rng.normal())
        out.append({**r, "bad": bad})
    return out


class C3(unittest.TestCase):
    def test_simple_v1_matches_rust_reference(self):
        v = c3.simple_v1(np.array([1.0, 2.0, 3.0, 4.0]))
        np.testing.assert_allclose(v, [4 / 8000, 2.5, 1.25 ** 0.5, 7.5 ** 0.5, 3.25, 4.0, 4 / 7.5 ** 0.5, 1.64])

    def test_calibration_helps_when_a_noisy_value_masks_a_level_change(self):
        rng = np.random.default_rng(0)
        train = rows(200, rng, kurt_noise=0.5)
        test = rows(100, rng, bad_std=1.15, kurt_noise=0.5, bad_frac=0.4)
        res = c3.analyse(train, test, resamples=200)
        self.assertGreater(res["auroc"]["B_calibrated"], 0.95)
        self.assertGreater(res["delta_B_minus_A"], 0.1)
        self.assertEqual(res["decision"], "supported")

    def test_no_gain_without_signal(self):
        rng = np.random.default_rng(1)
        res = c3.analyse(rows(200, rng), rows(100, rng, bad_frac=0.4), resamples=200)
        self.assertLess(abs(res["auroc"]["B_calibrated"] - 0.5), 0.15)
        self.assertNotEqual(res["decision"], "supported")

    def test_spread_floor(self):
        hist = [{v: 5.0 for v in c3.VALUES} for _ in range(10)]
        sp = c3.spreads(hist, c3.VALUES)
        self.assertTrue(all(s == c3.FLOOR for s in sp.values()))


if __name__ == "__main__":
    unittest.main()
