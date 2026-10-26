"""Round-trip controls for blockcodecs.py: lossless exactness and empirical error bounds."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blockcodecs as c  # noqa: E402


def signals():
    rng = np.random.default_rng(0)
    n = 4096 * 8
    t = np.arange(n)
    impulses = np.zeros(n)
    impulses[::613] = 400
    yield "harmonic+noise+impulses", np.round(800 * np.sin(2 * np.pi * t / 97) + rng.normal(0, 30, n) + impulses).astype(np.int64)
    yield "random walk", np.cumsum(rng.integers(-50, 51, n))
    yield "odd length", rng.integers(-2000, 2000, 4099)
    yield "constant", np.full(8192, 7)


class Lossless(unittest.TestCase):
    def test_round_trip(self):
        for name, x in signals():
            for enc, dec in ((c.int_zstd, c.int_zstd_dec), (c.delta_zstd, c.delta_zstd_dec),
                             (c.flac, c.flac_dec), (c.lift53_zstd, c.lift53_zstd_dec)):
                y = dec(enc(x))
                self.assertTrue(np.array_equal(y, x), f"{enc.__name__} {name}")

    def test_lift53_is_reversible_by_level(self):
        x = np.random.default_rng(1).integers(-10**6, 10**6, 1000)
        self.assertTrue(np.array_equal(c.unlift53(c.lift53(x, 9)), x))


class ErrorBounded(unittest.TestCase):
    def test_bounds(self):
        for name, x in signals():
            if len(x) % 4096:
                continue
            for eps in (0.5, 2.0, 16.0):
                cases = {
                    "quant_zstd": c.quant_zstd_dec(c.quant_zstd(x, eps)),
                    "quant_flac": c.quant_flac_dec(c.quant_flac(x, eps)),
                    "dwt_q": c.dwt_q_dec(c.dwt_q(x, eps)),
                    "harmonic": c.harmonic_residual_dec(c.harmonic_residual(x, eps)),
                    "tsa": c.tsa_residual_dec(c.tsa_residual(x, eps, 97.0)),
                    "sz3": c.h5_dec(c.sz3(x, eps)[0]),
                    "zfp": c.zfp_dec(c.zfp(x, eps)[0]),
                    "sperr": c.h5_dec(c.sperr(x, eps)[0]),
                }
                for codec, y in cases.items():
                    err = np.abs(y - x).max()
                    self.assertLessEqual(err, eps * (1 + 1e-9) + 1e-9, f"{codec} {name} eps={eps}: {err}")

    def test_half_count_is_lossless_on_integers(self):
        x = next(signals())[1]
        self.assertTrue(np.array_equal(c.quant_flac_dec(c.quant_flac(x, 0.5)), x))

    def test_partial_decode_uses_fewer_subbands(self):
        x = next(signals())[1].astype(float)
        b = c.dwt_q(x, 2.0)
        coarse = c.dwt_q_dec(b, keep_levels=1)
        self.assertEqual(len(coarse), len(x))
        self.assertGreater(np.abs(coarse - x).max(), 2.0)


if __name__ == "__main__":
    unittest.main()
