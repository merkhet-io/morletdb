#!/usr/bin/env python3
"""CV1 alignment, pairing, shuffle and split checks (synthetic data only)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as F  # noqa: E402
import gate as G  # noqa: E402


class Alignment(unittest.TestCase):
    def test_windows_are_complete_and_shared(self):
        self.assertEqual(F.windows(256001), [i * 32000 for i in range(8)])
        self.assertEqual(F.windows(255999), [i * 32000 for i in range(7)])   # 8th incomplete: dropped

    def test_mechanical_span_matches_the_window_time(self):
        t_host = np.arange(256001) / 64000.0
        t_mech = np.arange(16001) / 4000.0 - 0.000203
        m = F.mech_span(t_mech, t_host, 32000, 64000)                        # 0.5 s to 1.0 s
        self.assertTrue(abs(m.sum() - 2000) <= 1)
        self.assertGreaterEqual(t_mech[m].min(), 0.5)
        self.assertLess(t_mech[m].max(), 1.0)

    def test_band_energy_finds_a_tone_in_its_band(self):
        t = np.arange(32000) / 64000.0
        e = F.band_log_energy(np.sin(2 * np.pi * 3000 * t), F.VIB_BANDS)
        self.assertEqual(int(np.argmax(e)), 1)                                # 1-5 kHz


def toy(n_bear=6, rec=4, win=8):
    rng = np.random.default_rng(0)
    rows = [(f"b{b}", ["s1", "s2"][r % 2], r, w) for b in range(n_bear) for r in range(rec) for w in range(win)]
    z = {"bearing": np.array([r[0] for r in rows]), "setting": np.array([r[1] for r in rows]),
         "measurement": np.array([r[2] for r in rows]), "window": np.array([r[3] for r in rows])}
    n = len(rows)
    z["B"] = np.c_[(z["setting"] == "s1").astype(float), rng.normal(size=(n, 2))]
    z["C"] = rng.normal(size=(n, 3))
    z["Y"] = np.c_[z["C"][:, 0] + 0.1 * rng.normal(size=n), z["B"][:, 0] + 0.1 * rng.normal(size=n)]
    return z


class Shuffle(unittest.TestCase):
    def test_whole_recordings_swap_within_setting_and_training_only(self):
        z = toy()
        train = z["bearing"] != "b0"
        C = G.shuffle_current(z, train, np.random.default_rng(1))
        np.testing.assert_array_equal(C[~train], z["C"][~train])              # held-out untouched
        rec = np.array([f"{b}|{s}|{m}" for b, s, m in zip(z["bearing"], z["setting"], z["measurement"])])
        for r in np.unique(rec[train]):
            rows = np.where(rec == r)[0]
            # every recording's new block is some training recording's block of the same setting
            src = [np.where((z["C"] == C[rows[0]]).all(1))[0][0]]
            self.assertTrue(train[src[0]] and z["setting"][src[0]] == z["setting"][rows[0]])
            np.testing.assert_array_equal(C[rows], z["C"][np.where(rec == rec[src[0]])[0]])

    def test_current_signal_is_found_and_shuffling_destroys_it(self):
        z = toy(n_bear=8)
        test, train = z["bearing"] == "b0", z["bearing"] != "b0"
        Y = z["Y"]
        e = {}
        for m in "BD":
            X = G.design(z, m)
            e[m] = G.unit_error(Y[test], G.fit(X[train], Y[train], 1.0)(X[test]))
        Xs = G.design(z, "D", G.shuffle_current(z, train, np.random.default_rng(2)))
        e["sh"] = G.unit_error(Y[test], G.fit(Xs[train], Y[train], 1.0)(G.design(z, "D")[test]))
        self.assertLess(e["D"], 0.6 * e["B"])
        self.assertGreater(e["sh"], 0.9 * e["B"])


class Decomposition(unittest.TestCase):
    def test_constant_prediction_has_no_within_skill(self):
        Y = np.array([[1.0], [2.0], [5.0], [6.0]])
        P = np.array([[1.5], [1.5], [5.5], [5.5]])
        d = G.decompose(Y, P, np.array(["r1", "r1", "r2", "r2"]))
        self.assertAlmostEqual(d["between_rmse"], 0.0)
        self.assertAlmostEqual(d["within_rmse"], 0.5)


if __name__ == "__main__":
    unittest.main()
