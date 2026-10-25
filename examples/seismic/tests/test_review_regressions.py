"""Regressions from the PR #26 review: version identity, progress-only flushes and
detector lifecycle across operator stop/resume. Synthetic signals only."""
import unittest

import numpy as np
import psycopg

from helpers import (DETECTOR, META, RATE, REP, T0, TempDatabase, chunk, require_database,
                     signal, split_chunks)
from morlet_seismic import investigate
from morlet_seismic.detect import Detector
from morlet_seismic.ingest import Ingestor, init_db, run
from morlet_seismic.sources import ReplayControl

AVAILABLE = require_database()


class ListSource:
    """Minimal source with the replay contract: control, and exhausted on natural end."""

    mode = "replay"

    def __init__(self, chunks, stop_after=None):
        self.items, self.stop_after = list(chunks), stop_after
        self.control = ReplayControl()
        self.exhausted = False

    def chunks(self):
        for count, item in enumerate(self.items, 1):
            if not self.control.wait():
                return
            yield item
            if self.stop_after is not None and count >= self.stop_after:
                self.control.stop()  # Operator presses Stop.
        self.exhausted = not self.control.stopped


@unittest.skipUnless(AVAILABLE, "local MorletDB server not reachable")
class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase()
        self.addCleanup(self.db.drop)
        self.conn = psycopg.connect(self.db.dsn)
        self.addCleanup(self.conn.close)
        init_db(self.conn)

    def ingestor(self, dataset="test", **kw):
        return Ingestor(self.conn, dataset, REP, META, batch_chunks=4, **kw)

    def stored(self, recording_id, end):
        client = investigate.client(self.conn)
        return np.asarray(client.read_interval(recording_id, 0, end)["samples"])

    # -- 0. concurrent setup (found while rerunning the evaluation) -----------------
    def test_parallel_init_on_a_fresh_database_creates_one_collection(self):
        import threading
        fresh = TempDatabase()
        self.addCleanup(fresh.drop)
        errors, barrier = [], threading.Barrier(4)

        def worker():
            try:
                with psycopg.connect(fresh.dsn) as conn:
                    barrier.wait()
                    init_db(conn)
            except Exception as exc:  # noqa: BLE001 - collected for the assertion
                errors.append(repr(exc))
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        with psycopg.connect(fresh.dsn) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM pg_class WHERE relname IN "
                                          "('seismic_waveforms','seismic_waveforms_chunks')").fetchone()[0], 2)

    # -- 1. version identity ---------------------------------------------------
    def test_conflict_at_exact_recording_start_creates_distinct_version(self):
        x = signal(6000)
        y = x.copy()
        y[:50] += 7  # Same first timestamp, different samples.
        z = x.copy()
        z[:50] -= 7
        ingestor = self.ingestor()
        self.assertEqual(ingestor.handle(chunk(0, x[:3000])), "no_earlier_segment")
        self.assertEqual(ingestor.handle(chunk(0, y[:3000])), "overlap_conflict")
        self.assertEqual(ingestor.handle(chunk(0, z[:3000])), "overlap_conflict")
        ingestor.flush()
        segs = investigate.segments(self.conn, "test")
        self.assertEqual(len({s["recording_id"] for s in segs}), 3)
        self.assertEqual({s["start_ns"] for s in segs}, {T0})
        found = sorted(tuple(self.stored(s["recording_id"], 3000)[:50]) for s in segs)
        self.assertEqual(found, sorted(tuple(v[:50].astype(float)) for v in (x, y, z)))

    def test_conflict_partway_and_retransmission_of_both_versions_after_restart(self):
        x = signal(6000)
        changed = x[2900:5000].copy()
        changed[:100] += 1
        first = self.ingestor()
        first.handle(chunk(0, x[:3000]))
        self.assertEqual(first.handle(chunk(2900, changed)), "overlap_conflict")
        first.flush()
        before = {s["recording_id"]: s["committed"] for s in investigate.segments(self.conn, "test")}
        for attempt in range(2):  # Retransmit both versions, in both orders, after restarts.
            restarted = self.ingestor()
            order = [chunk(0, x[:3000]), chunk(2900, changed)]
            for c in (order if attempt == 0 else order[::-1]):
                self.assertEqual(restarted.handle(c), "duplicate")
            restarted.flush()
            self.assertEqual(restarted.stats["segments_opened"], 0)
        after = {s["recording_id"]: s["committed"] for s in investigate.segments(self.conn, "test")}
        self.assertEqual(before, after)
        ids = sorted(after, key=lambda r: after[r], reverse=True)
        np.testing.assert_array_equal(self.stored(ids[0], 3000), x[:3000].astype(float))
        np.testing.assert_array_equal(self.stored(ids[1], 2100), changed.astype(float))

    def test_identical_retransmission_at_exact_start_after_restart_is_not_copied(self):
        x = signal(4000)
        y = x.copy()
        y[:10] += 3
        first = self.ingestor()
        first.handle(chunk(0, x[:3000]))
        first.handle(chunk(0, y[:3000]))
        first.flush()
        restarted = self.ingestor()
        self.assertEqual(restarted.handle(chunk(0, y[:3000])), "duplicate")
        self.assertEqual(restarted.handle(chunk(0, x[:3000])), "duplicate")
        restarted.flush()
        self.assertEqual(len(investigate.segments(self.conn, "test")), 2)

    # -- 2. progress-only flushes ----------------------------------------------
    def test_all_duplicate_replay_persists_progress_once_per_verification(self):
        x = signal(9000)
        chunks = split_chunks(x, 3000)
        first = self.ingestor()
        for c in chunks:
            first.handle(c)
        first.flush()
        other = psycopg.connect(self.db.dsn)
        self.addCleanup(other.close)
        query = "SELECT committed_samples,duplicate_samples FROM seismic.progress WHERE stream='SY.TEST..HHZ'"
        self.assertEqual(other.execute(query).fetchone(), (9000, 0))
        other.rollback()
        replay = self.ingestor()  # Restart: everything is already committed.
        for c in chunks:
            self.assertEqual(replay.handle(c), "duplicate")
        replay.flush()
        replay.flush()  # A second flush must not apply counters again.
        self.assertEqual(other.execute(query).fetchone(), (9000, 9000))
        other.rollback()
        again = self.ingestor()
        for c in chunks:
            again.handle(c)
        again.flush()
        # Samples stay idempotent; duplicate_samples counts verification work per replay.
        self.assertEqual(other.execute(query).fetchone(), (9000, 18000))
        status = investigate.client(self.conn).stream_status(investigate.segments(self.conn, "test")[0]["recording_id"])
        self.assertEqual(status["received_samples"], 9000)

    # -- 3. detector lifecycle --------------------------------------------------
    def pipeline(self, dataset):
        detector = Detector(self.conn, investigate.client(self.conn), DETECTOR, dataset)
        return self.ingestor(dataset, on_commit=detector.on_commit, on_open=detector.on_open), detector

    def onsets(self, dataset):
        return [(t["on_ns"], t["off_ns"]) for t in investigate.triggers(self.conn, dataset, "stalta-test")]

    def test_operator_stop_then_resume_matches_uninterrupted_detection(self):
        # Bursts before the stop and shortly after it (same 600 s detector block).
        x = signal(150_000, bursts=(30_000, 64_000, 69_000, 130_000))
        chunks = split_chunks(x, 3000)
        ingestor, detector = self.pipeline("uninterrupted")
        run(ListSource(chunks), ingestor, detector=detector)
        expected = self.onsets("uninterrupted")
        self.assertGreaterEqual(len(expected), 4)

        ingestor, detector = self.pipeline("stopped")
        source = ListSource(chunks, stop_after=21)  # Stop at sample 63,000.
        run(source, ingestor, detector=detector)
        self.assertFalse(source.exhausted)
        closed = self.conn.execute(
            "SELECT bool_or(d.closed) FROM seismic.detector_progress d JOIN seismic.segments g USING(recording_id)"
            " WHERE g.dataset='stopped'").fetchone()[0]
        self.assertFalse(closed, "an operator stop must not finalize open recordings")
        ingestor, detector = self.pipeline("stopped")  # Restart and resume from the beginning.
        run(ListSource(chunks), ingestor, detector=detector)
        self.assertEqual(self.onsets("stopped"), expected)
        after_stop = T0 + round(64_000 * 1e9 / RATE)
        self.assertTrue(any(abs(on - after_stop) < 0.5e9 for on, _ in self.onsets("stopped")))

    def test_finalized_recording_that_grows_reanalyzes_its_trailing_blocks(self):
        x = signal(150_000, bursts=(30_000, 64_000, 69_000, 130_000))
        chunks = split_chunks(x, 3000)
        ingestor, detector = self.pipeline("uninterrupted")
        run(ListSource(chunks), ingestor, detector=detector)
        expected = self.onsets("uninterrupted")
        # Natural exhaustion of a shorter input finalizes the recording at 63,000 samples,
        # with a burst inside the truncated trailing block; later input extends it.
        ingestor, detector = self.pipeline("grown")
        run(ListSource(chunks[:21]), ingestor, detector=detector)
        self.assertTrue(self.conn.execute(
            "SELECT bool_and(d.closed) FROM seismic.detector_progress d JOIN seismic.segments g USING(recording_id)"
            " WHERE g.dataset='grown'").fetchone()[0])
        ingestor, detector = self.pipeline("grown")
        run(ListSource(chunks), ingestor, detector=detector)
        self.assertEqual(self.onsets("grown"), expected)
        reopened = self.conn.execute("SELECT count(*) FROM seismic.events WHERE kind='detector_reopened'").fetchone()[0]
        self.assertEqual(reopened, 1)


if __name__ == "__main__":
    unittest.main()
