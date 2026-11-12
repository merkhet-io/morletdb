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
    base = dict(format=None, rate=None, time_column=None, unit=None, prefix="", extractor=None, context_columns=None)
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

    def test_wide_export_with_time_and_controller_columns(self):
        """#388: one time column, signals, and a controller column written only on change."""
        p = self.tmp / "plant.csv"
        lines = ["time,program,block,spindle_current,vib"]
        for i in range(10):
            t = f"2026-03-01T08:00:00.{i * 100:03d}Z" if i < 9 else "2026-03-01T08:00:00.950Z"   # last row late
            prog = "P100" if i == 0 else ""
            block = {0: "N10", 4: "N20"}.get(i, "")
            lines.append(f"{t},{prog},{block},{1.5 + i / 10:.2f},{(-1) ** i * 0.25:.3f}")
        p.write_text("\n".join(lines) + "\n")
        recs = mi.recordings(p, opts(time_column="time", context_columns="program,block"))
        self.assertEqual([r.recording_id for r in recs], ["plant.spindle_current", "plant.vib"])
        r = recs[0]
        self.assertEqual(r.rate, 10.0)                             # median spacing 0.1 s, to 9 digits
        self.assertEqual(r.metadata["start"], "2026-03-01T08:00:00Z")
        ax = r.metadata["time_axis"]
        self.assertEqual(ax["rate_source"], "median spacing of the time column")
        self.assertAlmostEqual(ax["max_offset_s"], 0.05)           # the late last row is reported, not hidden
        self.assertEqual((ax["steps_backward"], ax["steps_zero"]), (0, 0))
        self.assertEqual(sorted(r.context), [("block", "N10", 0, 4), ("block", "N20", 4, 10),
                                             ("program", "P100", 0, 10)])
        self.assertEqual(recs[1].context, r.context)
        with_rate = mi.recordings(p, opts(time_column="time", rate=10, context_columns="block"))
        self.assertEqual(with_rate[0].metadata["time_axis"]["rate_source"], "--rate")
        self.assertEqual([x.recording_id for x in with_rate], ["plant.spindle_current", "plant.vib"])
        with self.assertRaisesRegex(ValueError, "no context column"):
            mi.recordings(p, opts(time_column="time", context_columns="tool"))

    def test_seconds_time_column_and_backward_steps(self):
        p = self.tmp / "rel.csv"
        p.write_text("t,a\n0,1\n0.5,2\n1.0,3\n0.9,4\n2.0,5\n")
        r = mi.recordings(p, opts(time_column="t"))[0]
        self.assertAlmostEqual(r.rate, 2.0)
        self.assertNotIn("start", r.metadata)                     # relative seconds give no start time
        self.assertEqual(r.metadata["time_axis"]["steps_backward"], 1)
        with self.assertRaisesRegex(ValueError, "neither numbers"):
            q = self.tmp / "bad.csv"
            q.write_text("t,a\nmonday,1\ntuesday,2\n")
            mi.recordings(q, opts(time_column="t"))

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



