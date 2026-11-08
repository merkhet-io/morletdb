"""The native decoder for compressed transfer, without a database: the frozen golden chunks decode through it bit
for bit, and every corruption is refused. Skipped when the library is not available (MORLETDB_NATIVE_LIB or a
platform wheel); with MORLET_SDK_REQUIRE_NATIVE=1 a missing library fails."""
import os
import struct
import unittest
from pathlib import Path

import numpy as np

from morletdb import InvalidInputError
from morletdb import _native
from morletdb._errors import DatabaseError

GOLDEN = Path(__file__).resolve().parents[3] / "testdata" / "rawcodec"


def _crc32c(data: bytes) -> int:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        table.append(c)
    c = 0xFFFFFFFF
    for b in data:
        c = table[(c ^ b) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


def chunk_frame(chunks, start, end):
    body = b"MTX1" + struct.pack("<qqI", start, end, len(chunks))
    for s, c in chunks:
        body += struct.pack("<qI", s, len(c)) + c
    return body + struct.pack("<I", _crc32c(body))


NATIVE = _native.native()
if NATIVE is None and os.environ.get("MORLET_SDK_REQUIRE_NATIVE") == "1":
    raise RuntimeError("MORLET_SDK_REQUIRE_NATIVE=1 but the native decoder library was not found")


@unittest.skipUnless(NATIVE, "native decoder library not available (set MORLETDB_NATIVE_LIB)")
class NativeDecoder(unittest.TestCase):
    def golden(self):
        files = sorted(GOLDEN.glob("*.chunk"))
        self.assertGreaterEqual(len(files), 16)
        for f in files:
            yield f.name, f.read_bytes(), np.frombuffer(f.with_suffix(".f64").read_bytes(), dtype="<u8")

    def test_golden_chunks_bit_for_bit(self):
        for name, chunk, want in self.golden():
            n = len(want)
            for a, b in [(0, n), (1, n), (n - 1, n)]:
                if b <= a:
                    continue
                got = NATIVE.decode(chunk_frame([(0, chunk)], a, b), a, b)
                self.assertEqual(got.dtype, np.float64)
                np.testing.assert_array_equal(got.view("<u8"), want[a:b], err_msg=name)

    def test_chunks_join_across_edges(self):
        chunks = [c for _, c, _ in self.golden()][:4]
        lengths = [len(w) for _, _, w in self.golden()][:4]
        starts = np.concatenate([[0], np.cumsum(lengths)[:-1]]).tolist()
        want = np.concatenate([w for _, _, w in self.golden()][:4])
        total = int(sum(lengths))
        got = NATIVE.decode(chunk_frame(list(zip(starts, chunks)), 0, total), 0, total)
        np.testing.assert_array_equal(got.view("<u8"), want)

    def test_corruption_and_gaps_are_refused(self):
        _, chunk, want = next(self.golden())
        n = len(want)
        frame = chunk_frame([(0, chunk)], 0, n)
        for i in range(0, len(frame), max(1, len(frame) // 50)):
            bad = bytearray(frame)
            bad[i] ^= 0x04
            with self.assertRaises(DatabaseError, msg=f"byte {i}"):
                NATIVE.decode(bytes(bad), 0, n)
        with self.assertRaises(DatabaseError):           # a chunk short of the interval
            NATIVE.decode(chunk_frame([(0, chunk)], 0, n + 1), 0, n + 1)
        bad_chunk = bytearray(chunk)
        bad_chunk[len(chunk) // 2] ^= 1                  # corrupt chunk under a valid frame checksum
        with self.assertRaisesRegex(DatabaseError, "corrupt chunk at sample 77"):
            NATIVE.decode(chunk_frame([(77, bytes(bad_chunk))], 77, 77 + n), 77, 77 + n)
        with self.assertRaises(InvalidInputError):
            NATIVE.decode(frame, 0, 0)

    def test_mode_is_validated(self):
        old = os.environ.get("MORLETDB_TRANSFER")
        try:
            os.environ["MORLETDB_TRANSFER"] = "fast"
            with self.assertRaises(InvalidInputError):
                _native.mode()
        finally:
            if old is None:
                os.environ.pop("MORLETDB_TRANSFER", None)
            else:
                os.environ["MORLETDB_TRANSFER"] = old


if __name__ == "__main__":
    unittest.main()
