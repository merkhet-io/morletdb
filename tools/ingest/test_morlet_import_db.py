"""morlet_import.py against a database: time anchors from a time column (#393, #383). Needs
MORLET_MCP_TEST_DATABASE_URL (a database where the role may create tables, with pg_morlet SQL 0.3.13)."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

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


    def test_reported_uncertainty_covers_every_imported_timestamp(self):
        """#561 through the import command: a jittered 1 kHz time column (and the report's three rows). For every
        row, the server's mapped time is within the uncertainty it reports. Before, the report's row 1 was 400,000 ns
        off with a reported 101 ns."""
        import random
        rnd = random.Random(5610)
        for name, rel in (("jitter", [0, 1_400_000, 2_000_000]),
                          ("jitter2", [k * 1_000_000 + rnd.randint(-450_000, 450_000) for k in range(1, 1501)])):
            p = self.tmp / f"{name}.csv"
            rows = sorted(rel)
            p.write_text("time,x\n" + "".join(f"2026-03-01T08:00:{(BASE % 10**9 + r) // 10**9:02d}."
                                               f"{r % 10**9:09d}Z,{i}\n" for i, r in enumerate(rows)))
            self.run_import(p)
            got = self.c.execute("SELECT s.n, u.utc_ns, u.uncertainty_ns, u.assumes_linear_clock FROM "
                                 "generate_series(0,%s) s(n), LATERAL morlet_sample_utc('public.imp_anchor',%s,s.n) u "
                                 "ORDER BY s.n", (len(rows) - 1, f"{name}.x")).fetchall()
            self.assertEqual(len(got), len(rows))
            for (n, utc, unc, linear), r in zip(got, rows):
                self.assertFalse(linear)
                self.assertLessEqual(abs(utc - (BASE + r)), unc, f"{name}: row {n}")
            if name == "jitter":
                self.assertGreaterEqual(got[1][2], 400_000)

    def test_a_rerun_widens_anchors_an_earlier_importer_wrote(self):
        """#561: anchors imported before the fix declared only the resolution. A rerun of the same file revises them
        to the new bound instead of failing on the changed batch; a second rerun writes nothing; anchors that differ
        otherwise are refused with a message."""
        p = self.tmp / "jitter.csv"
        p.write_text("time,x\n2026-03-01T08:00:00.000000000Z,0\n2026-03-01T08:00:00.001400000Z,1\n"
                     "2026-03-01T08:00:00.002000000Z,2\n")
        # the earlier importer: the resolution alone
        with mock.patch.object(mi, "anchor_uncertainties", lambda t, rate, anchors, res: [res] * len(anchors)):
            self.assertEqual(self.run_import(p)["time_anchors"], 1)
        self.assertEqual(self.c.execute("SELECT uncertainty_ns FROM imp_anchor_time_anchors").fetchone()[0], 1)
        self.assertEqual(self.run_import(p)["time_anchors"], 1)         # one anchor revised
        unc = self.c.execute("SELECT uncertainty_ns FROM morlet_sample_utc('public.imp_anchor','jitter.x',1)").fetchone()
        self.assertGreaterEqual(unc[0], 400_000)
        self.assertEqual(self.c.execute("SELECT count(*) FROM imp_anchor_time_anchors").fetchone()[0], 2)  # kept
        self.assertEqual(self.run_import(p)["time_anchors"], 0)
        p.write_text(p.read_text().replace("08:00:00.000000000Z,0", "08:00:00.000000005Z,0"))
        out = io.StringIO()
        with contextlib.redirect_stderr(out), contextlib.redirect_stdout(io.StringIO()):
            code = mi.main([str(p), "--collection", "public.imp_anchor", "--dsn", URL, "--time-column", "time",
                            "--rate", "1000", "--no-build"])
        self.assertNotEqual(code, 0)
        self.assertIn("written with other anchors", out.getvalue())

    def test_events_on_an_anchored_recording_land_by_the_anchors(self):
        """#393: morlet_events.py places a message by the anchors: block 1's start time is sample 100, not 113."""
        import morlet_events as me
        p, _ = tmi.Readers.block_csv(self)
        self.run_import(p)
        log = self.tmp / "alarms.csv"
        log.write_text("time,code\n2026-03-01T08:00:00.113Z,7\n2026-03-01T08:00:00.2085Z,8\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(me.main([str(log), "--collection", "public.imp_anchor", "--dsn", URL, "--time-column",
                                      "time", "--key", "alarm", "--value-columns", "code"]), 0)
        rows = self.c.execute("SELECT start_sample, value FROM imp_anchor_ctx WHERE key='alarm' ORDER BY start_sample").fetchall()
        self.assertEqual([r[0] for r in rows], [100, 195])
        self.assertEqual(rows[0][1]["time_basis"], "anchors")
        self.assertEqual(rows[1][1]["time_candidate_samples"], [195, 200])

if __name__ == "__main__":
    unittest.main()