class Exchange(unittest.TestCase):
    """morlet-exchange files (written as morlet_export.py writes them) read back exactly."""

    def write(self, path, recs, tamper=False):
        import hashlib
        import pyarrow as pa
        import pyarrow.parquet as pq
        import morlet_export as me
        manifest = {"format": "morlet-exchange", "version": 1, "recordings": {}}
        rows = []
        for rid, x, quantum, meta in recs:
            x = np.asarray(x, "<f8")
            manifest["recordings"][rid] = {"samples": int(x.size), "sha256": hashlib.sha256(x.tobytes()).hexdigest()}
            for a in range(0, x.size, 3):
                part = x[a:a + 3].tolist()
                if tamper and a == 0:
                    part[0] += 0.001
                rows.append({"recording_id": rid, "sample_rate_hz": 1000.0, "channel": "acc", "unit": "g",
                             "metadata": json.dumps(meta), "sample_quantum": quantum, "extractor": "morlet-energy-v1",
                             "start_sample": a, "samples": part})
        w = pq.ParquetWriter(path, me.SCHEMA)
        w.write_batch(pa.RecordBatch.from_pylist(rows, me.SCHEMA))
        w.add_key_value_metadata({me.KEY: json.dumps(manifest).encode()})
        w.close()

    def test_exchange_round_trip_and_checksum(self):
        tmp = Path(tempfile.mkdtemp())
        f = tmp / "x.parquet"
        self.write(f, [("a", [0.001, -0.002, 0.003, 0.004, 0.005], 0.001, {"asset": "p1"}),
                       ("b", [1.0, 2.0, 3.0, 4.0], 1.0, {})])
        recs = mi.recordings(f, opts())
        self.assertEqual([r.recording_id for r in recs], ["a", "b"])
        self.assertEqual(recs[0].samples.tolist(), [0.001, -0.002, 0.003, 0.004, 0.005])
        self.assertEqual((recs[0].quantum, recs[0].metadata, recs[0].rate, recs[0].unit), (0.001, {"asset": "p1"}, 1000.0, "g"))
        bad = tmp / "bad.parquet"
        self.write(bad, [("a", [0.001, 0.002, 0.003], 0.001, {})], tamper=True)
        with self.assertRaisesRegex(ValueError, "checksum"):
            mi.recordings(bad, opts())

    def test_plain_parquet_is_not_an_exchange_file(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        f = Path(tempfile.mkdtemp()) / "plain.parquet"
        pq.write_table(pa.table({"x": [1, 2]}), f)
        with self.assertRaisesRegex(ValueError, "not a MorletDB exchange file"):
            mi.recordings(f, opts())


def write_tdms(path):
    """A synthetic TDMS file written with npTDMS (not NI software): scaled int16, float, untimed, string."""
    from nptdms import TdmsWriter, ChannelObject
    t0 = np.datetime64("2024-05-06T07:08:09.000000", "us")
    raw = np.array([-3, -1, 0, 2, 5, 32767, -32768], dtype=np.int16)
    accel = ChannelObject("Bench", "accel", raw, properties={
        "wf_increment": 1 / 20000, "wf_start_time": t0, "wf_start_offset": 0.5, "unit_string": "g",
        "NI_Scaling_Status": "unscaled", "NI_Number_Of_Scales": 1, "NI_Scale[0]_Scale_Type": "Linear",
        "NI_Scale[0]_Linear_Slope": 0.001, "NI_Scale[0]_Linear_Y_Intercept": 0.5,
        "NI_Scale[0]_Linear_Input_Source": 0xFFFFFFFF})
    pressure = ChannelObject("Bench", "pressure", np.array([1.25, 1.5, 1.75], dtype=np.float64),
                             properties={"wf_increment": 0.01, "unit_string": "bar"})
    untimed = ChannelObject("Bench", "untimed", np.array([1.0, 2.0]), properties={})
    label = ChannelObject("Bench", "label", np.array(["a", "b"]), properties={"wf_increment": 1.0})
    with TdmsWriter(str(path)) as w:
        w.write_segment([accel, pressure, untimed, label])
    return raw


class Tdms(unittest.TestCase):
    def test_scaled_values_timing_units_and_refusals(self):
        f = Path(tempfile.mkdtemp()) / "bench.tdms"
        raw = write_tdms(f)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            recs = {r.channel: r for r in mi.recordings(f, opts())}
        self.assertEqual(sorted(recs), ["accel", "pressure"])
        a = recs["accel"]
        self.assertEqual(a.samples.tolist(), (raw.astype(np.float64) * 0.001 + 0.5).tolist())
        self.assertEqual((a.rate, a.unit, a.metadata["values"]), (20000.0, "g", "scaled"))
        self.assertEqual(a.metadata["start"], "2024-05-06T07:08:09.500000Z")
        self.assertEqual(a.metadata["scaling"]["NI_Scale[0]_Linear_Slope"], 0.001)
        self.assertEqual(a.recording_id, "bench.Bench.accel")
        p = recs["pressure"]
        self.assertEqual((p.samples.tolist(), p.rate, p.unit), ([1.25, 1.5, 1.75], 100.0, "bar"))
        self.assertNotIn("start", p.metadata)
        refused = json.loads(err.getvalue())["tdms_channels_not_imported"]
        self.assertTrue(any("untimed" in r and "wf_increment" in r for r in refused))
        self.assertTrue(any("label" in r and "not numeric" in r for r in refused))

    def test_raw_values_on_request(self):
        f = Path(tempfile.mkdtemp()) / "bench.tdms"
        raw = write_tdms(f)
        with contextlib.redirect_stderr(io.StringIO()):
            a = {r.channel: r for r in mi.recordings(f, opts(tdms_values="raw"))}["accel"]
        self.assertEqual(a.samples.tolist(), raw.astype(np.float64).tolist())
        self.assertEqual(a.metadata["values"], "raw")

    def test_no_waveform_channel_is_an_error(self):
        from nptdms import TdmsWriter, ChannelObject
        f = Path(tempfile.mkdtemp()) / "none.tdms"
        with TdmsWriter(str(f)) as w:
            w.write_segment([ChannelObject("G", "t", np.array([1.0, 2.0]))])
        with self.assertRaisesRegex(ValueError, "no importable waveform channel"):
            mi.recordings(f, opts())

if __name__ == "__main__":
    unittest.main()
