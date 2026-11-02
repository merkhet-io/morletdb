"""Readers of morlet_import.py on synthetic WAV, EDF and CSV files (no database)."""
import argparse
import json
import io
import contextlib
from pathlib import Path
import tempfile
import unittest
import wave

import numpy as np

import morlet_import as mi


def opts(**kw):
    base = dict(format=None, rate=None, time_column=None, unit=None, prefix="", extractor=None)
    base.update(kw)
    return argparse.Namespace(**base)


def write_edf(path, signals, nrec, duration=1.0, plus="EDF+C"):
    """signals: list of (label, unit, pmin, pmax, dmin, dmax, digital int16 array of nrec*k values)."""
    ns = len(signals)
    head = b"0".ljust(8) + b"X X X X".ljust(80) + b"Startdate X X X X".ljust(80) + b"01.01.25" + b"00.00.00"
    head += str(256 * (ns + 1)).encode().ljust(8) + plus.encode().ljust(44) + str(nrec).encode().ljust(8)
    head += str(duration).encode().ljust(8) + str(ns).encode().ljust(4)

    def col(values, width):
        return b"".join(str(v).encode().ljust(width) for v in values)
    k = [len(s[6]) // nrec for s in signals]
    head += col([s[0] for s in signals], 16) + col(["AgAgCl"] * ns, 80) + col([s[1] for s in signals], 8)
    head += col([s[2] for s in signals], 8) + col([s[3] for s in signals], 8) + col([s[4] for s in signals], 8)
    head += col([s[5] for s in signals], 8) + col(["HP:0.1Hz"] * ns, 80) + col(k, 8) + col([""] * ns, 32)
    body = b"".join(np.asarray(s[6][r * kk:(r + 1) * kk], "<i2").tobytes()
                    for r in range(nrec) for s, kk in zip(signals, k))
    Path(path).write_bytes(head + body)


class Readers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_wav_channels_are_integer_counts(self):
        for width, dtype, lo, hi in ((2, "<i2", -32768, 32767),):
            x = np.array([[lo, 0], [hi, 1], [-5, 7]], dtype)
            p = self.tmp / f"rig{width}.wav"
            with wave.open(str(p), "wb") as w:
                w.setnchannels(2); w.setsampwidth(width); w.setframerate(48000); w.writeframes(x.tobytes())
            recs = mi.recordings(p, opts())
            self.assertEqual([r.recording_id for r in recs], [f"rig{width}.ch0", f"rig{width}.ch1"])
            self.assertEqual(recs[0].samples.tolist(), [lo, hi, -5])
            self.assertEqual((recs[1].rate, recs[1].quantum, recs[1].unit), (48000.0, 1.0, "counts"))

    def test_wav_32_bit_is_stored_at_full_scale_exactly(self):
        x = np.array([-2**31, 2**31 - 1, -5, 0], "<i4")
        p = self.tmp / "hi.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(4); w.setframerate(96000); w.writeframes(x.tobytes())
        r = mi.recordings(p, opts())[0]
        self.assertEqual((r.unit, r.quantum), ("full-scale", 2.0 ** -31))
        self.assertEqual((r.samples * 2.0 ** 31).astype(np.int64).tolist(), x.tolist())

    def test_wav_24_and_8_bit(self):
        p = self.tmp / "a.wav"
        vals = [-8388608, 8388607, -1, 1]
        raw = b"".join(int(v & 0xFFFFFF).to_bytes(3, "little") for v in vals)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(3); w.setframerate(8000); w.writeframes(raw)
        self.assertEqual(mi.recordings(p, opts())[0].samples.tolist(), vals)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1); w.setsampwidth(1); w.setframerate(8000); w.writeframes(bytes([0, 128, 255]))
        self.assertEqual(mi.recordings(p, opts())[0].samples.tolist(), [-128, 0, 127])

    def test_edf_physical_values_and_quantum(self):
        d = np.array([-2048, 0, 2047, 100, -3] * 20, np.int16)
        ann = np.zeros(20 * 4, np.int16)
        p = self.tmp / "night.edf"
        write_edf(p, [("EEG Fpz-Cz", "uV", -208, 209, -2048, 2047, d), ("EDF Annotations", "", -1, 1, -32768, 32767, ann)],
                  nrec=20)
        recs = mi.recordings(p, opts())
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r.recording_id, r.rate, r.unit), ("night.EEG-Fpz-Cz", 5.0, "uV"))
        self.assertAlmostEqual(r.quantum, 417 / 4095)
        expect = (d.astype(float) + 2048) * 417 / 4095 - 208
        self.assertTrue(np.array_equal(r.samples.view(np.uint64), expect.view(np.uint64)))
        self.assertNotIn("X X X X", json.dumps(r.metadata))          # patient fields are not copied

    def test_edf_discontinuous_is_rejected(self):
        p = self.tmp / "d.edf"
        write_edf(p, [("EEG", "uV", -1, 1, -10, 10, np.zeros(10, np.int16))], nrec=2, plus="EDF+D")
        with self.assertRaisesRegex(ValueError, "EDF\\+D"):
            mi.recordings(p, opts())

    def test_csv_quantum_from_decimals(self):
        p = self.tmp / "log.csv"
        p.write_text("time,accel x,temp,label\n0,0.125,21.5,a\n1,-0.250,21.75,b\n2,1.000,22,c\n")
        recs = mi.recordings(p, opts(rate=100, time_column="time"))
        self.assertEqual([r.recording_id for r in recs], ["log.accel-x", "log.temp"])
        self.assertEqual(recs[0].quantum, 0.001)
        self.assertEqual(recs[1].quantum, 0.01)
        self.assertEqual(recs[0].samples.tolist(), [0.125, -0.25, 1.0])
        with self.assertRaisesRegex(ValueError, "--rate"):
            mi.recordings(p, opts())

    def test_invalid_samples_and_unknown_format(self):
        p = self.tmp / "big.csv"
        p.write_text("v\n1\n2e9\n")
        with self.assertRaisesRegex(ValueError, "1e9"):
            mi.recordings(p, opts(rate=10))
        with self.assertRaisesRegex(ValueError, "unknown format"):
            mi.recordings(self.tmp / "x.bin", opts())

    def test_chunks_carry_quantum_and_extractor(self):
        p = self.tmp / "slow.csv"
        p.write_text("v\n" + "\n".join(f"{i / 10:.1f}" for i in range(10000)) + "\n")
        cfg = mi.Config()
        chunks = list(mi.chunk_reader(opts(rate=50))(p, cfg, {}))
        self.assertEqual([c[2] for c in chunks], [0, 4096, 8192])
        rid, spec, _, values = chunks[0]
        self.assertEqual((rid, spec[4], spec[5]), ("slow.v", 0.1, "morlet-energy-lowrate-v1"))
        self.assertEqual(sum(len(c[3]) for c in chunks), 10000)

    def test_dry_run_prints_a_plan(self):
        p = self.tmp / "s.csv"
        p.write_text("a,b\n1,2\n3,4\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = mi.main([str(p), "--collection", "public.x", "--rate", "200", "--dry-run"])
        plan = json.loads(out.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual([r["recording_id"] for r in plan["recordings"]], ["s.a", "s.b"])
        self.assertEqual(plan["recordings"][0]["extractor"], "morlet-energy-v1")


if __name__ == "__main__":
    unittest.main()
