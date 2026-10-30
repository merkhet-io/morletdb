#!/usr/bin/env python3
"""Synthetic checks of the G1 harness (no CWRU or XJTU-SY data)."""
import sys
import unittest
from pathlib import Path

import numpy as np
from scipy.signal import hilbert

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1  # noqa: E402

FS = g1.CWRU_FS


def bearing_signal(fault_hz, n=g1.CWRU_N, fs=FS, resonance=3000.0, noise=0.5, seed=0):
    """Decaying resonance bursts at fault_hz, plus a shaft tone and white noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    x = np.zeros(n)
    for t0 in np.arange(0, t[-1], 1 / fault_hz):
        k = int(t0 * fs)
        tt = t[k:] - t[k]
        x[k:] += np.exp(-tt * 800) * np.sin(2 * np.pi * resonance * tt) * (1 + 0.1 * rng.normal())
    return x + 3 * np.sin(2 * np.pi * 29.95 * t) + noise * rng.normal(size=n)


def measured_erb(fc, bw, fs=96000.0):
    g = g1.gammatone_ir(fc, bw, fs)
    H = np.abs(np.fft.rfft(g, 1 << 20)) ** 2
    f = np.fft.rfftfreq(1 << 20, 1 / fs)
    return float(np.sum(H) * (f[1] - f[0]) / H.max()), float(f[np.argmax(H)])


class Ground(unittest.TestCase):
    def test_files(self):
        self.assertEqual(len(g1.CWRU_FAULTS), 60)
        self.assertEqual(len({m["cluster"] for m in g1.CWRU_FAULTS.values()}), 15)
        self.assertEqual(sorted({m["element"] for m in g1.CWRU_FAULTS.values()}), ["ball", "inner", "outer"])
        self.assertTrue(set(g1.PREVIOUSLY_USED) <= set(g1.CWRU_FAULTS))

    def test_frequencies(self):
        f = g1.cwru_freqs(0)
        self.assertAlmostEqual(f["outer"], 3.5848 * 1797 / 60, places=6)
        self.assertAlmostEqual(f["inner"], 162.19, places=1)


class Gammatone(unittest.TestCase):
    def test_glasberg_moore(self):
        self.assertAlmostEqual(g1.erb(1000.0), 132.639, places=3)
        self.assertAlmostEqual(float(g1.erb_number_inv(g1.erb_number(2345.0))), 2345.0, places=6)

    def test_erb_factor_order_4(self):
        self.assertAlmostEqual(g1.GT_ERB_FACTOR, 0.98175, places=5)

    def test_filter_has_requested_bandwidth_and_centre(self):
        for fc, bw in ((1000.0, g1.erb(1000.0)), (3000.0, 750.0), (4500.0, 1500.0)):
            e, peak = measured_erb(fc, bw)
            self.assertAlmostEqual(e / bw, 1.0, delta=0.03)
            self.assertAlmostEqual(peak, fc, delta=0.02 * fc)

    def test_envelope_peaks_at_three_over_two_pi_b(self):
        fc, bw, fs = 3000.0, 600.0, 96000.0
        g = g1.gammatone_ir(fc, bw, fs)
        b = bw / g1.GT_ERB_FACTOR
        env = np.abs(hilbert(g))
        self.assertAlmostEqual(np.argmax(env) / fs, 3 / (2 * np.pi * b), delta=2 / fs + 0.05 * 3 / (2 * np.pi * b))

    def test_erb_bank(self):
        bank = g1.erb_bank(FS)
        e = g1.erb_number(np.array([fc for fc, _ in bank]))
        np.testing.assert_allclose(np.diff(e), g1.ERB_STEP, atol=1e-9)
        self.assertTrue(all(fc - bw / 2 >= 500 and fc + bw / 2 <= FS / 2 for fc, bw in bank))
        self.assertGreater(len(bank), 15)


class Bands(unittest.TestCase):
    def test_cwru_grid(self):
        b = g1.fk_bands(FS, g1.CWRU_FK_LEVELS)
        self.assertTrue(all(lo >= 500 and hi - lo >= 500 - 1e-9 for _, lo, hi in b))
        self.assertEqual({lv for lv, _, _ in b}, set(g1.CWRU_FK_LEVELS))

    def test_matches_w2_grid_at_25k6(self):
        self.assertEqual(g1.fk_bands(g1.w2.FS, g1.w2.FK_LEVELS), g1.w2.fk_bands())


class Pipeline(unittest.TestCase):
    def test_outer_race_fault_is_found(self):
        freqs = g1.cwru_freqs(0)
        x = bearing_signal(freqs["outer"])
        arms = g1.snapshot_arms(x, freqs, FS, g1.CWRU_FK_LEVELS)
        self.assertEqual(set(arms), set(g1.ARMS))
        for arm in ("FK-RER", "GTFK-RER", "GTERB-RER"):
            label, (f, a) = arms[arm]
            self.assertTrue(g1.w2.detected(f, a, freqs["outer"]), (arm, label))
            self.assertGreater(g1.w2.peak_ratio(f, a, freqs["outer"]), 5, arm)

    def test_pure_noise_is_not_a_fault(self):
        freqs = g1.cwru_freqs(0)
        x = np.random.default_rng(3).normal(size=g1.CWRU_N)
        arms = g1.snapshot_arms(x, freqs, FS, g1.CWRU_FK_LEVELS)
        for arm in ("FK", "GTFK-K", "GTERB-K", "RAW"):
            f, a = arms[arm][1]
            self.assertLess(g1.w2.peak_ratio(f, a, freqs["outer"]), 10, arm)


if __name__ == "__main__":
    unittest.main()
