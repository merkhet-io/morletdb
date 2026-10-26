"""Synthetic controls for the C2 harness. They check mechanics, not evidence."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c2  # noqa: E402


def synthetic(op_effect, anomaly=0.6, seed=0):
    """Operations differ in level by op_effect; bad files raise std by `anomaly` (relative)."""
    rng = np.random.default_rng(seed)
    rows = []
    for period in (*c2.HISTORY_PERIODS, *c2.EVAL_PERIODS):
        for machine in ("M01", "M02"):
            for k in range(6):
                op = f"OP{k:02d}"
                level = 1 + op_effect * k
                for i in range(4):
                    bad = period in c2.EVAL_PERIODS and i == 0 and k % 2 == 0
                    v = {"duration_s": 10.0 * (1 + 0.01 * rng.normal())}
                    for a in "xyz":
                        v[f"std_{a}"] = level * (1 + anomaly * bad) * (1 + 0.05 * rng.normal())
                        v[f"kurtosis_{a}"] = 3 * (1 + 0.05 * rng.normal())
                        v[f"crest_{a}"] = 4 * (1 + 0.05 * rng.normal())
                    rows.append({"file": f"{machine}_{period}_{op}_{i:03d}.h5", "machine": machine, "period": period,
                                 "op": op, "bad": bad, **v})
    return rows


class C2(unittest.TestCase):
    def test_conditioning_helps_when_operations_differ(self):
        res = c2.analyse(synthetic(op_effect=1.0), resamples=200)
        self.assertGreater(res["auroc_machine_operation"], 0.95)
        self.assertGreater(res["delta"], 0.1)
        self.assertEqual(res["decision"], "supported")

    def test_no_gain_when_operations_are_alike(self):
        res = c2.analyse(synthetic(op_effect=0.0), resamples=200)
        self.assertLess(abs(res["delta"]), 0.05)
        self.assertNotEqual(res["decision"], "supported")

    def test_axis_values_match_simple_v1(self):
        x = np.array([1.0, 2.0, 3.0, 4.0])
        std, kurt, crest = c2.axis_values(x)
        self.assertAlmostEqual(std, 1.25 ** 0.5)
        self.assertAlmostEqual(kurt, 1.64)
        self.assertAlmostEqual(crest, 4 / 7.5 ** 0.5)

    def test_short_history_is_excluded(self):
        rows = [r for r in synthetic(1.0) if not (r["machine"] == "M02" and r["op"] == "OP05"
                                                  and r["period"] in c2.HISTORY_PERIODS[1:])]
        rows = [r for r in rows if not (r["machine"] == "M02" and r["op"] == "OP05"
                                        and r["period"] == c2.HISTORY_PERIODS[0] and r["file"].endswith(("2.h5", "3.h5")))]
        res = c2.analyse(rows, resamples=20)
        self.assertTrue(res["excluded"])
        self.assertTrue(all("M02" in e["file"] and "OP05" in e["file"] for e in res["excluded"]))


if __name__ == "__main__":
    unittest.main()
