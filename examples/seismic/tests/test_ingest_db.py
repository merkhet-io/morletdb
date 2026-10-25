"""Ingestion contracts against a real MorletDB server (synthetic signals only)."""
import unittest

import numpy as np
import psycopg

from helpers import (DETECTOR, META, RATE, REP, T0, TempDatabase, chunk, require_database,
                     require_features, signal, split_chunks)
from morlet_seismic import investigate, retrieval
from morlet_seismic.detect import Detector
from morlet_seismic.ingest import Ingestor, init_db

AVAILABLE = require_database()
FEATURES = require_features()


@unittest.skipUnless(AVAILABLE, "local MorletDB server (examples/seismic/compose.yaml) not reachable")
class IngestContracts(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase()
        self.addCleanup(self.db.drop)
        self.conn = psycopg.connect(self.db.dsn)
        self.addCleanup(self.conn.close)
        init_db(self.conn)

    def ingestor(self, dataset="test", **kw):
        return Ingestor(self.conn, dataset, REP, META, batch_chunks=4, **kw)

    def segs(self, dataset="test"):
        return investigate.segments(self.conn, dataset)

    def events(self, kind):
        return self.conn.execute("SELECT sample_ns,recording_id,detail FROM seismic.events WHERE kind=%s"
                                 " ORDER BY id", (kind,)).fetchall()

    def stored(self, segment):
        client = investigate.client(self.conn)
        out = []
        for lo in range(0, segment["committed"], 32768):
            out.extend(client.read_interval(segment["recording_id"], lo,
                                            min(segment["committed"], lo + 32768))["samples"])
        return np.asarray(out)

    def test_timestamps_map_to_sample_coordinates_and_raw_values_survive(self):
        x = signal(5000)
        ingestor = self.ingestor()
        for c in split_chunks(x, 700):
            ingestor.handle(c)
        ingestor.flush()
        [segment] = self.segs()
        self.assertEqual(segment["start_ns"], T0)
        self.assertEqual(segment["committed"], 5000)
        self.assertEqual(segment["end_ns"], T0 + 50 * 10**9)
        self.assertEqual(segment["metadata"]["segment_start_utc"], "2024-03-04T00:00:00.000000000Z")
        np.testing.assert_array_equal(self.stored(segment), x.astype(float))
        status = investigate.client(self.conn).stream_status(segment["recording_id"])
        self.assertEqual(status["extractor_version"], "morlet-energy-lowrate-v1")
        self.assertEqual(status["indexed_segments"], 1 + (5000 - 2048) // 512)
        progress = self.conn.execute("SELECT last_committed_end_ns,committed_samples FROM seismic.progress").fetchone()
        self.assertEqual(progress, (T0 + 50 * 10**9, 5000))

    def test_gaps_split_recordings_and_are_never_filled(self):
        x = signal(9000)
        ingestor = self.ingestor()
        for c in split_chunks(x[:3000], 1000):
            ingestor.handle(c)
        # 1.5 s of missing time, then data resumes on its own timestamps.
        for c in split_chunks(x[3150:], 1000, start=3150):
            ingestor.handle(c)
        ingestor.flush()
        first, second = self.segs()
        self.assertEqual((first["committed"], second["committed"]), (3000, 5850))
        self.assertEqual(second["start_ns"], T0 + 31_500_000_000)
        self.assertEqual(second["opened_reason"], "gap")
        np.testing.assert_array_equal(self.stored(second), x[3150:].astype(float))
        [(at, previous, detail)] = self.events("gap")
        self.assertEqual(previous, first["recording_id"])
        self.assertAlmostEqual(detail["gap_s"], 1.5)

    def test_duplicates_and_partial_overlaps_append_only_new_samples(self):
        x = signal(6000)
        ingestor = self.ingestor()
        self.assertEqual(ingestor.handle(chunk(0, x[:3000])), "no_earlier_segment")
        self.assertEqual(ingestor.handle(chunk(0, x[:3000])), "duplicate")
        self.assertEqual(ingestor.handle(chunk(2500, x[2500:4500])), "partial_duplicate")
        self.assertEqual(ingestor.handle(chunk(4500, x[4500:])), "append")
        ingestor.flush()
        [segment] = self.segs()
        self.assertEqual(segment["committed"], 6000)
        np.testing.assert_array_equal(self.stored(segment), x.astype(float))
        self.assertEqual(ingestor.stats["duplicate_samples"], 3000 + 500)

    def test_conflicting_overlap_preserves_both_versions(self):
        x = signal(6000)
        ingestor = self.ingestor()
        ingestor.handle(chunk(0, x[:3000]))
        changed = x[2900:5000].copy()
        changed[:100] += 1  # Timing jump: same timestamps, different samples.
        self.assertEqual(ingestor.handle(chunk(2900, changed)), "overlap_conflict")
        ingestor.flush()
        first, second = self.segs()
        self.assertEqual((first["committed"], second["committed"]), (3000, 2100))
        np.testing.assert_array_equal(self.stored(first), x[:3000].astype(float))
        np.testing.assert_array_equal(self.stored(second), changed.astype(float))
        self.assertEqual(len(self.events("overlap_conflict")), 1)

    def test_subsample_jitter_is_absorbed_and_logged_but_larger_offsets_split(self):
        x = signal(4000)
        ingestor = self.ingestor()
        ingestor.handle(chunk(0, x[:2000]))
        self.assertEqual(ingestor.handle(chunk(2000, x[2000:3000], shift_ns=3_000_000)), "append")
        self.assertEqual(ingestor.handle(chunk(3000, x[3000:], shift_ns=9_000_000)), "gap")
        ingestor.flush()
        self.assertEqual([s["committed"] for s in self.segs()], [3000, 1000])
        self.assertEqual(len(self.events("absorbed_jitter")), 1)

    def test_invalid_chunks_are_rejected_without_advancing(self):
        ingestor = self.ingestor()
        bad_rate = chunk(0, signal(100))
        object.__setattr__(bad_rate, "sample_rate", 200.0)  # v1 domain, not low-rate
        self.assertEqual(ingestor.handle(bad_rate), "rejected")
        self.assertEqual(ingestor.handle(chunk(0, np.full(10, 2e9))), "rejected")
        ingestor.flush()
        self.assertEqual(self.segs(), [])
        self.assertEqual(len(self.events("rejected_chunk")), 2)

    def test_restart_and_full_replay_do_not_duplicate_and_match_single_run(self):
        x = signal(120_000, bursts=(30_000, 70_000, 100_000))
        chunks = split_chunks(x[:60_000], 3000) + split_chunks(x[60_150:], 3000, start=60_150)

        def pipeline(dataset):
            detector = Detector(self.conn, investigate.client(self.conn), DETECTOR, dataset)
            ingestor = self.ingestor(dataset, on_commit=detector.on_commit, on_open=detector.on_open)
            return ingestor, detector

        ingestor, detector = pipeline("single")
        for c in chunks:
            ingestor.handle(c)
        ingestor.flush()
        detector.close_all()
        # Interrupted run: stop after 13 chunks, discard in-memory state, restart.
        ingestor, detector = pipeline("restarted")
        for c in chunks[:13]:
            ingestor.handle(c)
        ingestor.flush()
        ingestor, detector = pipeline("restarted")
        for c in chunks:  # Full replay from the beginning.
            ingestor.handle(c)
        ingestor.flush()
        detector.close_all()
        self.assertEqual(ingestor.stats["duplicate_samples"], 13 * 3000)
        self.assertEqual(ingestor.stats["segments_opened"], 1)  # Only the post-gap segment.
        single, restarted = self.segs("single"), self.segs("restarted")
        self.assertEqual([s["committed"] for s in single], [s["committed"] for s in restarted])
        for a, b in zip(single, restarted):
            np.testing.assert_array_equal(self.stored(a), self.stored(b))
        feature_sql = ("SELECT array_agg(features ORDER BY start_sample) FROM public.seismic_waveforms_segments"
                       " WHERE recording_id=%s")
        for a, b in zip(single, restarted):
            self.assertEqual(self.conn.execute(feature_sql, (a["recording_id"],)).fetchone(),
                             self.conn.execute(feature_sql, (b["recording_id"],)).fetchone())
        ta = [(t["on_ns"], t["off_ns"]) for t in investigate.triggers(self.conn, "single", "stalta-test")]
        tb = [(t["on_ns"], t["off_ns"]) for t in investigate.triggers(self.conn, "restarted", "stalta-test")]
        self.assertEqual(ta, tb)
        onsets = sorted(t[0] for t in ta)
        near = lambda burst: any(abs(o - (T0 + round(burst * 1e9 / RATE))) < 0.5e9 for o in onsets)  # noqa: E731
        self.assertTrue(near(30_000) and near(100_000))
        # 98.5 s after the post-gap segment start: inside the 120 s LTA warm-up,
        # so it is unmonitored rather than searched with an unsettled baseline.
        self.assertFalse(near(70_000))

    def test_finishing_one_stream_does_not_close_another(self):
        from morlet_seismic.ingest import run

        class Source:
            mode = "replay"

            def __init__(self, chunks):
                self.items = chunks
                self.exhausted = False

            def chunks(self):
                yield from self.items
                self.exhausted = True  # This stream's input ended naturally.

        x = signal(90_000, bursts=(80_000,))
        detector = Detector(self.conn, investigate.client(self.conn), DETECTOR, "test")
        other = self.ingestor(on_commit=detector.on_commit, on_open=detector.on_open)
        for c in split_chunks(x[:60_000], 3000):  # SY.TEST..HHZ still in progress
            other.handle(c)
        other.flush()
        b = [chunk(i, x[i:i + 3000], stream="SY.OTHER..HHZ") for i in range(0, 30_000, 3000)]
        meta = {**META, "SY.OTHER..HHZ": META["SY.TEST..HHZ"]}
        finishing = Ingestor(self.conn, "test", REP, meta, batch_chunks=4,
                             on_commit=detector.on_commit, on_open=detector.on_open)
        run(Source(b), finishing, detector=detector)
        closed = dict(self.conn.execute(
            "SELECT g.stream,d.closed FROM seismic.detector_progress d JOIN seismic.segments g USING(recording_id)").fetchall())
        self.assertTrue(closed["SY.OTHER..HHZ"])
        self.assertFalse(closed.get("SY.TEST..HHZ", False))  # still open for its own ingestor
        for c in split_chunks(x[60_000:], 3000, start=60_000):
            other.handle(c)
        other.flush()
        detector.close_all(streams=["SY.TEST..HHZ"])
        onsets = [t["on_ns"] for t in investigate.triggers(self.conn, "test", "stalta-test", "SY.TEST..HHZ")]
        self.assertTrue(any(abs(o - (T0 + 800 * 10**9)) < 0.5e9 for o in onsets))

    def test_retrieval_budgets_compatibility_and_empty_results(self):
        x = signal(40_000, bursts=(5_000, 25_000))
        labels = {"split": "reference"}
        ingestor = self.ingestor(split_of=lambda s, t: labels)
        for c in split_chunks(x, 3000):
            ingestor.handle(c)
        ingestor.flush()
        [segment] = self.segs()
        client = investigate.client(self.conn)
        meta = retrieval.stream_filter("test", "SY.TEST..HHZ")
        result = retrieval.pooled_pg(client, segment["recording_id"], 4_800, 4_800 + 2048, meta,
                                     top_k=3, max_candidates=10)
        self.assertEqual((result["status"], result["matches"]), ("budget_exceeded", []))
        result = retrieval.pooled_pg(client, segment["recording_id"], 4_800, 4_800 + 2048, meta, top_k=3)
        self.assertEqual(result["eligible"], 1 + (40_000 - 2048) // 512)
        # The other burst is found; overlapping source windows are excluded.
        self.assertTrue(all(m["end"] <= 4_800 or m["start"] >= 6_848 for m in result["matches"]))
        # Pooled features tolerate position: the best match contains the other burst.
        self.assertTrue(result["matches"][0]["start"] <= 25_000 < result["matches"][0]["end"])
        # Another station's filter has no compatible recordings: empty stays empty.
        other = retrieval.stream_filter("test", "SY.OTHER..HHZ")
        self.assertEqual(retrieval.pooled_pg(client, segment["recording_id"], 4_800, 6_848, other)["matches"], [])
        # A held-out filter excludes reference recordings entirely.
        held = retrieval.stream_filter("test", "SY.TEST..HHZ", split="heldout")
        self.assertEqual(retrieval.pooled_pg(client, segment["recording_id"], 4_800, 6_848, held)["eligible"], 0)

    @unittest.skipUnless(FEATURES, "build the Rust features adapter first")
    def test_temporal_gallery_oracle_approximation_and_posting_budget(self):
        x = signal(40_000, bursts=(5_000, 25_000))
        ingestor = self.ingestor(split_of=lambda s, t: {"split": "reference"})
        for c in split_chunks(x, 3000):
            ingestor.handle(c)
        ingestor.flush()
        [segment] = self.segs()
        client = investigate.client(self.conn)
        spec = retrieval.window_spec(REP, "HHZ", "counts", RATE)
        windows = [(segment["recording_id"], s, "background") for s in range(0, 38_000, 1024)]
        gallery = retrieval.build_gallery(client, spec, windows)
        query = retrieval.query_window(client, spec, segment["recording_id"], 4_800)
        exact = retrieval.temporal(gallery, query, top_k=3)
        approx = retrieval.temporal(gallery, query, approximate=True, top_k=3, candidate_budget=4)
        self.assertEqual(exact["scored"], exact["eligible"])
        self.assertLessEqual(approx["scored"], 4)
        self.assertIsNotNone(retrieval.recall(approx["matches"], exact["matches"]))
        tiny = retrieval.temporal(gallery, query, approximate=True, posting_budget=1)
        self.assertEqual((tiny["status"], tiny["matches"]), ("budget_exceeded", []))
        pooled = retrieval.pooled_gallery(gallery, query, top_k=3)
        self.assertTrue(all(not (m["start"] < 6_848 and 4_800 < m["end"]) for m in pooled["matches"]))
        # A gallery made only of the query's own source windows yields no matches.
        own = retrieval.build_gallery(client, spec, [(segment["recording_id"], 4_800, "trigger")])
        self.assertEqual(retrieval.temporal(own, query)["matches"], [])
        self.assertEqual(retrieval.pooled_gallery(own, query)["matches"], [])
        with self.assertRaises(ValueError):
            retrieval.build_gallery(client, spec, windows * 200)


if __name__ == "__main__":
    unittest.main()
