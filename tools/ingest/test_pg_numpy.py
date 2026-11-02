"""pg_numpy writes and reads exactly the bytes psycopg's own binary float8[] adapters use (no database needed)."""
import unittest

import numpy as np
from psycopg.adapt import PyFormat, Transformer

import pg_numpy


def special_values():
    rng = np.random.default_rng(20261102)
    x = np.concatenate([rng.standard_normal(5000) * 10.0 ** rng.integers(-300, 300, 5000),
                        [0.0, -0.0, np.inf, -np.inf, 5e-324, -5e-324, np.finfo(float).max, np.finfo(float).tiny]])
    payload_nan = np.frombuffer(np.uint64(0x7FF8000000000123).tobytes(), np.float64)
    return np.concatenate([x, payload_nan])


def reference_dump(values):
    tx = Transformer()
    return bytes(tx.get_dumper(values, PyFormat.BINARY).dump(values))


class Float8ArrayAdapterTests(unittest.TestCase):
    def test_dump_matches_psycopg_binary_list_dump(self):
        x = special_values()[:-1]                      # psycopg's float dumper may canonicalise NaN payloads
        self.assertEqual(bytes(pg_numpy.Float8ArrayDumper(np.ndarray).dump(x)), reference_dump(x.tolist()))

    def test_round_trip_is_bit_exact_including_nan_payload(self):
        x = special_values()
        data = pg_numpy.Float8ArrayDumper(np.ndarray).dump(x)
        y = pg_numpy.Float8ArrayLoader(pg_numpy.FLOAT8_ARRAY_OID).load(data)
        self.assertEqual(y.dtype, np.float64)
        np.testing.assert_array_equal(y.view(np.uint64), x.view(np.uint64))

    def test_loader_reads_psycopg_dump(self):
        x = special_values()[:-1]
        y = pg_numpy.Float8ArrayLoader(pg_numpy.FLOAT8_ARRAY_OID).load(reference_dump(x.tolist()))
        np.testing.assert_array_equal(y.view(np.uint64), x.view(np.uint64))

    def test_empty_and_float32(self):
        dumper, loader = pg_numpy.Float8ArrayDumper(np.ndarray), pg_numpy.Float8ArrayLoader(pg_numpy.FLOAT8_ARRAY_OID)
        self.assertEqual(loader.load(dumper.dump(np.empty(0))).shape, (0,))
        x = np.linspace(-1, 1, 101, dtype=np.float32)
        np.testing.assert_array_equal(loader.load(dumper.dump(x)), x.astype(np.float64))

    def test_refuses_what_float64_cannot_hold(self):
        dumper, loader = pg_numpy.Float8ArrayDumper(np.ndarray), pg_numpy.Float8ArrayLoader(pg_numpy.FLOAT8_ARRAY_OID)
        with self.assertRaises(TypeError):
            dumper.dump(np.arange(4))                 # integers: callers convert explicitly
        with self.assertRaises(TypeError):
            dumper.dump(np.zeros((2, 2)))
        with self.assertRaises(ValueError):
            loader.load(reference_dump([1.0, None]))   # NULL element


if __name__ == "__main__":
    unittest.main()
