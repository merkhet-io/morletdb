#!/usr/bin/env python3
"""Synthetic checks of the S1 harness (no PhysioNet data)."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s1  # noqa: E402


def write_edf(path, signals, duration, start=("01.01.89", "22.00.00"), annotations=None):
    """signals: list of (label, unit, samples_per_record, int16 data). annotations: bytes for an EDF+ annotation signal."""
    sigs = list(signals)
    if annotations is not None:
        n = (len(annotations) + 1) // 2
        sigs.append(("EDF Annotations", "", n, np.frombuffer(annotations.ljust(2 * n, b"\x00"), dtype="<i2")))
    ns = len(sigs)
    records = len(sigs[0][3]) // sigs[0][2]

    def f(v, w):
        return str(v).ljust(w)[:w].encode("latin-1")

    h = f("0", 8) + f("", 80) + f("", 80) + f(start[0], 8) + f(start[1], 8) + f(256 * (ns + 1), 8) + f("", 44)
    h += f(records, 8) + f(duration, 8) + f(ns, 4)
    for name, width, vals in (("label", 16, [s[0] for s in sigs]), ("transducer", 80, [""] * ns),
                              ("unit", 8, [s[1] for s in sigs]), ("pmin", 8, [-3276.8] * ns),
                              ("pmax", 8, [3276.7] * ns), ("dmin", 8, [-32768] * ns), ("dmax", 8, [32767] * ns),
                              ("prefilter", 80, [""] * ns), ("n", 8, [s[2] for s in sigs]), ("reserved", 32, [""] * ns)):
        h += b"".join(f(v, width) for v in vals)
    body = b"".join(b"".join(s[3][r * s[2]:(r + 1) * s[2]].astype("<i2").tobytes() for s in sigs) for r in range(records))
    Path(path).write_bytes(h + body)


class TestS1(unittest.TestCase):
    def test_edf_roundtrip_and_hypnogram(self):
        with tempfile.TemporaryDirectory() as d:
            n_epochs = 200
            x = (np.sin(np.arange(n_epochs * s1.EPOCH) / 7) * 1000).astype(np.int16)
            write_edf(Path(d) / "SC4011E0-PSG.edf", [("EEG Fpz-Cz", "uV", 3000, x), ("EOG horizontal", "uV", 3000, x)], 30)
            tal = b"+0\x14\x14\x00"
            for onset, dur, st in ((0, 3000, "W"), (3000, 600, "1"), (3600, 900, "2"), (4500, 300, "4"),
                                   (4800, 300, "R"), (5100, 30, "?"), (5130, 870, "W")):
                tal += f"+{onset}\x15{dur}\x14Sleep stage {st}\x14\x00".encode()
            # annotation-only EDF+: one record carrying the TALs
            write_edf(Path(d) / "SC4011EH-Hypnogram.edf",
                      [("EDF Annotations", "", (len(tal) + 1) // 2, np.frombuffer(tal.ljust(len(tal) + len(tal) % 2, b"\x00"), "<i2"))], 0)
            sig, rate, unit, _ = s1.read_signal(Path(d) / "SC4011E0-PSG.edf", "EEG Fpz-Cz")
            self.assertEqual(rate, 100.0)
            self.assertEqual(unit, "uV")
            np.testing.assert_allclose(sig, x * 0.1, atol=1e-9)
            files = s1.night_files(d, 1)
            self.assertEqual([f[0] for f in files], [1])
            _, _, idx, y = s1.epochs_of_night(files[0][1], files[0][2])
            # sleep runs from epoch 100 to 169; keep 60 before and 60 after, drop the unscored epoch 170
            self.assertEqual(idx[0], 40)
            self.assertNotIn(170, idx)
            self.assertEqual(idx[-1], 199)
            self.assertEqual(dict(zip(*np.unique(y, return_counts=True))), {0: 60 + 29, 1: 20, 2: 30, 3: 10, 4: 10})

    def test_fft_band_layout(self):
        e = s1.layout_edges()
        self.assertEqual(len(e), 13)
        self.assertTrue(np.all(np.diff(e) > 0) and e[-1] < 50)
        t = np.arange(s1.EPOCH) / s1.RATE
        f, p = s1.welch(np.sin(2 * np.pi * 10 * t))
        bp = s1.band_powers(f, p, e)
        c = s1.centres()
        self.assertEqual(int(np.argmax(bp)), int(np.argmin(np.abs(np.log(c / 10)))))
        self.assertAlmostEqual(float(np.sum(p) * (f[1] - f[0])), 0.5, delta=0.02)

    def test_kappa_vote_lr(self):
        y = np.array([0, 1, 2, 3, 4] * 20)
        self.assertAlmostEqual(s1.kappa(y, y), 1.0)
        self.assertEqual(list(s1.vote(np.array([[2, 1, 1, 2], [3, 3, 0, 0]]))), [2, 3])
        rng = np.random.default_rng(0)
        x = np.eye(5)[y] * 4 + rng.normal(size=(100, 5))
        W, ok, _ = s1.lr_fit(x, y, 1.0)
        self.assertTrue(ok)
        self.assertGreater(s1.kappa(y, s1.lr_predict(W, x)), 0.9)
        nb = s1.knn_neighbours(x, x, 1)
        self.assertTrue(np.all(nb[:, 0] == np.arange(100)))


if __name__ == "__main__":
    unittest.main()
