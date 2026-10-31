#!/usr/bin/env python3
"""F2 split, threshold and regime checks (synthetic data only)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import f2  # noqa: E402


def fleet(n=10):
    rng = np.random.default_rng(3)
    return {f"u{i:03d}": 1.0 + 0.002 * np.arange(60 + 5 * i) ** 1.5 / 10 + rng.normal(0, 0.05, 60 + 5 * i)
            for i in range(1, n + 1)}


class Split(unittest.TestCase):
    def test_disjoint_deterministic_halves(self):
        units = {f"u{i:03d}": None for i in range(1, 101)}
        dev, conf = f2.split(units)
        self.assertEqual((len(dev), len(conf)), (50, 50))
        self.assertFalse(set(dev) & set(conf))
        self.assertEqual(f2.split(units), (dev, conf))

    def test_threshold_uses_only_the_given_units(self):
        traj = fleet()
        dev = sorted(traj)[:5]
        before = f2.threshold(traj, dev)
        traj[sorted(traj)[-1]] = traj[sorted(traj)[-1]] * 100     # change a non-development unit
        self.assertEqual(f2.threshold(traj, dev), before)


class Regime(unittest.TestCase):
    def test_slope_is_causal_and_exact(self):
        y = np.concatenate([np.zeros(40), 0.5 * np.arange(40)])
        ts = np.arange(29, 79)
        s = f2.slopes(y, ts)
        # Changing values after t never changes s(t).
        y2 = y.copy()
        y2[60:] = -7
        np.testing.assert_allclose(f2.slopes(y2, np.arange(29, 59)), s[:30])
        # A pure ramp of slope 0.5 over the whole window gives exactly 0.5.
        np.testing.assert_allclose(s[ts >= 59], 0.5)

    def test_gate_never_sees_confirmation_units(self):
        traj = fleet(6)
        dev = sorted(traj)[:4]
        rows = f2.run_gate(traj, dev, tau=0.0, h=10)
        self.assertTrue(set(rows) <= set(dev))


if __name__ == "__main__":
    unittest.main()
