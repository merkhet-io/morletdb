"""Synthetic checks of the C4 harness (no 3W data)."""
import sys
import unittest
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import c4  # noqa: E402

NC, NV = len(c4.CHANNELS), len(c4.VALUES)


def win(well, inst, start, label, ctx, level, channels=range(5)):
    vals = np.full((NC, NV), np.nan)
    for c in channels:
        vals[c] = [level, 1.0 + level / 10, 3.0, 10 * level]
    return {"well": well, "instance": inst, "start": start, "label": label, "context": ctx, "vals": vals}


class Values(unittest.TestCase):
    def test_simple_v1_definitions(self):
        x = np.array([1.0, 2.0, 3.0, 4.0])
        std, crest, kurt, p75 = c4.channel_values(x)
        self.assertAlmostEqual(std, np.std(x))
        self.assertAlmostEqual(crest, 4 / np.sqrt(np.mean(x ** 2)))
        self.assertAlmostEqual(kurt, np.mean((x - 2.5) ** 4) / np.var(x) ** 2)
        self.assertAlmostEqual(p75, 3.25)

    def test_frozen_and_rejected(self):
        self.assertEqual(c4.channel_values(np.full(10, 5.0)), [0.0, 1.0, 0.0, 5.0])
        self.assertIsNone(c4.channel_values(np.array([1.0, np.nan])))
        self.assertIsNone(c4.channel_values(np.array([1.0, 2e9])))


class Windows(unittest.TestCase):
    def test_labels_and_contexts(self):
        n = c4.WINDOW
        cls = np.r_[np.full(n, 0.0), np.full(int(0.85 * n), 106.0), np.full(n - int(0.85 * n), 6.0),
                    np.full(n // 2, 0.0), np.full(n - n // 2, 4.0), np.full(n, np.nan), np.zeros(10)]
        state = np.r_[np.zeros(n), np.r_[np.zeros(n // 2), np.ones(n - n // 2)], np.zeros(n),
                      np.full(n, np.nan), np.zeros(10)]
        cols = {"P-PDG": np.arange(len(cls), dtype=float), "T-TPT": np.r_[np.full(n, np.nan), np.ones(len(cls) - n)]}
        ws = c4.instance_windows("WELL-00001", "0/x", np.arange(4) * n, cls, state, cols)
        self.assertEqual(len(ws), 4)  # trailing partial window dropped
        self.assertEqual([w["label"] for w in ws], [0, 6, None, None])  # 106 -> 6; 50/50 and NaN unlabelled
        self.assertEqual([w["context"] for w in ws], ["0", "mixed", "0", "unknown"])
        p, t = c4.CHANNELS.index("P-PDG"), c4.CHANNELS.index("T-TPT")
        self.assertTrue(np.isfinite(ws[0]["vals"][p]).all())
        self.assertTrue(np.isnan(ws[0]["vals"][t]).all())  # a missing observation makes the channel absent
        self.assertTrue(np.isfinite(ws[1]["vals"][t]).all())


class Prediction(unittest.TestCase):
    def setUp(self):
        # Well A: label 0 at level 1, earlier. Well B: label 4 at level 5. Query: well A, level ~5.
        self.ws = [win("A", f"a{i}", i * 10, 0, "0", 1.0) for i in range(4)]
        self.ws += [win("B", f"b{i}", 100 + i * 10, 4, "0", 5.0) for i in range(4)]
        self.ws += [win("C", "c0", 50, 4, "1", 5.0)]               # other context
        self.ws += [win("A", "a9", 200, 4, "0", 5.1),
                    win("A", "a9", 3800, 4, "0", 5.1)]              # second window of the same instance
        self.ws += [win("B", "late", 999, 0, "0", 1.0)]             # starts after the query

    def test_before_only_same_instance_and_votes(self):
        rows, excluded, _ = c4.predict(self.ws)
        q = next(r for r in rows if r["instance"] == "a9" and r["start"] == 200)
        self.assertEqual(q["N"], 4)            # nearest are well B's level-5 windows
        self.assertEqual(q["B"], 0)            # well A's last label
        self.assertEqual(q["prior"], 4)        # any context: 5 x label 4 vs 4 x label 0
        ids = {n["window"] for n in q["neighbours"]}
        self.assertNotIn(10, ids)              # own instance never a reference
        self.assertNotIn(11, ids)              # later window never a reference
        self.assertNotIn(8, ids)               # other context
        q2 = next(r for r in rows if r["instance"] == "a9" and r["start"] == 3800)
        self.assertNotIn(9, {n["window"] for n in q2["neighbours"]})
        self.assertGreater(excluded["fewer than 5 comparable references"], 0)  # early windows

    def test_distance_matches_definition(self):
        rows, _, _ = c4.predict(self.ws)
        q = next(r for r in rows if r["instance"] == "a9" and r["start"] == 200)
        same = [i for i, w in enumerate(self.ws) if w["context"] == "0" and w["start"] < 200 and w["instance"] != "a9"]
        V = np.stack([self.ws[i]["vals"] for i in same])
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(V, axis=0)
        x = self.ws[9]["vals"]
        n0 = q["neighbours"][0]
        d = np.sqrt(np.nanmean(((x - self.ws[n0["window"]]["vals"]) / np.abs(med)) ** 2))
        self.assertAlmostEqual(n0["distance"], d)

    def test_shared_channel_minimum(self):
        ws = [win("A", f"a{i}", i, 0, "0", 1.0, channels=[0, 1]) for i in range(6)]
        ws.append(win("B", "q", 100, 0, "0", 1.0, channels=[0, 1, 2]))
        _, excluded, _ = c4.predict(ws)
        self.assertEqual(excluded["fewer than 5 comparable references"], 7)

    def test_vote_tie_breaks_on_summed_distance(self):
        self.assertEqual(c4.vote([1, 1, 2, 2, 3], [0.1, 0.5, 0.2, 0.3, 0.0]), 2)


class Metrics(unittest.TestCase):
    def rows(self):
        out = []
        for well, inst, label, ok_n, ok_b in [("A", "i1", 0, 1, 1), ("A", "i2", 0, 1, 0), ("B", "i3", 0, 0, 0),
                                              ("B", "i4", 4, 1, 0), ("C", "i5", 4, 1, 1), ("C", "i6", 4, 0, 0),
                                              ("D", "i7", 9, 1, 1)]:
            out.append({"well": well, "instance": inst, "label": label, "context": "0",
                        "N": label if ok_n else -5, "B": label if ok_b else -5, "prior": 0,
                        "N_own_well": None, "N_context_free": label, "neighbours": []})
        return out

    def test_eligibility_and_balanced_accuracy(self):
        rows = self.rows()
        self.assertEqual(c4.eligible_classes(rows), [0, 4])  # class 9: one instance
        res = c4.analyse(rows, resamples=200)
        self.assertAlmostEqual(res["ba_N"], np.mean([2 / 3, 2 / 3]))
        self.assertAlmostEqual(res["ba_B"], np.mean([1 / 3, 1 / 3]))
        self.assertAlmostEqual(res["delta"], 1 / 3)
        self.assertEqual(res["reported"]["N_own_well"]["fallback_to_prior"], 6)

    def test_well_weights(self):
        g = c4.groups(self.rows(), "N")
        self.assertAlmostEqual(c4.balanced_accuracy(g, [0], ["A", "A", "B"]), (1 + 1 + 1 + 1 + 0) / 5)
        self.assertIsNone(c4.balanced_accuracy(g, [4], ["A"]))


if __name__ == "__main__":
    unittest.main()
