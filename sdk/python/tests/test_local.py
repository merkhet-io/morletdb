"""The embedded edition (#351). Needs the morlet-sqlite library: MORLETDB_SQLITE_LIB or a platform wheel; set
MORLET_SDK_REQUIRE_SQLITE=1 to fail instead of skipping without it."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import morletdb

try:
    morletdb._local._lib()
    MISSING = None
except morletdb.UnsupportedError as e:
    MISSING = str(e)
    if os.environ.get("MORLET_SDK_REQUIRE_SQLITE") == "1":
        raise


@unittest.skipIf(MISSING, f"morlet-sqlite library not available: {MISSING}")
class LocalStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "line3.morlet"

    def tearDown(self):
        self.dir.cleanup()

    def test_ingest_read_stats_and_reopen(self):
        x = np.round(np.sin(np.arange(600_000) / 9.0) * 3000) * (100 / 32768)
        start = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
        with morletdb.open(self.path) as db:
            rec = db.ingest("pump-7/accel-x", x, sample_rate_hz=25_600, channel="accel-x", unit="m/s^2",
                            start_time=start, metadata={"asset": "pump-7"}, sample_quantum=100 / 32768)
            self.assertEqual((rec.samples, rec.channel, rec.unit, rec.metadata["asset"]),
                             (600_000, "accel-x", "m/s^2", "pump-7"))
            self.assertEqual(rec.start_time, start)
            self.assertEqual(db.read("pump-7/accel-x", 40_960, 45_056).samples.tobytes(), x[40_960:45_056].tobytes())
            st = db.stats("pump-7/accel-x", 1000, 300_000)
            part = x[1000:300_000]
            self.assertEqual((st.sample_count, st.min, st.max), (299_000, part.min(), part.max()))
            self.assertAlmostEqual(st.mean, part.mean(), delta=1e-12)
            self.assertEqual(db.append("pump-7/accel-x", 600_000, x[:10]), 600_010)
            self.assertEqual(db.append("pump-7/accel-x", 600_000, x[:10]), 600_010)      # identical replay
            with self.assertRaises(morletdb.ConflictError):
                db.append("pump-7/accel-x", 0, x[:10] + 1)
            with self.assertRaises(morletdb.InvalidInputError):
                db.read("pump-7/accel-x", 0, 700_000)
            with self.assertRaises(morletdb.NotFoundError):
                db.stats("missing", 0, 10)
            with self.assertRaises(morletdb.InvalidInputError):
                db.append("pump-7/accel-x", 600_010, [np.nan])
        with morletdb.open(self.path, durability="normal") as db:
            self.assertEqual([r.recording_id for r in db.recordings()], ["pump-7/accel-x"])
            self.assertEqual(db.read("pump-7/accel-x", 0, 600_000).samples.tobytes(), x.tobytes())

    def test_a_foreign_file_is_refused(self):
        other = Path(self.dir.name) / "other.db"
        c = sqlite3.connect(other)
        c.execute("CREATE TABLE t(x)")
        c.commit()
        c.close()
        with self.assertRaisesRegex(morletdb.InvalidInputError, "not a MorletDB file"):
            morletdb.open(other)


if __name__ == "__main__":
    unittest.main()
