"""morlet_trial.py against a database: import, bit-exact proof, time check, report. Needs
MORLET_MCP_TEST_DATABASE_URL (a database with pg_morlet SQL 0.3.13 or later where the role may create schemas)."""
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import unittest

import morlet_trial as mt

URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")
T0 = 1767225600000000000


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class Trial(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.psycopg = psycopg
        self.schema = "trial_" + secrets.token_hex(4)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self.drop)

    def drop(self):
        with self.psycopg.connect(URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')

    def export(self):
        p = Path(self.dir.name) / "plant.lp"
        jitter = [0, 9, -4, 12, -11, 3, -7, 5] * 1000
        lines = []
        for n in range(6000):
            if 3000 <= n < 3100:
                continue                                              # a gap
            v = round(math.sin(2 * math.pi * 50 * n / 2000), 4)
            lines.append(f"vib,machine=press\\ 7 a={'-0.0' if n == 0 else v} {T0 + n * 500_000 + 1000 * jitter[n]}")
        lines += [f"env,machine=press\\ 7 temp=2{k}.5,on=true {T0 + k * 1_000_000_000}" for k in range(3)]
        p.write_text("\n".join(lines) + "\n")
        return p

    def run_trial(self, *extra):
        out = Path(self.dir.name) / "report"
        code = mt.main([str(self.export()), "--dsn", URL, "--collection", f"{self.schema}.signals", "--out", str(out),
                        *extra])
        return code, json.loads((out / "report.json").read_text()), (out / "report.md").read_text()

    def test_trial_proves_samples_and_times_and_reports_what_was_left_out(self):
        code, r, md = self.run_trial("--influx-bytes", "123456", "--no-build")
        self.assertEqual(code, 0)
        (rec,) = r["recordings"]
        self.assertEqual((rec["samples"], rec["bit_exact"]), (5900, True))
        self.assertAlmostEqual(rec["rate_hz"], 2000.0, delta=0.05)          # +-12 us jitter: a few ppm
        t = rec["time"]
        self.assertEqual((t["anchors_exact"], t["outside_reported_uncertainty"]), (t["anchors"], 0))
        self.assertGreaterEqual(t["anchors"], 2)                       # the gap starts a new block
        self.assertLessEqual(Decimal(t["max_deviation_ns"]), 30_000)
        self.assertGreater(r["import_seconds"], 0)
        self.assertIsNone(r["envelope_build_seconds"])                # --no-build
        self.assertEqual(sorted(s["series"] for s in r["skipped"]), ["env[machine=press 7].on",
                                                                    "env[machine=press 7].temp"])
        self.assertGreater(r["storage"]["sample_data_bytes"], 0)
        self.assertEqual(r["influx_bytes"], 123456)
        self.assertIn("| Bit-exact read-back | 1 of 1 |", md)
        self.assertIn("InfluxDB, as you measured it", md)
        self.assertEqual([x["file"] for x in r["importer"]], ["plant.lp"])

    def test_an_existing_collection_is_never_touched(self):
        with self.psycopg.connect(URL, autocommit=True) as c:
            c.execute(f'CREATE SCHEMA "{self.schema}"')
            c.execute("SELECT morlet_create_stream_collection(%s, 'signals')", (self.schema,))
        with self.assertRaisesRegex(SystemExit, "exists"):
            self.run_trial()
        with self.psycopg.connect(URL, autocommit=True) as c:
            self.assertEqual(c.execute(f'SELECT count(*) FROM "{self.schema}".signals').fetchone()[0], 0)
            self.assertEqual(c.execute(f'SELECT count(*) FROM "{self.schema}".signals_chunks').fetchone()[0], 0)

    def test_bad_times_are_a_usage_error(self):
        with self.assertRaises(SystemExit) as cm:
            mt.main(["--dsn", URL, "--influx-url", "http://127.0.0.1:9", "--influx-db", "x", "--measurement", "m",
                     "--start", "yesterday", "--end", "2026-01-01T00:00:00Z"])
        self.assertEqual(cm.exception.code, 2)

    def test_collection_names_are_qualified(self):
        self.assertEqual((mt.qualified("signals"), mt.qualified("plant.signals")), ("public.signals", "plant.signals"))


if __name__ == "__main__":
    unittest.main()
