"""Reconstruction, layout and provenance contracts.

Expected values come from PyWavelets (an independent implementation) or from
analytical facts (orthonormality, Haar on simple signals) - never from msmem.
"""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import pywt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from msmem import dwt  # noqa: E402
from msmem.representation import CoefficientSet, RepresentationSpec  # noqa: E402


class DwtAgainstReference(unittest.TestCase):
    def test_db4_filter_matches_reference(self):
        np.testing.assert_allclose(dwt.DB4_LO, pywt.Wavelet("db4").dec_lo, atol=1e-15)
        np.testing.assert_allclose(dwt._filters("db4")[1], pywt.Wavelet("db4").dec_hi, atol=1e-15)

    def test_coefficients_match_pywavelets_periodization(self):
        rng = np.random.default_rng(1)
        for family in ("db4", "haar"):
            for n, levels in ((64, 1), (256, 3), (1024, 5)):
                x = rng.normal(size=n)
                ours = dwt.wavedec(x, family, levels)
                ref = pywt.wavedec(x, family, mode="periodization", level=levels)
                self.assertEqual([len(c) for c in ours], [len(c) for c in ref])
                for a, b in zip(ours, ref):
                    np.testing.assert_allclose(a, b, atol=1e-12)

    def test_perfect_reconstruction_and_parseval(self):
        rng = np.random.default_rng(2)
        x = rng.normal(size=1024)
        coeffs = dwt.wavedec(x, "db4", 5)
        np.testing.assert_allclose(dwt.waverec(coeffs, "db4"), x, atol=1e-12)
        energy = sum(float(c @ c) for c in coeffs)
        self.assertAlmostEqual(energy, float(x @ x), places=8)

    def test_haar_analytical_fixture(self):
        # Constant c over 8 samples: approximation at level 3 is c*sqrt(8), details 0.
        coeffs = dwt.wavedec(np.full(8, 2.0), "haar", 3)
        np.testing.assert_allclose(coeffs[0], [2.0 * np.sqrt(8)])
        for d in coeffs[1:]:
            np.testing.assert_allclose(d, 0.0, atol=1e-15)
        # A single step [1,1,1,1,-1,-1,-1,-1]: one level-3 detail of sqrt(8), approx 0.
        coeffs = dwt.wavedec(np.array([1, 1, 1, 1, -1, -1, -1, -1.0]), "haar", 3)
        np.testing.assert_allclose(coeffs[0], [0.0], atol=1e-15)
        np.testing.assert_allclose(np.abs(coeffs[1]), [np.sqrt(8)])

    def test_symmetric_extension_is_explicit(self):
        x = np.arange(10.0)
        y = dwt.extend(x, 2)  # next multiple of 4 is 12
        np.testing.assert_array_equal(y, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 8])


class RepresentationContracts(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(3)
        t = np.arange(1000) / 100.0
        self.x = np.sin(2 * np.pi * 1.2 * t) + 0.05 * rng.normal(size=1000)
        self.x[400:405] += 3.0  # a short, localized transient
        self.spec = RepresentationSpec()

    def test_full_reconstruction_preserves_raw_window(self):
        rep = CoefficientSet.from_samples(self.x, self.spec, "rec-1", 0)
        np.testing.assert_allclose(rep.reconstruct(), self.x, atol=1e-10)
        self.assertEqual(rep.source.end_sample, 1000)
        self.assertEqual(len(rep.source.samples_sha256), 64)

    def test_dropping_levels_is_an_orthogonal_projection(self):
        rep = CoefficientSet.from_samples(self.x, self.spec, "rec-1", 0)
        keep = {"a5", "d5", "d4"}
        dropped = sum(float(rep.blocks[n] @ rep.blocks[n]) for n, _ in self.spec.shapes() if n not in keep)
        coeffs = [rep.blocks[n] if n in keep else np.zeros(s) for n, s in self.spec.shapes()]
        padded = dwt.waverec(coeffs, "db4")
        full = dwt.waverec([rep.blocks[n] for n, _ in self.spec.shapes()], "db4")
        self.assertAlmostEqual(float((full - padded) @ (full - padded)), dropped, places=8)

    def test_layout_bands_depend_on_sample_rate(self):
        self.assertEqual([n for n, _ in self.spec.shapes()], ["a5", "d5", "d4", "d3", "d2", "d1"])
        self.assertEqual([s for _, s in self.spec.shapes()], [32, 32, 64, 128, 256, 512])
        self.assertEqual(self.spec.band_hz("d1"), (25.0, 50.0))
        other = replace(self.spec, sample_rate_hz=500.0)
        self.assertEqual(other.band_hz("d1"), (125.0, 250.0))
        self.assertNotEqual(self.spec.id, other.id)

    def test_serialization_is_deterministic_and_selective(self):
        rep = CoefficientSet.from_samples(self.x, self.spec, "rec-1", 0)
        blob = rep.to_bytes()
        self.assertEqual(blob, CoefficientSet.from_samples(self.x, self.spec, "rec-1", 0).to_bytes())
        full, full_bytes = CoefficientSet.read(blob)
        np.testing.assert_allclose(full.reconstruct(), self.x, atol=1e-10)
        coarse, coarse_bytes = CoefficientSet.read(blob, keep={"a5"})
        self.assertEqual(full_bytes - coarse_bytes, 8 * (1024 - 32))
        with self.assertRaises(ValueError):
            CoefficientSet.read(blob, expected_spec=replace(self.spec, family="haar"))

    def test_refinement_is_deterministic_and_keeps_raw(self):
        rep = CoefficientSet.from_samples(self.x, self.spec, "rec-1", 0)
        a = rep.refinement(16)
        self.assertEqual(a, rep.refinement(16))
        self.assertEqual(len(a), 16)
        magnitudes = [abs(v) for _, _, v in a]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))
        # The stored set keeps every coefficient regardless of any budgeted context.
        self.assertEqual(sum(len(b) for b in rep.blocks.values()), 1024)
        # Candidates restrict selection; coarse + all details reconstructs exactly.
        only_d1 = rep.refinement(8, candidates=["d1"])
        self.assertTrue(all(name == "d1" for name, _, _ in only_d1))
        everything = rep.refinement(1024 - 32)
        normalized = (self.x - rep.scale[0]) / rep.scale[1]
        np.testing.assert_allclose(rep.reconstruct_from({"a5"}, everything), normalized, atol=1e-10)

    def test_rejects_invalid_windows(self):
        with self.assertRaises(ValueError):
            CoefficientSet.from_samples(self.x[:999], self.spec, "rec-1", 0)
        bad = self.x.copy()
        bad[3] = np.nan
        with self.assertRaises(ValueError):
            CoefficientSet.from_samples(bad, self.spec, "rec-1", 0)


if __name__ == "__main__":
    unittest.main()
