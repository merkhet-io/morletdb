#!/usr/bin/env python3
"""Checks the reference reader (standard library only) against the committed corpus (testdata/morlet-file-1), and,
when the morlet-sqlite library is available (MORLETDB_SQLITE_LIB or a platform wheel), against fresh files written
by it. Run: python reference/python/test_reference.py"""
import hashlib
import json
import random
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent.parent / "testdata" / "morlet-file-1"
sys.path.insert(0, str(HERE))
import morletfile  # noqa: E402


class Corpus(unittest.TestCase):
    def test_files_are_unchanged(self):
        for line in (CORPUS / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split()
            self.assertEqual(hashlib.sha256((CORPUS / name).read_bytes()).hexdigest(), digest, name)

    def test_every_case_reads_bit_for_bit(self):
        expected = json.loads((CORPUS / "expected.json").read_text())
        recs = {r["recording_id"]: r for r in morletfile.recordings(CORPUS / "corpus.morlet")}
        self.assertEqual(set(recs), set(expected))
        for name, e in expected.items():
            self.assertEqual(recs[name]["end_sample"], e["samples"], name)
            x = morletfile.read(CORPUS / "corpus.morlet", name, 0, e["samples"])
            self.assertEqual(hashlib.sha256(struct.pack(f"<{len(x)}d", *x)).hexdigest(), e["sha256_float64le"], name)
            self.assertEqual([repr(v) for v in x[:4]], e["first"], name)

    def test_corruption_is_detected(self):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(CORPUS / "corpus.morlet")) as c:
            enc = bytearray(c.execute("SELECT encoded FROM chunks LIMIT 1").fetchone()[0])
        enc[len(enc) // 2] ^= 1
        with self.assertRaisesRegex(morletfile.FormatError, "checksum"):
            morletfile.decode_chunk(enc)


class AgainstTheLibrary(unittest.TestCase):
    """Random files from the writer (morletdb.open) read the same through the reference reader."""

    def test_random_files(self):
        try:
            sys.path.insert(0, str(HERE.parent.parent / "sdk" / "python" / "src"))
            import numpy as np
            import morletdb
            morletdb._local._lib()
        except Exception as e:      # numpy, the SDK or the library missing
            self.skipTest(f"writer not available: {e}")
        rnd = np.random.default_rng(7)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.morlet"
            with morletdb.open(f) as db:
                for k in range(12):
                    n = int(rnd.integers(1, 60_000))
                    q = [1.0, 1 / 512, 0.1, 0.0625][k % 4]
                    x = {0: np.round(rnd.normal(0, 1000, n)) * q, 1: rnd.normal(0, 1, n),
                         2: 17.3 + np.round(rnd.normal(0, 100, n)) * 0.01}[k % 3]
                    x[rnd.integers(0, n, max(1, n // 500))] = -0.0
                    db.ingest(f"r{k}", x, sample_rate_hz=1000.0, channel="x", unit="", sample_quantum=q)
                    a = int(rnd.integers(0, n))
                    b = int(rnd.integers(a + 1, n + 1))
                    got = morletfile.read(f, f"r{k}", a, b)
                    self.assertEqual(struct.pack(f"<{len(got)}d", *got), x[a:b].astype("<f8").tobytes(), f"r{k}")


if __name__ == "__main__":
    random.seed(0)
    unittest.main(verbosity=2)
