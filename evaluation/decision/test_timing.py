"""Synthetic controls for timing.py (ordering sensitivity, distances, gallery rules). Not evidence."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import timing as t  # noqa: E402
from retrieval import baseline_features  # noqa: E402

BIN = t.HERE.parents[1] / "target/release/examples/features"


def quake(sp_s, rng):
    """Noise, a P burst at 5 s and a larger S burst sp_s later (100 Hz, 4096 samples)."""
    x = rng.normal(0, 0.1, t.WINDOW)
    n = np.arange(300)
    for onset, amp, f in ((5.0, 1.0, 8.0), (5.0 + sp_s, 3.0, 4.0)):
        i = int(onset * t.RATE)
        x[i:i + 300] += amp * np.exp(-n / 80) * np.sin(2 * np.pi * f * n / t.RATE)
    return x


class Controls(unittest.TestCase):
    def test_spectrogram_sees_order_pooled_does_not(self):
        x = quake(15, np.random.default_rng(0))
        r = x[::-1].copy()
        pooled = baseline_features(x, t.RATE, "zscore")["fft_bands"]
        pooled_r = baseline_features(r, t.RATE, "zscore")["fft_bands"]
        self.assertLess(np.abs(pooled - pooled_r).max(), 1e-9)
        self.assertGreater(np.abs(t.spectrogram_bins(x, t.RATE) - t.spectrogram_bins(r, t.RATE)).max(), 0.1)
        self.assertEqual(t.spectrogram_bins(x, t.RATE).shape, (48,))

    def test_xcorr(self):
        rng = np.random.default_rng(1)
        a = t.envelope(quake(15, rng))
        shifted = np.roll(a, 10)  # 1 s at 10 Hz, inside the 3 s lag window
        far = t.envelope(quake(30, rng))
        d = t.xcorr_distances(a[None], np.stack([a, shifted, far]))[0]
        self.assertAlmostEqual(d[0], 0.0, places=6)
        self.assertLess(d[1], 0.2)
        self.assertGreater(d[2], d[1])

    def test_gallery_rules(self):
        meta = {"kind": np.array(["event"] * 4 + ["noise"]), "event": np.array(["a", "b", "c", "a", "n"]),
                "station": np.array(["S1", "S1", "S1", "S2", "S1"]),
                "origin": np.array([0.0, 3600.0, 10 * 86400.0, 0.0, 0.0]),
                "lat": np.array([-20.0, -20.05, -20.0, -20.0, np.nan]), "lon": np.array([-69.0, -69.0, -69.0, -69.0, np.nan]),
                "depth": np.array([100.0, 100.0, 100.0, 100.0, np.nan])}
        gi = np.arange(5)
        m = t.gallery_mask(meta, np.array([0]), gi, True)[0]
        # b: same sequence (1 h, ~5.6 km) excluded; c: 10 days later kept; a@S2 other station; noise never.
        self.assertEqual(m.tolist(), [False, False, True, False, False])
        m_any = t.gallery_mask(meta, np.array([0]), gi, False)[0]
        self.assertEqual(m_any.tolist(), [False, False, True, False, False])  # a@S2 is the same event

    def test_bootstrap_sign(self):
        better = {"e1": 10.0, "e2": 12.0}
        worse = {"e1": 20.0, "e2": 22.0}
        c = t.bootstrap(better, worse)
        self.assertEqual(c["diff_km"], 10.0)
        self.assertEqual(t.verdict(c), "supported")
        self.assertEqual(t.verdict(t.bootstrap(better, better)), "rejected")

    @unittest.skipUnless(BIN.exists(), "build the Rust adapter first")
    def test_temporal_morlet_sees_order(self):
        x = quake(15, np.random.default_rng(2))
        pooled, temporal = t.morlet(np.stack([x, x[::-1].copy()]), BIN)
        self.assertLess(np.abs(pooled[0] - pooled[1]).max(), 1e-6)
        self.assertGreater(np.abs(temporal[0] - temporal[1]).max(), 0.1)
        self.assertEqual(temporal.shape[1], 51)


if __name__ == "__main__":
    unittest.main()
