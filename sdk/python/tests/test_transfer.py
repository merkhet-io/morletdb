"""Files to server and back (#351, #356): needs MORLET_SDK_TEST_DSN and the morlet-sqlite library (see test_local)."""
import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from unittest import mock
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


def _crc32c_table():
    out = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        out.append(c)
    return out


CRC32C = _crc32c_table()


def raw_chunk(x):
    """x as a chunk of kind raw (format version 2, chunk-format.md): the same samples, other bytes than the
    encoder writes for compressible data. Stands in for chunks written by another encoder version."""
    x = np.ascontiguousarray(x, dtype="<f8")
    n, varint = len(x), bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        varint.append(b | (0x80 if n else 0))
        if not n:
            break
    body = bytes([2, 0]) + bytes(varint) + x.tobytes()
    c = 0xFFFFFFFF
    for byte in body:
        c = CRC32C[(c ^ byte) & 0xFF] ^ (c >> 8)
    return body + (c ^ 0xFFFFFFFF).to_bytes(4, "little")


def reencode_file(path, rid, writer="morlet-sqlite 0.0.1", change=None):
    """Rewrite every chunk of a file recording as raw chunks and record another writer; `change` maps a sample
    index to a new value (a deliberately altered sample)."""
    with morletdb.open(path) as f:
        rec = f.recording(rid)
        x = f.read(rid, 0, rec.samples).samples.copy()
    for i, v in (change or {}).items():
        x[i] = v
    with closing(sqlite3.connect(path)) as c:
        for s, n in c.execute("SELECT start_sample, sample_count FROM chunks WHERE recording_id=?", (rid,)).fetchall():
            c.execute("UPDATE chunks SET encoded=? WHERE recording_id=? AND start_sample=?",
                      (raw_chunk(x[s:s + n]), rid, s))
        c.execute("UPDATE morlet_meta SET value=? WHERE key='created_by'", (writer,))
        c.commit()
    return x


def noisy(n, seed):
    rng = np.random.default_rng(seed)
    return np.round(np.sin(np.arange(n) / 11.0) * 3000 + rng.normal(0, 3, n)) / 512


class EncoderVersions(unittest.TestCase):
    def test_same_encoder(self):
        from morletdb._transfer import _same_encoder
        self.assertTrue(_same_encoder("morlet-sqlite 0.3.8", "pg_morlet 0.3.8"))
        self.assertFalse(_same_encoder("morlet-sqlite 0.3.9", "pg_morlet 0.3.8"))
        self.assertFalse(_same_encoder("unknown", "pg_morlet 0.3.8"))
        # Study B6: a server writing Grizzbit 2.1 is another encoder than a file writer of the same version.
        self.assertFalse(_same_encoder("morlet-sqlite 0.3.8", "pg_morlet 0.3.8 (Grizzbit 2.1)"))
        self.assertTrue(_same_encoder("pg_morlet 0.3.8 (Grizzbit 2.1)", "pg_morlet 0.3.8 (Grizzbit 2.1)"))
        self.assertTrue(_same_encoder("morlet-sqlite 0.3.8", "pg_morlet 0.3.8 (Grizzbit 1)"))


