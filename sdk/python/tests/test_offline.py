"""Input validation and time handling, without a database."""
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from morletdb import InvalidInputError
from morletdb._client import Client, _iso, _meta_start_ns, _samples, _start_ns
from morletdb._errors import ConflictError, NotFoundError, translate


class SampleValidation(unittest.TestCase):
    def test_exact_widening(self):
        for dtype in (np.float64, np.float32, np.float16, np.int8, np.int16, np.int32, np.uint16):
            a = (np.arange(-50, 50) * 3).astype(dtype) if np.dtype(dtype).kind != "u" else np.arange(100, dtype=dtype)
            x = _samples(a)
            self.assertEqual(x.dtype, np.float64)
            np.testing.assert_array_equal(x, a.astype(np.float64))
        big = np.array([10 ** 9, -(10 ** 9)], dtype=np.int64)
        np.testing.assert_array_equal(_samples(big), big.astype(np.float64))

    def test_refusals(self):
        bad = [np.zeros((2, 2)), np.array([], dtype=float), np.array([1 + 2j]), np.array([True, False]),
               np.array(["a"]), np.array([1.0, np.nan]), np.array([np.inf]), np.array([1e9 + 1]), np.array([2 ** 31 - 1], dtype=np.int32),
               np.array([2 ** 53 + 1], dtype=np.int64),
               np.array([1.0], dtype=np.longdouble) if np.dtype(np.longdouble).itemsize > 8 else np.zeros((1, 1))]
        for a in bad:
            with self.assertRaises(InvalidInputError, msg=repr(a)):
                _samples(a)

    def test_sign_of_zero_and_subnormals_survive(self):
        x = _samples([-0.0, 5e-324, -5e-324, 1e9, -1e9])
        self.assertEqual(np.signbit(x[0]), True)
        self.assertEqual(x[1], 5e-324)


class Timing(unittest.TestCase):
    def test_start_time_conversions(self):
        t = datetime(2026, 3, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
        ns = _start_ns(t)
        self.assertEqual(ns, 1772366400123456000)
        self.assertEqual(_start_ns(t.astimezone(timezone(timedelta(hours=5)))), ns)
        self.assertEqual(_start_ns(ns + 789), ns + 789)
        self.assertEqual(_iso(ns + 789), "2026-03-01T12:00:00.123456789Z")
        self.assertEqual(_iso(1772366400 * 10 ** 9), "2026-03-01T12:00:00Z")
        with self.assertRaises(InvalidInputError):
            _start_ns(datetime(2026, 3, 1))            # naive
        with self.assertRaises(InvalidInputError):
            _start_ns("2026-03-01")

    def test_metadata_start(self):
        self.assertEqual(_meta_start_ns({"start_ns": 5, "start": "1970-01-01T00:00:01Z"}), 5)
        self.assertEqual(_meta_start_ns({"start": "1970-01-01T00:00:01Z"}), 10 ** 9)
        self.assertIsNone(_meta_start_ns({"start": "1970-01-01T00:00:01"}))   # naive: unknown zone
        self.assertIsNone(_meta_start_ns({}))


class ErrorTranslation(unittest.TestCase):
    def test_messages(self):
        self.assertIsInstance(translate(Exception("conflicting replay at chunk offset")), ConflictError)
        self.assertIsInstance(translate(Exception("source recording does not exist or is not visible")), NotFoundError)
        self.assertIsInstance(translate(Exception("query interval must match the stored representation window")),
                              InvalidInputError)


if __name__ == "__main__":
    unittest.main()


class ReaderChoice(unittest.TestCase):
    """Raw reads are chosen per collection: an upgraded server can hold legacy float8[] collections."""

    class Fake(Client):
        def __init__(self, has_raw, encoded):
            self._functions = {"public.morlet_read_interval_raw(regclass,text,bigint,bigint)": has_raw}
            self._encoded = {}
            self.layout = encoded
            self.queries = []

        def _one(self, sql, args=(), **kw):
            self.queries.append(sql)
            return self.layout[args[0]]

    def test_per_collection(self):
        c = self.Fake(True, {"legacy": False, "encoded": True})
        self.assertFalse(c._raw_reads("legacy"))
        self.assertTrue(c._raw_reads("encoded"))
        self.assertFalse(c._raw_reads("legacy"))
        self.assertEqual(len(c.queries), 2)  # looked up once per collection
        self.assertTrue(all("morlet_stream_relations" in q for q in c.queries))

    def test_old_server(self):
        c = self.Fake(False, {"encoded": True})
        self.assertFalse(c._raw_reads("encoded"))
        self.assertEqual(c.queries, [])
