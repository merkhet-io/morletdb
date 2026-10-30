#!/usr/bin/env python3
"""Synthetic checks of the W2 harness (no XJTU-SY data)."""
import sys
import unittest
from pathlib import Path

import numpy as np
from numpy.polynomial import legendre as npleg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import w2  # noqa: E402

RNG = np.random.default_rng(5)


def bearing_signal(fault_hz, n=32768, fs=w2.FS, resonance=3000.0, noise=0.6, seed=0):
    """Decaying resonance bursts at fault_hz, plus a shaft tone and white noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    x = np.zeros(n)
    for t0 in np.arange(0, t[-1], 1 / fault_hz):
        k = int(t0 * fs)
        tt = t[k:] - t[k]
        x[k:] += np.exp(-tt * 800) * np.sin(2 * np.pi * resonance * tt) * (1 + 0.1 * rng.normal())
    return x + 3 * np.sin(2 * np.pi * 35 * t) + noise * rng.normal(size=n)


class CharFreqs(unittest.TestCase):
    def test_matches_dataset_paper_at_35hz(self):
        f = w2.char_freqs(35.0)
        self.assertAlmostEqual(f["outer"], 107.91, places=2)
        self.assertAlmostEqual(f["inner"], 172.09, places=2)
        self.assertAlmostEqual(f["ball"], 72.33, places=2)
        self.assertAlmostEqual(f["cage"], 13.49, places=2)

    def test_ground_truth_counts(self):
        self.assertEqual(len(w2.FAULTS), 15)
        self.assertEqual(sum(len(v) for v in w2.FAULTS.values()), 19)
        self.assertEqual(sum(len(w2.FAULTS[b]) for b in w2.COMPOUND), 6)


class Legendre(unittest.TestCase):
    def test_filter_bank_orthogonal(self):
        H0, H1, G0, G1 = w2.FILTERS
        W = np.block([[H0, H1], [G0, G1]])
        np.testing.assert_allclose(W @ W.T, np.eye(2 * w2.R_MULT), atol=1e-12)
        np.testing.assert_allclose(w2.PREFILTER @ w2.PREFILTER.T, np.eye(w2.R_MULT), atol=1e-12)

    def test_vanishing_moments(self):
        """Child-interval Legendre coefficients of a cubic are mapped to zero detail."""
        _, _, G0, G1 = w2.FILTERS
        r = w2.R_MULT
        xg, wg = npleg.leggauss(12)
        u, wu = (xg + 1) / 2, wg / 2
        phi = lambda i, x: np.sqrt(2 * i + 1) * npleg.Legendre.basis(i)(2 * x - 1)  # noqa: E731
        for deg in range(r):
            p = lambda x: (x - 0.3) ** deg  # noqa: E731
            c0 = np.array([np.sum(wu * p(u / 2) * phi(j, u)) for j in range(r)])
            c1 = np.array([np.sum(wu * p((u + 1) / 2) * phi(j, u)) for j in range(r)])
            np.testing.assert_allclose(G0 @ c0 + G1 @ c1, 0, atol=1e-12)

    def test_perfect_reconstruction_and_parseval(self):
        x = RNG.normal(size=4096)
        d, s = w2.lmwt(x)
        np.testing.assert_allclose(w2.ilmwt(d, s), x, atol=1e-10)
        energy = sum(np.sum(v ** 2) for v in d) + np.sum(s ** 2)
        self.assertAlmostEqual(energy, float(np.sum(x ** 2)), places=8)

    def test_level_signals_add_up(self):
        x = RNG.normal(size=4096)
        d, s = w2.lmwt(x)
        total = sum(w2.lmwt_level_signal(x, j) for j in w2.LMWT_LEVELS) + w2.ilmwt([np.zeros_like(v) for v in d], s)
        np.testing.assert_allclose(total, x, atol=1e-10)

    def test_levels_go_from_high_to_low_frequency(self):
        x = RNG.normal(size=32768)
        cents = []
        for j in w2.LMWT_LEVELS:
            y = w2.lmwt_level_signal(x, j)
            p = np.abs(np.fft.rfft(y)) ** 2
            f = np.fft.rfftfreq(len(y), 1 / w2.FS)
            cents.append(float(np.sum(f * p) / np.sum(p)))
        self.assertTrue(all(a > b for a, b in zip(cents, cents[1:])), cents)


class Kurtogram(unittest.TestCase):
    def test_grid(self):
        b = w2.fk_bands()
        self.assertTrue(all(lo >= w2.MIN_CARRIER for _, lo, _ in b))
        self.assertTrue(all(hi - lo >= 533 for _, lo, hi in b))
        self.assertEqual(len({lv for lv, _, _ in b}), len(w2.FK_LEVELS))

    def test_spectral_kurtosis_of_noise_is_near_zero(self):
        self.assertLess(abs(w2.spectral_kurtosis(RNG.normal(size=32768))), 0.1)


class EWT(unittest.TestCase):
    def test_tight_frame(self):
        x = bearing_signal(107.91)
        f = np.fft.rfftfreq(len(x), 1 / w2.FS)
        filt = w2.ewt_filters(w2.ewt_boundaries(x), f)
        self.assertEqual(len(filt), w2.EWT_MODES)
        total = sum(psi ** 2 for _, _, psi in filt)
        np.testing.assert_allclose(total, 1, atol=1e-9)


class Pipeline(unittest.TestCase):
    def test_outer_race_fault_is_found(self):
        freqs = w2.char_freqs(35.0)
        x = bearing_signal(freqs["outer"], noise=0.5)
        arms = w2.snapshot_arms(x, freqs)
        for arm in ("FK", "FK-RER", "LMWT-RER"):
            label, (f, a) = arms[arm]
            self.assertTrue(w2.detected(f, a, freqs["outer"]), (arm, label))
            self.assertGreater(w2.peak_ratio(f, a, freqs["outer"]), 5, arm)
            self.assertFalse(w2.detected(f, a, freqs["cage"]) and w2.detected(f, a, freqs["ball"]), arm)
        self.assertIn("-", arms["FK"][0])          # a band label, carrier near the 3 kHz resonance
        lo, hi = (float(v) for v in arms["FK"][0].split()[1].split("-"))
        self.assertTrue(lo <= 3000 <= hi + 600, arms["FK"][0])

    def test_window(self):
        f = np.array([100.0, 105.0, 107.0, 110.0, 113.0])
        self.assertEqual(list(w2.in_window(f, 107.91)), [False, False, True, True, False])


if __name__ == "__main__":
    unittest.main()