@unittest.skipUnless(DSN, "set MORLET_SDK_TEST_DSN to run the integration tests")
@unittest.skipIf(MISSING, f"morlet-sqlite library not available: {MISSING}")
class Transfer(unittest.TestCase):
    def setUp(self):
        self.schema = "sdk_xfer_" + uuid.uuid4().hex[:10]
        self.conn = psycopg.connect(DSN)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        # These tests compare chunk bytes between a file and a server writing the same coding: the file format 1
        # writer's Grizzbit 1. A server that writes Grizzbit 2.1 (study B6's arm, whose configuration may set it)
        # is tested in test_grizzbit21_server_and_format1_files.
        self.conn.execute("SELECT morlet_version()")
        self.conn.execute("SET morlet.chunk_format = grizzbit1")
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

    def test_grizzbit21_server_and_format1_files(self):
        """Study B6: a server writing Grizzbit 2.1 stores version-5 chunks where they are smaller; the transfer
        verifies samples, labels the server's encoder, does not compare chunk bytes, compressed reads through the
        native decoder return the samples bit for bit, and the exported file holds only chunk format versions 2
        and 3 (file format 1)."""
        self.conn.execute("SET morlet.chunk_format = grizzbit21")
        self.conn.commit()
        rng = np.random.default_rng(6)
        # Integer counts of a noisy resonance: Grizzbit 2.1 codes them with LPC, smaller than Grizzbit 1.
        e = rng.integers(-20, 21, 50_000)
        y = np.zeros(50_000)
        for i in range(2, 50_000):
            y[i] = round(1.95 * y[i - 1] - 0.97 * y[i - 2] + e[i])
        x = y.astype("<f8")
        src, out = Path(self.dir.name) / "src.morlet", Path(self.dir.name) / "out.morlet"
        with morletdb.open(src) as f:
            f.ingest("r", x, sample_rate_hz=1000, channel="x", unit="")
        report = []
        [rec] = self.db.import_file(src, self.coll, report=report)
        self.conn.commit()
        self.assertEqual(rec.samples, 50_000)
        self.assertTrue(report[0].receiver_encoder.endswith(" (Grizzbit 2.1)"), report[0].receiver_encoder)
        self.assertEqual((report[0].samples_verified, report[0].chunk_bytes), (50_000, "not compared"))
        versions = {c[2][0] for c in self.server_chunks("r")}
        self.assertIn(5, versions)
        self.assertTrue({c[2][0] for c in chunks_of_file(src, "r")} <= {2, 3})
        self.assertEqual(self.db.read(self.coll, "r", 0, 50_000).samples.tobytes(), x.tobytes())
        [rec] = self.db.export_file(self.coll, out, report=report)
        self.assertEqual((rec.samples, report[1].chunk_bytes), (50_000, "not compared"))
        self.assertTrue({c[2][0] for c in chunks_of_file(out, "r")} <= {2, 3})
        with morletdb.open(out) as f:
            self.assertEqual(f.read("r", 0, 50_000).samples.tobytes(), x.tobytes())

    def test_file_to_server_to_file(self):
        rng = np.random.default_rng(351)
        x = np.round(np.sin(np.arange(1_500_000) / 11.0) * 3000 + rng.normal(0, 3, 1_500_000)) / 512
        x[::997] = rng.normal(0, 1, len(x[::997]))
        src, back = Path(self.dir.name) / "src.morlet", Path(self.dir.name) / "back.morlet"
        with morletdb.open(src) as f:
            f.ingest("pump-7/accel-x", x[:1_000_000], sample_rate_hz=25_600, channel="accel-x", unit="m/s^2",
                     metadata={"asset": "pump-7"}, sample_quantum=1 / 512)
            f.append_chunks("pump-7/accel-x", 1_000_000, x[1_000_000:1_000_100], [1, 99])   # odd layout
        report = []
        [rec] = self.db.import_file(src, self.coll, report=report)
        self.conn.commit()
        self.assertEqual((rec.samples, rec.metadata["asset"]), (1_000_100, "pump-7"))
        [check] = report
        self.assertEqual((check.samples_copied, check.samples_verified, check.chunk_bytes), (1_000_100, 1_000_100, "identical"))
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

    # --- #356: samples are verified, not chunk bytes -------------------------------------------------------
    def server_samples(self, rid):
        return self.db.read(self.coll, rid, 0, self.db.recording(self.coll, rid).samples).samples

    def test_a_file_from_another_encoder_imports_verifies_and_resumes(self):
        x = noisy(700_000, 356)
        src = Path(self.dir.name) / "old.morlet"
        with morletdb.open(src) as f:
            f.ingest("r", x[:500_000], sample_rate_hz=25_600, channel="x", unit="", sample_quantum=1 / 512)
        reencode_file(src, "r")
        report = []
        [rec] = self.db.import_file(src, self.coll, report=report)
        self.conn.commit()
        self.assertEqual(rec.samples, 500_000)
        # The server encoded the same samples as other bytes: a chunk byte comparison would refuse this import.
        server, file = self.server_chunks("r"), chunks_of_file(src, "r")
        self.assertEqual([c[:2] for c in server], [c[:2] for c in file])
        self.assertTrue(all(a[2] != b[2] for a, b in zip(server, file)))
        self.assertEqual(self.server_samples("r").tobytes(), x[:500_000].tobytes())
        self.assertEqual(report, [morletdb.TransferCheck("r", 500_000, 500_000, "not compared",
                                                         "morlet-sqlite 0.0.1", report[0].receiver_encoder)])
        # Continue on the file (the library's own encoder), import again: the prefix is compared by its samples.
        with morletdb.open(src) as f:
            f.append("r", 500_000, x[500_000:])
        report = []
        [rec] = self.db.import_file(src, self.coll, report=report)
        self.conn.commit()
        self.assertEqual((rec.samples, report[0].samples_copied, report[0].samples_verified), (700_000, 200_000, 700_000))
        self.assertEqual(self.server_samples("r").tobytes(), x.tobytes())

    def test_a_changed_sample_is_refused_on_resume(self):
        x = noisy(300_000, 357)
        src = Path(self.dir.name) / "a.morlet"
        with morletdb.open(src) as f:
            f.ingest("r", x[:200_000], sample_rate_hz=1000, channel="x", unit="", sample_quantum=1 / 512)
        self.db.import_file(src, self.coll)
        self.conn.commit()
        with morletdb.open(src) as f:
            f.append("r", 200_000, x[200_000:])
        # One sample of the prefix one ulp away, written by "another encoder": other bytes and other samples.
        reencode_file(src, "r", change={123_456: np.nextafter(x[123_456], np.inf)})
        with self.assertRaisesRegex(morletdb.ConflictError, "first 200000 samples differ"):
            self.db.import_file(src, self.coll)
        self.conn.rollback()
        # The sign of zero counts too.
        z = Path(self.dir.name) / "z.morlet"
        with morletdb.open(z) as f:
            f.ingest("z", np.zeros(10_000), sample_rate_hz=1000, channel="x", unit="")
        self.db.import_file(z, self.coll)
        self.conn.commit()
        with morletdb.open(z) as f:
            f.append("z", 10_000, np.zeros(10))
        reencode_file(z, "z", change={5: -0.0})
        with self.assertRaises(morletdb.ConflictError):
            self.db.import_file(z, self.coll)

    def test_a_sample_changed_on_the_way_is_refused(self):
        x = noisy(100_000, 358)
        src = Path(self.dir.name) / "a.morlet"
        with morletdb.open(src) as f:
            f.ingest("r", x, sample_rate_hz=1000, channel="x", unit="")
        real = morletdb._transfer._append_batch

        def corrupt(client, collection, rid, batch, xs, a):
            xs = xs.copy()
            xs[len(xs) // 2] = np.nextafter(xs[len(xs) // 2], -np.inf)
            real(client, collection, rid, batch, xs, a)

        with mock.patch.object(morletdb._transfer, "_append_batch", corrupt):
            with self.assertRaisesRegex(morletdb.DatabaseError, "samples of .* differ after the import"):
                self.db.import_file(src, self.coll)

    def test_export_from_chunks_of_another_encoder_verifies_and_resumes(self):
        x = noisy(300_000, 359)
        self.db.ingest(self.coll, "r", x[:200_000], sample_rate_hz=1000, channel="x", unit="", sample_quantum=1 / 512,
                       search_window=None)
        self.conn.commit()
        # The server's chunks as another encoder would have written them.
        for s, n, _, _ in self.server_chunks("r"):
            self.conn.execute(f"UPDATE {self.coll}_chunks SET encoded=%s WHERE recording_id=%s AND start_sample=%s",
                              (raw_chunk(x[s:s + n]), "r", s))
        self.conn.commit()
        out = Path(self.dir.name) / "out.morlet"
        report = []
        with self.assertWarns(RuntimeWarning):      # same encoder version reported, other bytes: reported
            [rec] = self.db.export_file(self.coll, out, report=report)
        self.assertEqual((rec.samples, report[0].chunk_bytes), (200_000, "different"))
        self.assertTrue(all(a[2] != b[2] for a, b in zip(self.server_chunks("r"), chunks_of_file(out, "r"))))
        self.db.append(self.coll, "r", 200_000, x[200_000:])
        self.conn.commit()
        # The prefix differs in its bytes and is compared by its samples; the new chunks come out byte for byte.
        [rec] = self.db.export_file(self.coll, out, report=report)
        self.assertEqual((rec.samples, report[1].samples_copied, report[1].samples_verified, report[1].chunk_bytes),
                         (300_000, 100_000, 300_000, "identical"))
        with morletdb.open(out) as f:
            self.assertEqual(f.read("r", 0, 300_000).samples.tobytes(), x.tobytes())
        # A sample of the file's prefix changed: refused.
        self.db.append(self.coll, "r", 300_000, x[:1000])
        self.conn.commit()
        reencode_file(out, "r", change={42: x[42] + 1})
        with self.assertRaisesRegex(morletdb.ConflictError, "first 300000 samples differ"):
            self.db.export_file(self.coll, out)


if __name__ == "__main__":
    unittest.main()
