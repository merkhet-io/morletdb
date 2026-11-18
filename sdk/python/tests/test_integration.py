"""The SDK contract against a real MorletDB (PostgreSQL with pg_morlet).

    MORLET_SDK_TEST_DSN=postgresql://... python -m unittest discover -s sdk/python/tests

Without MORLET_SDK_TEST_DSN the tests are skipped; with MORLET_SDK_REQUIRE_DB=1 a missing database fails.
Every test works in its own schema, dropped afterwards.
"""
import os
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import psycopg

import morletdb
from morletdb import ConflictError, InvalidInputError, NotFoundError

DSN = os.environ.get("MORLET_SDK_TEST_DSN", "")
if not DSN and os.environ.get("MORLET_SDK_REQUIRE_DB") == "1":
    raise RuntimeError("MORLET_SDK_REQUIRE_DB=1 but MORLET_SDK_TEST_DSN is not set")

RAW_FUNCTIONS = ("public.morlet_append_chunk_raw(regclass,text,bigint,bytea)",
                 "public.morlet_read_interval_raw(regclass,text,bigint,bigint)")


def events(n, positions, rate, seed=0, step=1 / 512):
    """Noise with decaying 900 Hz bursts at `positions`, on a value grid of `step` (like ADC counts)."""
    rng = np.random.default_rng(seed)
    x = 0.05 * rng.standard_normal(n)
    t = np.arange(4096) / rate
    burst = np.exp(-t * 40) * np.sin(2 * np.pi * 900 * t)
    for p in positions:
        x[p:p + 4096] += burst[: max(0, min(4096, n - p))]
    return np.round(x / step) * step


