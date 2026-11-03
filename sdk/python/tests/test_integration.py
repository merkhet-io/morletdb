"""The SDK contract against a real MorletDB (PostgreSQL with pg_morlet).

    MORLET_SDK_TEST_DSN=postgresql://... python -m unittest discover -s sdk/python/tests

Without MORLET_SDK_TEST_DSN the tests are skipped; with MORLET_SDK_REQUIRE_DB=1 a missing database fails.
Every test works in its own schema, dropped afterwards.
"""
import os
import unittest
import uuid
from datetime import datetime, timezone

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


if __name__ == "__main__":
    unittest.main()
