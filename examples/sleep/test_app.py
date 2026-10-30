"""Sleep demo tests; the API test runs only with MORLET_SLEEP_DEMO_DSN (a database prepared by load.py)."""
import os
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402
import load  # noqa: E402


class Helpers(unittest.TestCase):
    def test_min_max_buckets(self):
        lo, hi = app.min_max_buckets(np.array([0, 5, -1, 2, 3, 9, -4, 1], float), 4)
        self.assertEqual(lo, [0, -1, 3, -4])
        self.assertEqual(hi, [5, 2, 9, 1])

    def test_clock(self):
        self.assertEqual(app.clock("23:59:30", 1), "00:00:00")
        self.assertEqual(app.clock("22:15:00", 120), "23:15:00")
        self.assertEqual(load.clock(("01.01.89", "22.15.00"), 2), "22:16:00")

    def test_page_is_self_contained(self):
        page = (Path(app.__file__).parent / "index.html").read_text()
        self.assertNotIn("https://", page)
        self.assertNotIn("http://", page.replace("http://www.w3.org/2000/svg", ""))
        self.assertIn("Not a medical device", page)

    def test_unknown_endpoint(self):
        with self.assertRaises(LookupError):
            app.api(None, "/api/nope", {})


@unittest.skipUnless(os.environ.get("MORLET_SLEEP_DEMO_DSN"), "needs MORLET_SLEEP_DEMO_DSN with loaded data")
class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = app.Demo(os.environ["MORLET_SLEEP_DEMO_DSN"])

    def test_round_trip(self):
        info = app.api(self.demo, "/api/nights", {})
        rid = info["nights"][0]["recording_id"]
        night = app.api(self.demo, "/api/night", {"recording": [rid]})
        self.assertEqual(len(night["epochs"]), len(night["expert"]))
        ep = app.api(self.demo, "/api/epoch", {"recording": [rid], "epoch": [str(night["epochs"][100])]})
        self.assertEqual(len(ep["samples"]), 3000)
        self.assertEqual(len(ep["neighbours"]), app.K)
        self.assertEqual(sum(ep["votes"]), app.K)
        self.assertEqual(ep["retrieved"], night["retrieved"][100])
        subjects = {n["subject"] for n in ep["neighbours"]}
        self.assertNotIn(ep["subject"], subjects)          # other people only
        d = [n["distance"] for n in ep["neighbours"]]
        self.assertEqual(d, sorted(d))
        nb = ep["neighbours"][0]
        iv = app.api(self.demo, "/api/interval", {"recording": [nb["recording_id"]], "epoch": [str(nb["epoch"])]})
        self.assertEqual(iv["stage"], nb["stage"])

    def test_gallery_nights_are_not_queries(self):
        dev = next(r for r, m in self.demo.recordings.items() if m["split"] == "development")
        with self.assertRaises(LookupError):
            app.api(self.demo, "/api/night", {"recording": [dev]})


if __name__ == "__main__":
    unittest.main()
