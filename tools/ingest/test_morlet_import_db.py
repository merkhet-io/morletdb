"""morlet_import.py against a database: time anchors from a time column (#393, #383). Needs
MORLET_MCP_TEST_DATABASE_URL (a database where the role may create tables, with pg_morlet SQL 0.3.13)."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import morlet_import as mi
import test_morlet_import as tmi

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")
BASE = 1_772_352_000_000_000_000           # 2026-03-01T08:00:00Z in ns


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class Anchors(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.c = psycopg.connect(URL, autocommit=True)
        if self.c.execute("SELECT to_regproc('public.morlet_add_time_anchors') IS NULL").fetchone()[0]:
            self.skipTest("server without time anchors (SQL 0.3.13)")
        for (t,) in self.c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                                   "AND tablename LIKE 'imp\\_anchor%' ORDER BY length(tablename) DESC").fetchall():
            self.c.execute(f"DROP TABLE IF EXISTS public.{t} CASCADE")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self.c.close()
        self._tmp.cleanup()

    def run_import(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mi.main([str(path), "--collection", "public.imp_anchor", "--create", "--dsn", URL,
                            "--time-column", "time", "--rate", "1000", "--no-build"])
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_block_stamped_import_has_exact_time_lookup_at_every_block_start(self):
        """#383's done-when: a test dataset with block jumps imports with exact time lookup at every block start."""
        p, starts_ms = tmi.Readers.block_csv(self)
        report = self.run_import(p)
        self.assertEqual(report["time_anchors"], 3)
        rid = "blocks.a"
        for b, s in enumerate(starts_ms):
            got = self.c.execute("SELECT array_agg(sample ORDER BY sample), bool_or(ambiguous) FROM "
                                 "morlet_utc_to_sample('public.imp_anchor',%s,%s)", (rid, BASE + s * 1_000_000)).fetchone()
            self.assertIn(100 * b, got[0], f"block {b}")
        # inside the backward step the time has two samples; both are reported
        got = self.c.execute("SELECT array_agg(sample ORDER BY sample), bool_and(ambiguous) FROM "
                             "morlet_utc_to_sample('public.imp_anchor',%s,%s)", (rid, BASE + 208 * 1_000_000)).fetchone()
        self.assertEqual(got, ([195, 200], True))
        # the start + rate mapping would put block 2's start 8 ms (8 samples) off
        utc = self.c.execute("SELECT utc_ns, basis FROM morlet_sample_utc('public.imp_anchor',%s,200)", (rid,)).fetchone()
        self.assertEqual((int(utc[0]), utc[1]), (BASE + 208_000_000, "anchors"))
        # samples are stored as before, and a rerun writes nothing new
        self.assertEqual(self.c.execute("SELECT count(*) FROM imp_anchor_time_anchors").fetchone()[0], 3)
        self.assertEqual(self.run_import(p)["time_anchors"], 0)
        self.assertEqual(self.c.execute("SELECT count(*) FROM imp_anchor_time_anchors").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
