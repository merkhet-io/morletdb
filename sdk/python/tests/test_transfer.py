"""Files to server and back (#351): needs MORLET_SDK_TEST_DSN and the morlet-sqlite library (see test_local)."""
import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
import uuid
from pathlib import Path

import numpy as np
import psycopg

import morletdb

DSN = os.environ.get("MORLET_SDK_TEST_DSN", "")
try:
    morletdb._local._lib()
    MISSING = None
except morletdb.UnsupportedError as e:
    MISSING = str(e)
    if os.environ.get("MORLET_SDK_REQUIRE_SQLITE") == "1":
        raise


def chunks_of_file(path, rid):
    with closing(sqlite3.connect(path)) as c:
        return [(s, n, bytes(e), bytes(m)) for s, n, e, m in c.execute(
            "SELECT start_sample, sample_count, encoded, summary FROM chunks WHERE recording_id=? ORDER BY start_sample",
            (rid,))]


@unittest.skipUnless(DSN, "set MORLET_SDK_TEST_DSN to run the integration tests")
@unittest.skipIf(MISSING, f"morlet-sqlite library not available: {MISSING}")
class Transfer(unittest.TestCase):
    def setUp(self):
        self.schema = "sdk_xfer_" + uuid.uuid4().hex[:10]
        self.conn = psycopg.connect(DSN)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.conn.commit()
        self.db = morletdb.Client(self.conn)
        self.coll = f"{self.schema}.signals"
        self.db.create_collection(self.coll)
        self.conn.commit()
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()
        self.dir.cleanup()

    def server_chunks(self, rid):
        return [(s, n, bytes(e), bytes(m)) for s, n, e, m in self.conn.execute(
            f"SELECT start_sample, sample_count, encoded, summary FROM {self.coll}_chunks WHERE recording_id=%s "
            "ORDER BY start_sample", (rid,))]

    def test_file_to_server_to_file(self):
        rng = np.random.default_rng(351)
        x = np.round(np.sin(np.arange(1_500_000) / 11.0) * 3000 + rng.normal(0, 3, 1_500_000)) / 512
        x[::997] = rng.normal(0, 1, len(x[::997]))
        src, back = Path(self.dir.name) / "src.morlet", Path(self.dir.name) / "back.morlet"
        with morletdb.open(src) as f:
            f.ingest("pump-7/accel-x", x[:1_000_000], sample_rate_hz=25_600, channel="accel-x", unit="m/s^2",
                     metadata={"asset": "pump-7"}, sample_quantum=1 / 512)
            f.append_chunks("pump-7/accel-x", 1_000_000, x[1_000_000:1_000_100], [1, 99])   # odd layout
        [rec] = self.db.import_file(src, self.coll)
        self.conn.commit()
        self.assertEqual((rec.samples, rec.metadata["asset"]), (1_000_100, "pump-7"))
        self.assertEqual(self.server_chunks("pump-7/accel-x"), chunks_of_file(src, "pump-7/accel-x"))
        # continue on the file, import again: only the rest moves
        with morletdb.open(src) as f:
            f.append("pump-7/accel-x", 1_000_100, x[1_000_100:])
        self.db.import_file(src, self.coll)
        self.conn.commit()
        self.assertEqual(self.server_chunks("pump-7/accel-x"), chunks_of_file(src, "pump-7/accel-x"))
        st_server = self.db.stats(self.coll, "pump-7/accel-x", 12_345, 1_400_000)
        # and back into a new file, chunk for chunk
        [out] = self.db.export_file(self.coll, back)
        self.assertEqual(out.samples, 1_500_000)
        self.assertEqual(chunks_of_file(back, "pump-7/accel-x"), self.server_chunks("pump-7/accel-x"))
        with morletdb.open(back) as f:
            self.assertEqual(f.read("pump-7/accel-x", 0, 1_500_000).samples.tobytes(), x.tobytes())
            st_file = f.stats("pump-7/accel-x", 12_345, 1_400_000)
        self.assertEqual(st_server, st_file)

    def test_a_diverging_server_recording_is_refused(self):
        src = Path(self.dir.name) / "a.morlet"
        with morletdb.open(src) as f:
            f.ingest("r", np.arange(10_000.0), sample_rate_hz=1000, channel="x", unit="")
        self.db.ingest(self.coll, "r", np.arange(4096.0) + 1, sample_rate_hz=1000, channel="x", unit="",
                       search_window=None)
        self.conn.commit()
        with self.assertRaises(morletdb.ConflictError):
            self.db.import_file(src, self.coll)


if __name__ == "__main__":
    unittest.main()
