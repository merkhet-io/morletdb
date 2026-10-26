"""Viewer helper tests; the API test runs only with MORLET_VIEWER_TEST_DSN (a database where
tools/viewer/demo.py has been run)."""
import os
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import viewer  # noqa: E402


class Helpers(unittest.TestCase):
    def test_min_max_buckets(self):
        x = np.array([0, 5, -1, 2, 3, 9, -4, 1], float)
        offsets, lo, hi = viewer.min_max_buckets(x, 4)
        self.assertEqual(offsets, [0, 2, 4, 6])
        self.assertEqual(lo, [0, -1, 3, -4])
        self.assertEqual(hi, [5, 2, 9, 1])
        self.assertEqual(len(viewer.min_max_buckets(x, 100)[0]), 8)

    def test_snap_window(self):
        self.assertIsNone(viewer.snap_window(0, 400, 10_000))
        self.assertEqual(viewer.snap_window(1000, 6000, 100_000), (1452, 5548))
        self.assertEqual(viewer.snap_window(99_000, 99_900, 100_000), (99_194, 99_706))
        self.assertEqual(viewer.snap_window(0, 10**6, 10**7), (483_616, 516_384))

    def test_page_is_self_contained(self):
        page = (Path(viewer.__file__).parent / "index.html").read_text()
        self.assertNotIn("http://", page.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", page)


@unittest.skipUnless(os.environ.get("MORLET_VIEWER_TEST_DSN"), "needs MORLET_VIEWER_TEST_DSN with demo data")
class Api(unittest.TestCase):
    def test_demo_round_trip(self):
        db = viewer.Database(os.environ["MORLET_VIEWER_TEST_DSN"])
        c = "public.demo_spindle"
        self.assertIn(c, [x["name"] for x in viewer.api(db, "/api/collections", {})["collections"]])
        ov = viewer.api(db, "/api/overview", {"collection": [c], "recording": ["part-006-vib"], "start": ["0"], "end": ["90000"],
                                              "buckets": ["500"]})
        self.assertEqual(len(ov["min"]), 500)
        ctx = viewer.api(db, "/api/context", {"collection": [c], "recording": ["part-006-vib"], "start": ["0"], "end": ["90000"]})
        n30 = next(s for s in ctx["segments"] if s["context"]["block"] == "N30")
        cmp = viewer.api(db, "/api/compare", {"collection": [c], "segment": [str(n30["segment_id"])]})
        self.assertEqual(cmp["summary"]["top_value"], "std")
        self.assertGreater(cmp["summary"]["score"], 0.5)
        # Both channels of the acquisition; with the level, the current channel shows the air cut too.
        both = viewer.api(db, "/api/compare", {"collection": [c], "segment": [str(n30["segment_id"])],
                                               "levels": ["1"], "before_only": ["1"]})
        roles = {ch["role"]: ch for ch in both["channels"]}
        self.assertEqual(set(roles), {"vib", "current"})
        self.assertGreater(roles["current"]["score"], 0.3)
        acq = viewer.api(db, "/api/acquisition", {"collection": [c], "recording": ["part-006-vib"]})
        self.assertEqual(acq["acquisition"]["acquisition_id"], "part-006")
        self.assertEqual(len(acq["channels"]), 2)
        sim = viewer.api(db, "/api/similar", {"collection": [c], "recording": ["part-007-vib"], "start": ["30000"], "end": ["36000"]})
        self.assertEqual(sim["window"][1] - sim["window"][0], 4096)
        with self.assertRaises(LookupError):
            viewer.api(db, "/api/recordings", {"collection": ["public.demo_spindle; DROP TABLE x"]})

    def test_sessions_are_read_only(self):
        import psycopg
        db = viewer.Database(os.environ["MORLET_VIEWER_TEST_DSN"])
        for first in (None, "SELECT 1"):  # also when the write is not the first statement
            with db.connect() as conn, self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                if first:
                    conn.execute(first)
                conn.execute("CREATE TABLE viewer_should_not_write (x int)")
        with db.connect() as conn:
            self.assertEqual(conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"], "20s")


@unittest.skipUnless(os.environ.get("MORLET_VIEWER_TEST_DSN"), "needs MORLET_VIEWER_TEST_DSN with demo data")
class LabelWrites(unittest.TestCase):
    """The write path through a real server: token, JSON, Origin and Host checks."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), viewer.Handler)
        viewer.Handler.db = viewer.Database(os.environ["MORLET_VIEWER_TEST_DSN"])
        viewer.Handler.port = cls.server.server_address[1]
        viewer.Handler.token = "test-token"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{viewer.Handler.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        viewer.Handler.token = None

    def post(self, path, body, headers):
        import json
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_guards_and_round_trip(self):
        ok = {"Content-Type": "application/json", "X-Morlet-Token": "test-token"}
        body = {"collection": "public.demo_spindle", "recording": "part-003-vib", "start": 100, "end": 200,
                "label": "unit_test", "note": "x"}
        self.assertEqual(self.post("/api/label", body, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.post("/api/label", body, {**ok, "X-Morlet-Token": "wrong"})[0], 403)
        self.assertEqual(self.post("/api/label", body, {**ok, "Origin": "https://example.org"})[0], 403)
        self.assertEqual(self.post("/api/label", body, {**ok, "Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.post("/api/label", body, {**ok, "Host": "attacker.example"})[0], 403)
        self.assertEqual(self.post("/api/label", {**body, "label": ""}, ok)[0], 400)
        code, out = self.post("/api/label", body, {**ok, "Origin": self.base})
        self.assertEqual(code, 200)
        code, out = self.post("/api/retract_label", {"collection": "public.demo_spindle", "label_id": out["label_id"]}, ok)
        self.assertEqual(code, 200)

    def test_page_carries_the_token_and_reads_check_host(self):
        import urllib.error
        import urllib.request
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn('content="test-token"', r.read().decode())
        req = urllib.request.Request(self.base + "/api/collections", headers={"Host": "rebind.example:80"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 421)


if __name__ == "__main__":
    unittest.main()
