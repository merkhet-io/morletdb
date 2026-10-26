"""Synthetic controls for bearings.py: leakage, task restrictions, metrics. Not evidence."""
import sys
import zlib
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bearings as b  # noqa: E402

BIN = b.HERE.parents[1] / "target/release/examples/features"
CHOSEN = ["K001", "KA01", "KI01", "K003", "K004", "KA03", "KA05", "KI03", "KI05", "KB23", "KB24"]


def synthetic(bearing, setting, measurement, n=131072):
    """Impulse trains whose rate encodes the damage location; settings change speed."""
    rng = np.random.default_rng(zlib.crc32(f"{bearing}{setting}{measurement}".encode()))
    speed = 0.6 if setting.startswith("N09") else 1.0
    x = rng.normal(0, 0.3, n)
    location = b.BEARINGS[bearing][0]
    rates = {"healthy": [], "outer": [180.0], "inner": [270.0], "combined": [180.0, 270.0]}[location]
    ring = np.exp(-np.arange(200) / 30) * np.sin(2 * np.pi * 4000 * np.arange(200) / b.RATE)
    for r in rates:
        period = int(b.RATE / (r * speed))
        for start in range(rng.integers(0, period), n - 200, period):
            x[start:start + 200] += ring
    return x


class Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        recs = [(k, s, m, synthetic(k, s, m)) for k in CHOSEN for s in b.SETTINGS for m in (1, 2)]
        native, decim, cls.meta = b.build_windows(recs)
        cls.native = native
        if BIN.exists():
            cls.feats, cls.cost = b.compute_features(native, decim, BIN)
        else:
            cls.feats = None

    def test_split(self):
        self.assertEqual(len(b.HELDOUT), 23)
        self.assertFalse(set(b.DEV) & set(b.HELDOUT))
        self.assertNotIn("KA08", b.HELDOUT)
        self.assertEqual(len(b.DEV) + len(b.HELDOUT) + len(b.EXCLUDED), 32)

    def test_windows(self):
        m = self.meta
        self.assertEqual(self.native.shape[1], b.WINDOW)
        self.assertEqual(len(self.native), len(CHOSEN) * 4 * 2 * b.WINDOWS_PER_FILE)
        q = m["query"]
        self.assertTrue((np.isin(m["slot"][q], b.QUERY_WINDOW_SLOTS)).all())
        starts = b.window_starts(131072)
        self.assertTrue((np.diff(starts) >= b.WINDOW).all())

    def test_task_masks(self):
        m = self.meta
        gi = np.where(m["role"] == "heldout")[0]
        qi = gi[m["query"][gi]]
        for task in ("T1", "T2", "T3", "N", "ALL"):
            mask = b.task_mask(task, m, qi, gi)
            same_bearing = m["bearing"][gi][None, :] == m["bearing"][qi][:, None]
            self.assertFalse((mask & same_bearing).any(), task)
        t2 = b.task_mask("T2", m, qi, gi)
        self.assertFalse((t2 & (m["setting"][gi][None, :] == m["setting"][qi][:, None])).any())
        n = b.task_mask("N", m, qi, gi)
        self.assertFalse((n & (m["location"][gi][None, :] == m["location"][qi][:, None])).any())
        self.assertTrue(n[np.isin(m["location"][qi], ["healthy", "combined"])].any())
        self.assertFalse(n[~np.isin(m["location"][qi], ["healthy", "combined"])].any())

    def test_rank_metrics_fixture(self):
        dist = np.array([[0.1, 0.2, 0.3, 0.05]])
        eligible = np.array([[True, True, True, False]])
        relevant = np.array([[False, True, True, True]])
        row = b.rank_metrics(dist, eligible, relevant)[0]
        self.assertEqual(list(row["top"]), [0, 1, 2])
        self.assertAlmostEqual(row["rr"], 0.5)
        self.assertAlmostEqual(row["ap"], (1 / 2 + 2 / 3) / 2)
        self.assertAlmostEqual(row["p10"], 0.2)

    def test_bootstrap_identical_is_zero(self):
        a = {"x": 0.5, "y": 0.7, "z": 0.9}
        r = b.bootstrap_diff(a, a)
        self.assertEqual(r["diff"], 0.0)
        self.assertEqual(r["ci95"], [0.0, 0.0])

    @unittest.skipUnless(BIN.exists(), "build the Rust adapter first")
    def test_end_to_end(self):
        dev = b.evaluate(self.feats, self.meta, "dev")
        held = b.evaluate(self.feats, self.meta, "heldout", dev["thresholds"])
        comp = b.comparisons(held)
        self.assertIn(comp["primary"]["verdict_provisional_margin_0.05"],
                      ("H2 supported", "H2 not supported", "inconclusive"))
        r = held["results"][f"{b.FROZEN}/raw/M"]["tasks"]["T2/location"]
        for match in r["matches"]:
            q_bearing, q_setting = match["query"][:2]
            self.assertTrue(all(t[0] != q_bearing and t[1] != q_setting for t in match["top"]))
        # Spectrally separable controls: every method should clearly beat chance (0.25, four balanced classes).
        for method in b.METHODS:
            p10 = held["results"][f"{b.FROZEN}/raw/{method}"]["tasks"]["T1/location"]["p10"]["mean_over_bearings"]
            self.assertGreater(p10, 0.3, method)
        self.assertEqual(held["query_bearings"], sorted(set(CHOSEN) - set(b.DEV)))


if __name__ == "__main__":
    unittest.main()