@unittest.skipUnless(DSN, "set MORLET_SDK_TEST_DSN to run the integration tests")
class Contract(unittest.TestCase):
    def setUp(self):
        self.schema = "sdk_test_" + uuid.uuid4().hex[:10]
        self.conn = psycopg.connect(DSN)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.conn.commit()
        self.db = morletdb.Client(self.conn)
        self.coll = f"{self.schema}.signals"
        self.db.create_collection(self.coll)
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()

    def both_transports(self):
        """Run the block once with the server's raw functions and once with the array fallback."""
        yield "raw"
        for f in RAW_FUNCTIONS:
            self.db._functions[f] = False
        yield "array"
        self.db._functions.clear()

    def test_exact_round_trip_for_supported_types(self):
        rng = np.random.default_rng(1)
        inputs = {
            "f64": np.concatenate([rng.standard_normal(9000) * 1e3, [-0.0, 5e-324, -5e-324, 1e9, -1e9]]),
            "f32": rng.standard_normal(5000).astype(np.float32),
            "i16": rng.integers(-32768, 32767, 7000).astype(np.int16),
            "i32": rng.integers(-10 ** 9, 10 ** 9, 3000).astype(np.int32),
            "grid": np.round(rng.standard_normal(8192) / 0.001) * 0.001,
        }
        for transport in self.both_transports():
            for name, a in inputs.items():
                rid = f"{transport}-{name}"
                rec = self.db.ingest(self.coll, rid, a, sample_rate_hz=1000, channel="x", unit="u")
                self.assertEqual(rec.samples, a.size)
                got = self.db.read(self.coll, rid, 0, a.size).samples
                want = a.astype(np.float64)
                self.assertEqual(got.dtype, np.float64)
                np.testing.assert_array_equal(got.view(np.uint64), want.view(np.uint64), err_msg=rid)
        self.conn.commit()

    def test_interval_boundaries_and_timing(self):
        x = events(20_000, [5000], 8000)
        t0 = datetime(2026, 3, 1, 12, 0, 0, 250000, tzinfo=timezone.utc)
        rec = self.db.ingest(self.coll, "timed", x, sample_rate_hz=8000, channel="accel", unit="g", start_time=t0,
                             metadata={"asset": "pump-7"})
        self.assertEqual(rec.start_ns, 1772366400250000000)
        self.assertEqual(rec.start_time, t0)
        self.assertEqual(rec.metadata["asset"], "pump-7")
        self.assertEqual(rec.metadata["start"], "2026-03-01T12:00:00.250000000Z")
        for transport in self.both_transports():
            for a, b in [(0, 1), (4095, 4097), (4096, 8192), (100, 19_999), (0, 20_000)]:
                iv = self.db.read(self.coll, "timed", a, b)
                self.assertEqual(iv.samples.size, b - a, transport)       # end is exclusive
                np.testing.assert_array_equal(iv.samples, x[a:b])
                self.assertAlmostEqual(iv.time_offsets_s()[0], a / 8000)
            with self.assertRaises(NotFoundError):
                self.db.read(self.coll, "timed", 0, 20_001)
            with self.assertRaises(InvalidInputError):
                self.db.read(self.coll, "timed", 10, 10)
        # 1 s after the start: sample 8000; 1 s + 1 ns is not on a sample, so the next one (ceil)
        self.assertEqual(self.db.samples_for_time(rec, rec.start_ns + 10 ** 9, rec.start_ns + 10 ** 9 + 1),
                         (8000, 8001))
        ns_rec = self.db.ingest(self.coll, "ns", x[:100], sample_rate_hz=64000, channel="c", unit="u",
                                start_time=1_000_000_000_000_015_625)
        self.assertEqual(ns_rec.start_ns, 1_000_000_000_000_015_625)
        self.conn.commit()

    def test_search_results_resolve_to_retrievable_intervals(self):
        rate = 8000
        a = events(60_000, [8192, 30_000, 50_000], rate, seed=2)
        b = events(60_000, [12_000, 40_960], rate, seed=3)
        self.db.ingest(self.coll, "machine-a", a, sample_rate_hz=rate, channel="accel", unit="g",
                       metadata={"line": "1"})
        self.db.ingest(self.coll, "machine-b", b, sample_rate_hz=rate, channel="accel", unit="g",
                       metadata={"line": "2"})
        self.conn.commit()
        res = self.db.search(self.coll, "machine-a", 8192, top_k=5)
        self.assertEqual(res.query.length, 4096)
        self.assertEqual(res.mode, "exact")
        self.assertTrue(res.matches)
        distances = [m.distance for m in res.matches]
        self.assertEqual(distances, sorted(distances))
        sources = {"machine-a": a, "machine-b": b}
        for m in res.matches:
            self.assertEqual(m.ref.length, 4096)
            self.assertFalse(m.ref.recording_id == "machine-a" and m.ref.start_sample < 8192 + 4096
                             and m.ref.end_sample > 8192, "source interval excluded")
            iv = self.db.read_ref(m.ref)
            np.testing.assert_array_equal(iv.samples, sources[m.ref.recording_id][m.ref.start_sample:m.ref.end_sample])
        # the other events rank before background windows
        event_starts = {("machine-a", 30_000), ("machine-a", 50_000), ("machine-b", 12_000), ("machine-b", 40_960)}
        for m in res.matches[:2]:
            self.assertTrue(any(m.ref.recording_id == rid and abs(m.ref.start_sample - p) < 2048
                                for rid, p in event_starts), m)
        only_b = self.db.search(self.coll, "machine-a", 8192, metadata_filter={"line": "2"}, top_k=5)
        self.assertTrue(only_b.matches and all(m.ref.recording_id == "machine-b" for m in only_b.matches))
        with self.assertRaises(InvalidInputError):
            self.db.search(self.coll, "machine-a", 0, 1000)       # not the stored window length

    def test_invalid_inputs(self):
        for bad in [np.zeros((10, 2)), np.array([], float), np.array([1j]), np.array([1.0, float("nan")])]:
            with self.assertRaises(InvalidInputError):
                self.db.ingest(self.coll, "bad", bad, sample_rate_hz=100, channel="x", unit="u")
        with self.assertRaises(InvalidInputError):
            self.db.ingest(self.coll, "bad", np.ones(10), sample_rate_hz=0, channel="x", unit="u")
        with self.assertRaises(InvalidInputError):
            self.db.ingest(self.coll, "bad", np.ones(10), sample_rate_hz=10, channel="x", unit="u",
                           start_time=datetime(2026, 1, 1))
        with self.assertRaises(InvalidInputError):
            self.db.ingest(self.coll, "bad", np.ones(10), sample_rate_hz=10, channel="x", unit="u",
                           start_time=0, metadata={"start": "x"})
        with self.assertRaises(NotFoundError):
            self.db.recording(self.coll, "missing")
        with self.assertRaises(NotFoundError):
            self.db.recordings("public.no_such_collection")

    def test_transactions_and_retries(self):
        x = events(10_000, [1000], 8000)
        self.db.ingest(self.coll, "tx", x, sample_rate_hz=8000, channel="c", unit="u")
        self.conn.rollback()                                   # the caller owns the transaction
        with self.assertRaises(NotFoundError):
            self.db.recording(self.coll, "tx")
        self.conn.rollback()
        self.db.ingest(self.coll, "tx", x, sample_rate_hz=8000, channel="c", unit="u")
        self.conn.commit()
        rec = self.db.ingest(self.coll, "tx", x, sample_rate_hz=8000, channel="c", unit="u")   # identical replay
        self.assertEqual(rec.samples, x.size)
        self.conn.commit()
        y = x.copy()
        y[5000] += 1.0
        with self.assertRaises(ConflictError):
            self.db.ingest(self.coll, "tx", y, sample_rate_hz=8000, channel="c", unit="u")
        self.conn.rollback()
        np.testing.assert_array_equal(self.db.read(self.coll, "tx", 0, x.size).samples, x)
        self.assertEqual(self.db.append(self.coll, "tx", x.size, x[:500]), x.size + 500)
        self.conn.commit()
        with self.assertRaises(InvalidInputError):           # a gap
            self.db.append(self.coll, "tx", x.size + 1000, x[:10])
        self.conn.rollback()

    def test_owned_connection_commits_on_success_and_rolls_back_on_error(self):
        x = events(5000, [100], 8000)
        with morletdb.Client.connect(DSN) as db:
            db.ingest(self.coll, "owned", x, sample_rate_hz=8000, channel="c", unit="u")
        self.assertEqual(self.db.recording(self.coll, "owned").samples, x.size)
        with self.assertRaises(RuntimeError):
            with morletdb.Client.connect(DSN) as db:
                db.ingest(self.coll, "rolled-back", x, sample_rate_hz=8000, channel="c", unit="u")
                raise RuntimeError("stop")
        with self.assertRaises(NotFoundError):
            self.db.recording(self.coll, "rolled-back")

    def test_stats_when_supported(self):
        x = events(9000, [100], 8000)
        self.db.ingest(self.coll, "st", x, sample_rate_hz=8000, channel="c", unit="u")
        try:
            s = self.db.stats(self.coll, "st", 10, 8000)
        except morletdb.UnsupportedError:
            self.skipTest("server without morlet_interval_stats")
        self.assertEqual(s.sample_count, 7990)
        self.assertEqual((s.min, s.max), (x[10:8000].min(), x[10:8000].max()))
        self.assertAlmostEqual(s.mean, x[10:8000].mean(), delta=1e-12 * max(1, abs(x).max()))

    def test_compressed_transfer_when_supported(self):
        """Encoded reads (decoded in the client) return the raw reads' samples bit for bit, on intervals inside
        one chunk, across chunk edges and over the whole recording, and fail the same way on missing data."""
        from morletdb import _native
        if _native.native() is None:
            self.skipTest("native decoder library not available")
        if not self.db._has("public.morlet_read_interval_encoded(regclass,text,bigint,bigint,integer)"):
            self.skipTest("server without morlet_read_interval_encoded")
        rng = np.random.default_rng(3)
        x = np.concatenate([events(70000, [500, 40000], 25600), rng.standard_normal(30000)])
        self.db.ingest(self.coll, "ct", x, sample_rate_hz=25600, channel="c", unit="u")
        old = os.environ.get("MORLETDB_TRANSFER")
        try:
            for a, b in [(0, 100000), (1, 2), (4095, 4097), (1234, 98765), (69000, 71000), (99999, 100000)]:
                got = {}
                for mode in ("raw", "encoded"):
                    os.environ["MORLETDB_TRANSFER"] = mode
                    got[mode] = self.db.read(self.coll, "ct", a, b).samples
                np.testing.assert_array_equal(got["encoded"].view("<u8"), got["raw"].view("<u8"))
                np.testing.assert_array_equal(got["encoded"].view("<u8"), x[a:b].view("<u8"))
                self.assertTrue(got["encoded"].flags.writeable)
            self.conn.execute(f"DELETE FROM {self.coll}_chunks WHERE start_sample=8192")
            os.environ["MORLETDB_TRANSFER"] = "encoded"
            with self.assertRaises(NotFoundError):
                self.db.read(self.coll, "ct", 0, 20000)
        finally:
            self.conn.rollback()
            if old is None:
                os.environ.pop("MORLETDB_TRANSFER", None)
            else:
                os.environ["MORLETDB_TRANSFER"] = old

    def test_compressed_transfer_of_version5_chunks(self):
        """Study B6: chunks written with Grizzbit 2.1 (version 5) come to a client whose native decoder reads version
        5 as chunks, and decode to the raw reads' samples bit for bit."""
        from morletdb import _native
        lib = _native.native()
        if lib is None:
            self.skipTest("native decoder library not available")
        if lib.max_chunk_version < 5:
            self.skipTest(f"native decoder reads chunk versions up to {lib.max_chunk_version}")
        self.conn.execute("SELECT morlet_version()")              # loads the library, which defines the setting
        if self.conn.execute("SELECT current_setting('morlet.chunk_format', true)").fetchone()[0] is None:
            self.skipTest("server without morlet.chunk_format")
        self.conn.execute("SET morlet.chunk_format = grizzbit21")
        rng = np.random.default_rng(7)
        e = rng.integers(-20, 21, 40_000)
        y = np.zeros(40_000)
        for i in range(2, 40_000):
            y[i] = round(1.95 * y[i - 1] - 0.97 * y[i - 2] + e[i])
        old = os.environ.get("MORLETDB_TRANSFER")
        try:
            self.db.ingest(self.coll, "v5", y, sample_rate_hz=1000, channel="c", unit="u")
            versions = {r[0] for r in self.conn.execute(
                f"SELECT get_byte(encoded,0) FROM {self.coll}_chunks WHERE recording_id='v5'").fetchall()}
            self.assertIn(5, versions)
            for a, b in [(0, 40000), (1, 2), (4095, 4097), (1234, 38765)]:
                got = {}
                for mode in ("raw", "encoded"):
                    os.environ["MORLETDB_TRANSFER"] = mode
                    got[mode] = self.db.read(self.coll, "v5", a, b).samples
                np.testing.assert_array_equal(got["encoded"].view("<u8"), got["raw"].view("<u8"))
                np.testing.assert_array_equal(got["encoded"].view("<u8"), y[a:b].view("<u8"))
            frame = self.conn.execute(
                "SELECT morlet_read_interval_encoded(%s::regclass,'v5',0,40000,%s)",
                (self.coll, lib.max_chunk_version)).fetchone()[0]
            self.assertEqual(bytes(frame[:4]), b"MTX1")      # chunks, not samples
        finally:
            self.conn.rollback()
            self.conn.execute("RESET morlet.chunk_format")
            self.conn.commit()
            if old is None:
                os.environ.pop("MORLETDB_TRANSFER", None)
            else:
                os.environ["MORLETDB_TRANSFER"] = old

    def test_latest_when_supported(self):
        x = events(20000, [100, 9000], 8000)
        self.db.ingest(self.coll, "lt", x, sample_rate_hz=8000, channel="c", unit="u",
                       metadata={"latest_test": 1}, start_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
        try:
            rows = self.db.latest(self.coll, 5, {"latest_test": 1})
        except morletdb.UnsupportedError:
            self.skipTest("server without morlet_latest")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r.start_sample, r.end_sample), (19995, 20000))
        self.assertTrue(np.array_equal(r.samples, self.db.read(self.coll, "lt", 19995, 20000).samples))
        self.assertEqual(r.stats, self.db.stats(self.coll, "lt", 19995, 20000))
        self.assertEqual(r.end_time, datetime(2026, 1, 1, 0, 0, 2, 500000, tzinfo=timezone.utc))
        chunk = self.db.latest(self.coll, 0, {"latest_test": 1})[0]
        self.assertIsNone(chunk.samples)
        self.assertEqual(chunk.end_sample, 20000)
        self.assertEqual(chunk.stats, self.db.stats(self.coll, "lt", chunk.start_sample, 20000))

    def test_notifications_when_supported(self):
        try:
            channel = self.db.enable_notifications(self.coll)
        except morletdb.UnsupportedError:
            self.skipTest("server without notifications")
        self.assertEqual(channel, "morlet_signals")
        self.db.ingest(self.coll, "nt", np.zeros(100), sample_rate_hz=100, channel="c", unit="u")
        self.conn.commit()
        with morletdb.Client.connect(DSN, autocommit=True) as listener:
            with self.assertRaises(InvalidInputError):
                next(self.db.notifications(self.coll))       # not autocommit
            self.assertEqual(list(listener.notifications(self.coll, timeout=0.5)), [])  # nothing committed since

            def commit_later():
                with psycopg.connect(DSN) as c:
                    w = morletdb.Client(c)
                    w.append(self.coll, "nt", 100, np.ones(50))
                    c.rollback()                             # rolled back: no notification
                    w.append(self.coll, "nt", 100, np.ones(50))
                    c.commit()
            it = listener.notifications(self.coll, timeout=10, stop_after=1)
            t = threading.Timer(0.5, commit_later)               # LISTEN runs on the first next(), before it fires
            t.start()
            notes = list(it)
            t.join()
        self.assertEqual(len(notes), 1)
        n = notes[0]
        self.assertEqual((n.collection, n.recording_count, n.truncated), (self.coll, 1, False))
        self.assertEqual(n.recordings, (morletdb.NotifiedRange("nt", 100, 150, 100.0, 1.0, 1.5),))
        self.assertTrue(self.db.disable_notifications(self.coll))
        self.assertFalse(self.db.disable_notifications(self.coll))

    def test_overview_and_summaries_when_supported(self):
        x = events(20000, [100, 9000], 8000)
        self.db.ingest(self.coll, "ov", x, sample_rate_hz=8000, channel="c", unit="u")
        try:
            parts = self.db.overview(self.coll, "ov", 0, 20000, buckets=4)
        except morletdb.UnsupportedError:
            self.skipTest("server without morlet_interval_overview")
        has_summaries = self.db._one("SELECT count(summary) FROM " + self.coll + "_chunks")
        self.assertGreater(has_summaries, 0, "create_collection enables summaries on SQL 0.3.3 servers")
        self.assertEqual(sum(p.sample_count for p in parts), 20000)
        self.assertEqual((parts[0].start_sample, parts[-1].end_sample), (0, 20000))
        self.assertEqual(min(p.min for p in parts), x.min())
        self.assertEqual(max(p.max for p in parts), x.max())
        whole = self.db.stats(self.coll, "ov", 0, 20000)
        one = self.db.overview(self.coll, "ov", 0, 20000, buckets=1)[0]
        self.assertEqual((one.sample_count, one.min, one.max, one.mean, one.rms),
                         (whole.sample_count, whole.min, whole.max, whole.mean, whole.rms))

    def test_exclude_the_source_recording_when_supported(self):
        rate = 8000
        cyc = events(60_000, [8192, 24_576, 40_960], rate, seed=4)     # the same event in three cycles
        other = events(60_000, [20_000], rate, seed=5)
        self.db.ingest(self.coll, "cyclic", cyc, sample_rate_hz=rate, channel="accel", unit="g")
        self.db.ingest(self.coll, "other", other, sample_rate_hz=rate, channel="accel", unit="g")
        self.conn.commit()
        try:
            res = self.db.search(self.coll, "cyclic", 8192, top_k=5, exclude="recording")
        except morletdb.UnsupportedError:
            self.skipTest("server without search exclusion (SQL 0.3.11)")
        self.assertTrue(res.matches)
        self.assertTrue(all(m.ref.recording_id == "other" for m in res.matches))
        self.assertTrue(all(m.evidence is None for m in res.matches))
        window = self.db.search(self.coll, "cyclic", 8192, top_k=5, exclude="window", evidence=True)
        plain = self.db.search(self.coll, "cyclic", 8192, top_k=5, evidence=True)
        self.assertEqual([(m.ref, m.distance) for m in window.matches], [(m.ref, m.distance) for m in plain.matches])
        self.assertEqual(window.matches[0].ref.recording_id, "cyclic")    # the next cycle
        with self.assertRaises(InvalidInputError):
            self.db.search(self.coll, "cyclic", 8192, exclude="part")
        with self.assertRaises(InvalidInputError):
            self.db.search(self.coll, "cyclic", 8192, exclude="recording", exclude_source=False)

    def test_time_filters_and_evidence_when_supported(self):
        t0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
        x = events(40000, [3000, 15000, 30000], 8000)
        self.db.ingest(self.coll, "early", x, sample_rate_hz=8000, channel="c", unit="u", start_time=t0)
        self.db.ingest(self.coll, "late", x, sample_rate_hz=8000, channel="c", unit="u",
                       start_time=datetime(2026, 3, 2, tzinfo=timezone.utc))
        self.db.ingest(self.coll, "untimed", x, sample_rate_hz=8000, channel="c", unit="u")
        try:
            r = self.db.search(self.coll, "late", 14000, before_only=True, evidence=True, top_k=5)
        except morletdb.UnsupportedError:
            self.skipTest("server without search time filters")
        self.assertTrue(r.matches)
        for m in r.matches:
            self.assertNotEqual(m.ref.recording_id, "untimed")
            if m.ref.recording_id == "late":
                self.assertLessEqual(m.ref.end_sample, 14000)
            self.assertIsNotNone(m.evidence.start_utc)
            self.assertEqual(m.evidence.stats.sample_count, m.ref.length)
            self.assertEqual(len(m.evidence.overview), 16)
            seg = self.db.read_ref(m.ref).samples
            self.assertEqual((m.evidence.stats.min, m.evidence.stats.max), (seg.min(), seg.max()))
        early = self.db.search(self.coll, "late", 14000, time_from=t0, time_to=t0 + timedelta(seconds=3), top_k=5)
        self.assertTrue(early.matches)
        self.assertTrue(all(m.ref.recording_id == "early" and m.ref.end_sample <= 24000 for m in early.matches))
        with self.assertRaises(InvalidInputError):
            self.db.search(self.coll, "late", 14000, time_from=datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
