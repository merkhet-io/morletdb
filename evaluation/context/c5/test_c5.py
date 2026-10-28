"""Synthetic checks of the C5 harness: python -I test_c5.py."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c5  # noqa: E402


class Values(unittest.TestCase):
    def test_known_values(self):
        v = c5.simple_values([1.0, 2.0, 3.0, 4.0], 2.0)
        self.assertEqual(v[0], 2.0)
        self.assertEqual(v[1], 2.5)
        self.assertAlmostEqual(v[2], 1.25 ** 0.5, places=15)
        self.assertAlmostEqual(v[3], 7.5 ** 0.5, places=15)
        self.assertEqual(v[4], 3.25)
        self.assertEqual(v[5], 4.0)
        self.assertAlmostEqual(v[7], 1.64, places=12)

    def test_constant_window_matches_product(self):
        # pg_morlet 0.3.1 gives std 3.5388358909926865e-15 for 2000 samples of 0.1 (checked in PR #32).
        v = c5.simple_values(np.full(2000, 0.1), 1000.0)
        self.assertEqual(v[2], 3.5388358909926865e-15)
        self.assertEqual(v[7], 1.0)

    def test_rejects_like_product(self):
        with self.assertRaises(ValueError):
            c5.simple_values([1.0, np.nan], 1.0)

    def test_file_headers_and_times(self):
        row = dict(zip(["Time", "setSpeed"], ["19-Oct-2021 08:46:54", "750"]))
        self.assertEqual(c5.row_time(row), 1634633214.0)  # read as UTC, whatever the machine's zone
        self.assertEqual(c5.row_time({"dateTime": "2021-10-19 08:46:54"}), 1634633214.0)

    def test_context_bands(self):
        self.assertEqual(c5.context_key(1500, 2500, 750, "noise"), (0, 5, 1, "noise"))
        self.assertEqual(c5.context_key(3600, 3999, 1000, "sine"), (4, 7, 2, "sine"))


class Histories(unittest.TestCase):
    def test_median_and_negligible_floor(self):
        h = c5.History()
        for s in (3e-15, 4e-15, 5e-15, 6e-15):  # frozen-channel std residue
            h.add([1.6, 0.1, s, 0.1, 0.1, 0.1, 1.0, 1.0])
        self.assertEqual(h.median(2), 4.5e-15)
        dev = c5.relative_deviations([1.6, 0.1, 0.01, 0.1, 0.1, 0.1, 1.2, 3.0], h, c5.SCORE)
        self.assertIsNone(dev[2])
        self.assertEqual(dev[0], 0.0)
        self.assertAlmostEqual(dev[6], 0.2)

    def test_min_history_and_before_only(self):
        vals = np.ones((8, 8))
        vals[:, 2] = np.arange(1, 9)
        s, _ = c5.history_scores(vals, np.array(["a"] * 8), (2,), True)
        self.assertTrue(np.isnan(s[:5]).all())
        self.assertAlmostEqual(s[5], (6 - 3) / 3)  # median of 1..5 is 3; the segment itself is excluded

    def test_context_separates_histories(self):
        vals = np.ones((12, 8))
        ctx = np.array(["a", "b"] * 6)
        vals[ctx == "b", 2] = 10.0
        s_ctx, _ = c5.history_scores(vals, ctx, (2,), True)
        self.assertTrue(np.nanmax(s_ctx) == 0.0)


class Alarms(unittest.TestCase):
    def test_step_gives_stable_alarm(self):
        rng = np.random.default_rng(1)
        x = 1 + 0.01 * rng.standard_normal(1000)
        x[800:] += 1.0
        t = np.arange(1000) * 12.0
        r = c5.alarm(x, t, t[-1])
        self.assertTrue(r["stable"])
        self.assertTrue(800 <= r["alarm"] <= 806)
        self.assertAlmostEqual(r["lead"], (t[-1] - t[r["alarm"]]) / (t[-1] - t[149]))

    def test_transient_is_unstable(self):
        rng = np.random.default_rng(2)
        x = 1 + 0.01 * rng.standard_normal(1000)
        x[300:320] += 1.0
        r = c5.alarm(x, np.arange(1000) * 12.0, 999 * 12.0)
        self.assertFalse(r["stable"])
        self.assertEqual(r["lead"], 0.0)

    def test_unscored_segments_extend_calibration(self):
        x = np.full(400, np.nan)
        x[140:] = 1.0
        r = c5.alarm(x, np.arange(400) * 12.0, 399 * 12.0)
        self.assertTrue(r["calibration_extended"])
        self.assertEqual(r["calibration"], 30)

    def test_context_beats_context_free_on_varying_conditions(self):
        """Two operating contexts with different levels, drawn at random; degradation from 70%.
        Same-context history alarms stably; all-history thresholds drown in the context spread."""
        rng = np.random.default_rng(3)
        n = 2000
        ctx = rng.choice(["slow", "fast"], n)
        level = np.where(ctx == "fast", 3.0, 1.0)
        std = level * (1 + 0.02 * rng.standard_normal(n))
        std[int(0.7 * n):] *= 1.3
        vals = np.ones((n, 8))
        vals[:, 2] = std
        t = np.arange(n) * 12.0
        s_ctx, _ = c5.history_scores(vals, ctx, (2,), True)
        s_all, _ = c5.history_scores(vals, ctx, (2,), False)
        r_ctx, r_all = c5.alarm(s_ctx, t, t[-1]), c5.alarm(s_all, t, t[-1])
        self.assertTrue(r_ctx["stable"])
        self.assertGreater(r_ctx["lead"], r_all["lead"])


class Decision(unittest.TestCase):
    def test_decision_rule(self):
        rows = {f"B{i}": {"X": {"lead": 0.5, "alarm": 1, "stable": True},
                          "Y": {"lead": 0.1, "alarm": 1, "stable": True}} for i in range(10)}
        d = c5.decide(rows, list(rows), "X", "Y")
        self.assertEqual(d["verdict"], "supported")
        d = c5.decide(rows, list(rows), "Y", "X")
        self.assertEqual(d["verdict"], "not supported")


if __name__ == "__main__":
    unittest.main()
