"""Offline contracts: replay adapter, leakage rules, association and live opt-in."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from helpers import RATE, REP, T0, signal
from morlet_seismic import catalog, dataset, investigate
from morlet_seismic.sources import ReplayControl, ReplaySource, SeedLinkSource


def write_mseed(path, stream, start_ns, data):
    from obspy import Stream, Trace, UTCDateTime
    net, sta, loc, cha = stream.split(".")
    trace = Trace(np.asarray(data, dtype=np.int32))
    trace.stats.update({"network": net, "station": sta, "location": loc, "channel": cha,
                        "sampling_rate": RATE, "starttime": UTCDateTime(ns=start_ns)})
    path.parent.mkdir(parents=True, exist_ok=True)
    Stream([trace]).write(str(path), format="MSEED", encoding="STEIM2", reclen=512)


def synthetic_manifest(base):
    """Two streams; stream A has a 2 s gap and a day-boundary-like duplicate."""
    a, b = "SY.AAA..HHZ", "SY.BBB..HHZ"
    x = signal(30_000)
    files = [("raw/a1.mseed", a, T0 - 5 * 10**9, x[:12_000]),          # starts before period
             ("raw/a2.mseed", a, T0 + 100 * 10**9, x[10_500:20_000]),   # overlaps a1 by 15 s
             ("raw/a3.mseed", a, T0 + 197 * 10**9, x[20_200:]),         # after a 2 s gap
             ("raw/b1.mseed", b, T0, x[:25_000])]
    waveforms = []
    for path, stream, start, data in files:
        write_mseed(base / path, stream, start, data)
        waveforms.append({"stream": stream, "period": "p", "path": path,
                          "request_start": f"{start:020d}"})
    definition = {"periods": [{"name": "p", "split": "reference",
                               "start": "2024-03-04T00:00:00Z", "end": "2024-03-04T00:04:00Z"}],
                  "streams": []}
    return {"definition": definition, "waveforms": sorted(waveforms, key=lambda w: w["path"])}


class ReplayAdapter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manifest = synthetic_manifest(self.base)

    def test_chunks_are_time_ordered_trimmed_and_never_cross_traces(self):
        chunks = list(ReplaySource(self.manifest, self.base, chunk_samples=4000).chunks())
        starts = [c.start_ns for c in chunks]
        self.assertEqual(starts, sorted(starts))
        a = [c for c in chunks if c.stream == "SY.AAA..HHZ"]
        self.assertEqual(a[0].start_ns, T0)  # Samples before the period are cut by index.
        self.assertTrue(all(c.end_ns <= T0 + 240 * 10**9 for c in chunks))
        self.assertTrue(all(len(c.samples) <= 4000 for c in chunks))
        # The a1/a2 overlap is replayed unchanged, for ingestion to verify.
        self.assertTrue(any(c.start_ns == T0 + 100 * 10**9 for c in a))
        x = signal(30_000)
        first = next(c for c in a if c.start_ns == T0 + 100 * 10**9)
        np.testing.assert_array_equal(first.samples, x[10_500:10_500 + len(first.samples)])
        self.assertEqual({c.quality for c in chunks}, {"D"})

    def test_pause_resume_and_paced_speed(self):
        control = ReplayControl(speed=400.0)  # 240 s of samples in about 0.6 s
        source = ReplaySource(self.manifest, self.base, chunk_samples=3000, control=control)
        emitted = []
        worker = threading.Thread(target=lambda: emitted.extend(source.chunks()))
        started = time.monotonic()
        worker.start()
        time.sleep(0.1)
        control.pause()
        paused_count = len(emitted)
        time.sleep(0.4)
        self.assertLessEqual(len(emitted) - paused_count, 1)
        control.resume()
        worker.join(10)
        self.assertGreater(time.monotonic() - started, 0.5)
        self.assertEqual(len(emitted), len(list(ReplaySource(self.manifest, self.base,
                                                             chunk_samples=3000).chunks())))

    def test_live_input_requires_explicit_opt_in(self):
        with self.assertRaises(PermissionError):
            SeedLinkSource("example.invalid:18000", ["SY.AAA..HHZ"])


class SplitsAndAssociation(unittest.TestCase):
    def test_definition_rejects_overlapping_periods(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"periods": [{"name": "a", "split": "reference", "start": "2024-01-01T00:00:00Z",
                                    "end": "2024-01-03T00:00:00Z"},
                                   {"name": "b", "split": "heldout", "start": "2024-01-02T00:00:00Z",
                                    "end": "2024-01-04T00:00:00Z"}]}, handle)
        with self.assertRaisesRegex(ValueError, "overlap"):
            dataset.load_definition(handle.name)
        Path(handle.name).unlink()

    def test_labels_follow_whole_periods_and_reject_unknown_time(self):
        definition = {"periods": [{"name": "r", "split": "reference", "start": "2024-03-04T00:00:00Z",
                                   "end": "2024-03-11T00:00:00Z"},
                                  {"name": "h", "split": "heldout", "start": "2024-05-01T00:00:00Z",
                                   "end": "2024-05-06T12:00:00Z"}]}
        label = investigate.labels_for(definition)
        self.assertEqual(label("s", T0)["split"], "reference")
        self.assertEqual(label("s", 1_714_521_600 * 10**9)["split"], "heldout")
        with self.assertRaises(ValueError):
            label("s", 1_712_000_000 * 10**9)

    def test_gallery_uses_only_reference_detector_windows_and_time_grid(self):
        segs = [{"recording_id": "ref", "split": "reference", "rate": RATE, "committed": 100_000},
                {"recording_id": "held", "split": "heldout", "rate": RATE, "committed": 100_000}]
        trigs = [{"recording_id": "ref", "on_sample": 50_000}, {"recording_id": "held", "on_sample": 50_000},
                 {"recording_id": "ref", "on_sample": 100}]  # too early to fit a window
        windows = investigate.gallery_windows(segs, trigs, REP, 2.0, background_every_s=300)
        self.assertTrue(all(r == "ref" for r, _, _ in windows))
        self.assertIn(("ref", 49_800, "trigger"), windows)
        self.assertEqual(len([w for w in windows if w[2] == "trigger"]), 1)
        backgrounds = [s for _, s, k in windows if k == "background"]
        self.assertTrue(all(s % REP["stride_samples"] == 0 for s in backgrounds))
        self.assertTrue(all(s + 2048 <= 100_000 for _, s, _ in windows))

    def test_gallery_cap_keeps_triggers_and_thins_background_evenly(self):
        windows = [("r", i, "trigger") for i in range(100)] + [("r", 10_000 + i, "background") for i in range(1000)]
        kept, report = investigate.cap_gallery(windows, limit=300)
        self.assertEqual((report["kept"], report["trigger_windows"], report["background_windows"]), (300, 100, 200))
        backs = [s for _, s, k in kept if k == "background"]
        self.assertEqual((backs[0], backs[-1]), (10_000, 10_999))  # spread, not truncated
        kept, report = investigate.cap_gallery(windows[:100], limit=40)
        self.assertEqual((report["kept"], report["trigger_windows_dropped"]), (40, 60))

    def test_association_uses_predicted_arrival_windows_not_origin_times(self):
        arrival = {"event_id": "ev1", "stream": "S", "p_ns": 1_000 * 10**9, "s_ns": 1_013 * 10**9,
                   "window_ns": (995 * 10**9, 1_033 * 10**9)}
        trigs = [{"recording_id": "r", "on_sample": 1, "stream": "S", "on_ns": 1_001 * 10**9},
                 {"recording_id": "r", "on_sample": 2, "stream": "S", "on_ns": 1_014 * 10**9},
                 {"recording_id": "r", "on_sample": 3, "stream": "S", "on_ns": 990 * 10**9},
                 {"recording_id": "r", "on_sample": 4, "stream": "OTHER", "on_ns": 1_001 * 10**9}]
        result = catalog.associate(trigs, [arrival])
        self.assertAlmostEqual(result[("r", 1)][0]["p_residual_s"], 1.0)
        self.assertEqual(len(result[("r", 2)]), 1)          # S-wave trigger, same event
        self.assertEqual(result[("r", 3)], [])               # before P window: unmatched
        self.assertEqual(result[("r", 4)], [])               # other station never associates
        segs = [{"stream": "S", "start_ns": 0, "end_ns": 2_000 * 10**9}]
        self.assertTrue(catalog.monitored(segs, "S", 1_000 * 10**9, 120))
        self.assertFalse(catalog.monitored(segs, "S", 60 * 10**9, 120))  # detector warm-up
        self.assertFalse(catalog.monitored(segs, "S", 3_000 * 10**9, 120))  # no data


if __name__ == "__main__":
    unittest.main()
