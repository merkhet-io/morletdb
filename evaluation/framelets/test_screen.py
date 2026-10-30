#!/usr/bin/env python3
"""Transform checks for the F1 screen (synthetic signals only, no EEG)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import screen  # noqa: E402

RNG = np.random.default_rng(3)


class Framelet(unittest.TestCase):
    def test_parseval(self):
        x = RNG.normal(size=(4, 3000))
        bands, low = screen.framelet(x)
        energy = sum(np.sum(c ** 2, -1) for pair in bands for c in pair) + np.sum(low ** 2, -1)
        np.testing.assert_allclose(energy, np.sum(x ** 2, -1), rtol=1e-10)

    def test_sine_lands_in_its_level(self):
        t = np.arange(3000) / 100
        for j, (lo, hi) in enumerate(screen.OCTAVES):
            f = np.sqrt(lo * hi) if hi < 50 else 35.0
            bands, _ = screen.framelet(np.sin(2 * np.pi * f * t)[None])
            e = [float(np.sum(c1 ** 2 + c2 ** 2)) for c1, c2 in bands]
            self.assertEqual(int(np.argmax(e)), j, (f, np.round(e, 1)))


class GHM(unittest.TestCase):
    def test_orthonormal_step(self):
        v = RNG.normal(size=(3, 64, 2))
        s, d = screen.ghm_step(v)
        np.testing.assert_allclose(np.sum(s ** 2) + np.sum(d ** 2), np.sum(v ** 2), rtol=1e-12)
        np.testing.assert_allclose(screen.ghm_inverse_step(s, d), v, atol=1e-12)

    def test_six_levels_preserve_energy(self):
        x = RNG.normal(size=(2, 3000))
        details, low = screen.ghm(x)
        self.assertEqual(low.shape, (2, 24, 2))
        ext = screen.extend(x)
        energy = sum(np.sum(d ** 2, (-1, -2)) for d in details) + np.sum(low ** 2, (-1, -2))
        np.testing.assert_allclose(energy, np.sum(ext ** 2, -1), rtol=1e-10)

    def test_slow_sine_is_mostly_coarse(self):
        t = np.arange(3000) / 100
        details, _ = screen.ghm(np.sin(2 * np.pi * 2.0 * t)[None])
        e = [float(np.sum(d ** 2)) for d in details]
        self.assertLess(sum(e[:3]), 0.05 * sum(e))


class Features(unittest.TestCase):
    def test_shapes_and_finite(self):
        f = screen.epoch_features(RNG.normal(size=(5, 3000)) * 20)
        self.assertEqual(f["fr_e"].shape, (5, 12))
        self.assertEqual(f["mw_e"].shape, (5, 12))
        self.assertEqual(f["fr_k"].shape, (5, 6))
        self.assertEqual(f["bp_k"].shape, (5, 6))
        for v in f.values():
            self.assertTrue(np.all(np.isfinite(v)))

    def test_bursts_raise_kurtosis(self):
        t = np.arange(3000) / 100
        steady = np.sin(2 * np.pi * 9.0 * t)
        burst = steady * (np.abs(t - 15) < 0.7)   # a spindle-like burst in the 6.25-12.5 Hz octave
        f = screen.epoch_features(np.stack([steady, burst]) * 30 + RNG.normal(size=(2, 3000)) * 0.1)
        self.assertGreater(f["fr_k"][1, 2], f["fr_k"][0, 2] + 1)
        self.assertGreater(f["bp_k"][1, 2], f["bp_k"][0, 2] + 1)


if __name__ == "__main__":
    unittest.main()
