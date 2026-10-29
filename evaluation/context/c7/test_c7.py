"""Synthetic checks of the C7 harness: python -I test_c7.py."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c7  # noqa: E402
c5 = c7.c5


def values_from_std(std, rng):
    """simple-v1 rows whose std (the score's driver) follows `std`; crest and kurtosis fixed."""
    v = np.ones((len(std), len(c5.NAMES)))
    v[:, 2] = std
    v[:, c5.RMS] = std
    v[:, 6] = 4.0 + 0.01 * rng.standard_normal(len(std))
    v[:, 7] = 3.0 + 0.01 * rng.standard_normal(len(std))
    return v


class Pin(unittest.TestCase):
    def test_pinned_are_leave_one_out(self):
        v = np.ones((40, len(c5.NAMES)))
        v[:, 2] = np.arange(1, 41)
        s, top = c7.pinned_scores(v, (2,), pin=6)
        # Snapshot 0 against 2..6 (median 4); snapshot 10 (value 11) against 1..6 (median 3.5).
        self.assertAlmostEqual(s[0], 3 / 4)
        self.assertAlmostEqual(s[10], 7.5 / 3.5)
        self.assertEqual(top[10], 2)

    def test_pin_ignores_later_snapshots(self):
        v = np.ones((50, len(c5.NAMES)))
        v[30:, 2] = 2.0
        s, _ = c7.pinned_scores(v, (2,))
        self.assertTrue(np.all(s[:30] == 0.0))
        self.assertTrue(np.all(s[30:] == 1.0))  # never absorbed

    def test_too_small_pin_is_unscored(self):
        v = np.ones((10, len(c5.NAMES)))
        s, _ = c7.pinned_scores(v, (2,), pin=5)
        self.assertTrue(np.isnan(s[:5]).all())  # 4 others < MIN_HISTORY
        self.assertTrue(np.isnan(s[5:]).all() == False)  # noqa: E712  (5 pinned members score later ones)


class Absorption(unittest.TestCase):
    """C5's B09 in miniature: a sustained rise early in life."""

    def setUp(self):
        rng = np.random.default_rng(11)
        n = 600
        std = 1 + 0.02 * rng.standard_normal(n)
        std[150:] *= 1.5
        self.v, self.t = values_from_std(std, rng), np.arange(n) * 60.0
        self.rows = c7.unit_rows(self.v, self.t)

    def test_all_history_absorbs_and_pin_does_not(self):
        pin, allh = self.rows["H-PIN"], self.rows["H-ALL"]
        self.assertTrue(pin["stable"])
        self.assertTrue(150 <= pin["alarm"] <= 160)
        self.assertGreater(pin["lead"], 0.7)
        self.assertFalse(allh["stable"])
        self.assertEqual(allh["lead"], 0.0)

    def test_gated_refresh_keeps_the_rise_out(self):
        self.assertTrue(self.rows["H-GATED"]["stable"])
        self.assertEqual(self.rows["H-GATED"]["alarm"], self.rows["H-PIN"]["alarm"])

    def test_absorption_share(self):
        ab = self.rows["absorption"]
        self.assertGreater(ab["H-PIN"], 0.95)
        self.assertLess(ab["H-ALL"], 0.5)

    def test_decisions_on_a_sample(self):
        per = {f"b{i}": self.rows for i in range(15)}
        r = c7.channel_result(per, list(per))
        self.assertEqual(r["Q1"]["verdict"], "supported")
        self.assertEqual(r["absorption"]["H-PIN"]["bearings"], 15)


class Gated(unittest.TestCase):
    def test_slow_drift_is_admitted(self):
        """A drift too slow to cross the threshold enters the gated history, so the gated median follows it."""
        rng = np.random.default_rng(5)
        n = 400
        std = 1 + 0.02 * rng.standard_normal(n) + np.linspace(0, 0.3, n)
        v = values_from_std(std, rng)
        g, _ = c7.gated_scores(v, c5.SCORE)
        p, _ = c7.pinned_scores(v, c5.SCORE)
        self.assertLess(np.nanmean(g[-50:]), np.nanmean(p[-50:]))

    def test_inside_the_pin_gated_equals_pinned(self):
        rng = np.random.default_rng(6)
        v = values_from_std(1 + 0.02 * rng.standard_normal(100), rng)
        g, _ = c7.gated_scores(v, c5.SCORE)
        p, _ = c7.pinned_scores(v, c5.SCORE)
        np.testing.assert_array_equal(g[:c7.PIN], p[:c7.PIN])


class ContextPin(unittest.TestCase):
    def test_same_context_members_only(self):
        v = np.ones((200, len(c5.NAMES)))
        ctx = np.array(["a", "b"] * 100)
        v[ctx == "b", 2] = 10.0
        v[160:, 2] *= 2  # both contexts rise late
        s, _ = c7.pinned_context_scores(v, ctx, (2,), pin=20)
        self.assertTrue(np.all(s[:160] == 0.0))
        self.assertTrue(np.all(s[160:] == 1.0))

    def test_context_with_few_pinned_members_is_unscored(self):
        v = np.ones((50, len(c5.NAMES)))
        ctx = np.array(["a"] * 47 + ["b"] * 3)
        s, _ = c7.pinned_context_scores(v, ctx, (2,), pin=50)
        self.assertTrue(np.isnan(s[47:]).all())


class Files(unittest.TestCase):
    def write(self, d, name, header=True):
        rng = np.random.default_rng(int(name.split(".")[0]))
        x = rng.standard_normal((c7.SAMPLES, 2))
        with open(d / name, "w", encoding="ascii") as f:
            if header:
                f.write("Horizontal_vibration_signals,Vertical_vibration_signals\n")
            np.savetxt(f, x, delimiter=",", fmt="%.6f")
        return x

    def test_layout_header_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "35Hz12kN" / "Bearing1_1"
            d.mkdir(parents=True)
            xs = [self.write(d, f"{i}.csv", header=i != 2) for i in (1, 2, 10)]
            with self.assertRaises(SystemExit):  # 1, 2, 10: a gap
                c7.snapshot_files(d)
            (d / "10.csv").rename(d / "3.csv")
            out = c7.bearing_values(Path(tmp), "Bearing1_1")
            self.assertEqual(out["values"].shape, (2, 3, len(c5.NAMES)))
            np.testing.assert_array_equal(out["t"], [0, 60, 120])
            h, _ = c7.load_snapshot(d / "3.csv")
            self.assertAlmostEqual(out["values"][0, 2, c5.RMS], float(np.sqrt(np.mean(h ** 2))), places=12)
            self.assertAlmostEqual(h[0], round(xs[2][0, 0], 6))

    def test_wrong_length_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "1.csv"
            np.savetxt(p, np.zeros((100, 2)), delimiter=",")
            with self.assertRaises(SystemExit):
                c7.load_snapshot(p)


if __name__ == "__main__":
    unittest.main()
