"""Synthetic controls for the W1 harness. They check mechanics, not evidence."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import w1  # noqa: E402

RATE = 25600.0


def snapshot(rng, impulses=0.0, n=2560):
    x = rng.normal(size=n)
    if impulses:
        period = int(RATE / 150)  # 150 Hz defect rate
        burst = np.exp(-np.arange(60) / 8) * np.sin(2 * np.pi * 4000 * np.arange(60) / RATE)
        for start in range(rng.integers(0, period), n - 60, period):
            x[start:start + 60] += impulses * burst
    return x


class AlarmRule(unittest.TestCase):
    def test_lead_and_stability(self):
        v = np.r_[np.ones(200), 10 * np.ones(100)]
        r = w1.alarm(v)
        self.assertTrue(r["stable"])
        # The causal median of 5 crosses after 3 high samples: alarm at index 202.
        self.assertEqual(r["alarm"], 202)
        self.assertAlmostEqual(r["lead"], (299 - 202) / (299 - 119))

    def test_unstable_alarm_earns_nothing(self):
        rng = np.random.default_rng(0)
        v = np.r_[rng.normal(size=300), 50 * np.ones(10), rng.normal(size=700)]
        r = w1.alarm(v)
        self.assertIsNotNone(r["alarm"])
        self.assertFalse(r["stable"])
        self.assertEqual(r["lead"], 0.0)

    def test_short_unit_has_no_alarm(self):
        self.assertIsNone(w1.alarm(np.ones(100))["alarm"])

    def test_ledoit_wolf_is_a_valid_shrinkage(self):
        rng = np.random.default_rng(1)
        x = rng.normal(size=(120, 15))
        lw = w1.ledoit_wolf(x)
        np.testing.assert_allclose(lw, lw.T)
        self.assertGreater(np.linalg.eigvalsh(lw).min(), 0)
        s = np.cov(x.T, bias=True)
        self.assertAlmostEqual(np.trace(lw), np.trace(s))


class Indicators(unittest.TestCase):
    def test_impulsive_indicators_rise_with_impulses(self):
        rng = np.random.default_rng(2)
        clean, faulty = snapshot(rng), snapshot(rng, impulses=6.0)
        freqs = np.geomspace(200.0, 10000.0, 32)
        self.assertGreater(w1.b_sk(faulty, RATE), w1.b_sk(clean, RATE) + 1)
        self.assertGreater(max(map(w1.kurt, w1.packet_nodes(faulty))), max(map(w1.kurt, w1.packet_nodes(clean))) + 1)
        sk = lambda x: w1.spectral_kurtosis(w1.morlet_cwt(x, RATE, freqs)).max()  # noqa: E731
        self.assertGreater(sk(faulty), sk(clean) + 1)

    def test_gaussian_noise_has_near_zero_spectral_kurtosis(self):
        # Per-bin SK of Gaussian noise averages ~0; the kurtogram maximum is biased upward
        # by the many bins, which is why thresholds are calibrated per bearing.
        rng = np.random.default_rng(3)
        _, _, z = w1.signal.stft(rng.normal(size=25600), RATE, window="hann", nperseg=64, noverlap=32)
        self.assertLess(abs(w1.spectral_kurtosis(z[1:-1]).mean()), 0.1)

    def test_cwt_centre_frequency(self):
        t = np.arange(2560) / RATE
        x = np.sin(2 * np.pi * 3000 * t)
        freqs = np.geomspace(200.0, 10000.0, 32)
        power = (np.abs(w1.morlet_cwt(x, RATE, freqs)) ** 2).mean(1)
        self.assertLess(abs(freqs[power.argmax()] / 3000 - 1), 0.15)

    def test_degradation_run_alarms_after_onset_with_no_false_alarm(self):
        rng = np.random.default_rng(4)
        amps = np.r_[np.zeros(400), np.linspace(0.5, 8, 200)]
        snaps = [snapshot(rng, a) for a in amps]
        for name, fn in {"B-SK": lambda x: w1.b_sk(x, RATE),
                         "W-WPK": lambda x: max(map(w1.kurt, w1.packet_nodes(x)))}.items():
            r = w1.alarm([fn(x) for x in snaps])
            self.assertIsNotNone(r["alarm"], name)
            self.assertGreaterEqual(r["alarm"], 400, name)
            self.assertTrue(r["stable"], name)


if __name__ == "__main__":
    unittest.main()
