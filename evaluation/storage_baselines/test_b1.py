#!/usr/bin/env python3
"""B1 harness checks on synthetic data only: every arm round-trips and sizes are sane."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import measure as M  # noqa: E402

BENCH = HERE.parent.parent / "target/release/examples/rawcodec_bench"


def calibrated(n, seed=0):
    """EDF-like: asymmetric range, values (d - dmin) * step + pmin in float64."""
    rng = np.random.default_rng(seed)
    d = np.round(np.cumsum(rng.normal(0, 30, n)) % 4000 - 2000).astype(np.int64)
    pmin, pmax, dmin, dmax = -208.0, 209.0, -2048.0, 2047.0
    return (d - dmin) * (pmax - pmin) / (dmax - dmin) + pmin, d, (pmax - pmin) / (dmax - dmin) / 2


class Arms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_float_arms_round_trip_and_beat_raw(self):
        x, d, _ = calibrated(409600)
        lengths = np.full(100, 4096)
        for nbytes in (M.arm_zstd(x, lengths), M.arm_parquet(x, "BYTE_STREAM_SPLIT"), M.arm_duckdb(x, "DOUBLE", self.tmp)[0]):
            self.assertLess(M.bits(nbytes, len(x)), 64)

    def test_integer_arms_round_trip(self):
        _, d, _ = calibrated(409600)
        self.assertLess(M.bits(M.arm_parquet(d, "DELTA_BINARY_PACKED"), len(d)), 16)
        self.assertLess(M.bits(M.arm_duckdb(d, "BIGINT", self.tmp)[0], len(d)), 16)
        self.assertLess(M.bits(M.arm_flac(d), len(d)), 16)

    def test_flac_refuses_more_than_24_bits(self):
        self.assertIsNone(M.arm_flac(np.array([0, 1 << 23], dtype=np.int64)))

    def test_duckdb_measures_payload_not_the_empty_table(self):
        small = M.arm_duckdb(np.zeros(10), "DOUBLE", self.tmp)[0]
        self.assertLessEqual(small, M.BLOCK)

    @unittest.skipUnless(BENCH.exists(), "build: cargo build --release -p morlet-core --example rawcodec_bench")
    def test_morlet_codes_calibrated_floats_on_a_grid(self):
        x, _, q = calibrated(409600)
        lengths = np.full(100, 4096)
        r = M.arm_morlet(x, lengths, np.full(100, q), BENCH, self.tmp)
        self.assertEqual(r["grid_chunks"], 100)
        self.assertLess(r["bits_per_sample"], 16)


if __name__ == "__main__":
    unittest.main()
