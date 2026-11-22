"""Client.explain_match (#573): the scorer's own explanation of one selected match."""
import os
import unittest
import uuid

import numpy as np
import psycopg

import morletdb
from morletdb import IntervalRef, InvalidInputError

DSN = os.environ.get("MORLET_SDK_TEST_DSN", "")
if not DSN and os.environ.get("MORLET_SDK_REQUIRE_DB") == "1":
    raise RuntimeError("MORLET_SDK_REQUIRE_DB=1 but MORLET_SDK_TEST_DSN is not set")


def wave(shift, gain=1.0):
    i = np.arange(8192) + shift
    return gain * (np.sin(i / 9.0) + 0.2 * np.cos(i / 2.7) + 0.1 * np.sin(i / 53.0))


class Validation(unittest.TestCase):
    def test_refused_before_any_query(self):
        client = object.__new__(morletdb.Client)
        q = IntervalRef("a.c", "q", 0, 2048)
        with self.assertRaises(InvalidInputError):
            client.explain_match(q, IntervalRef("a.c", "m", 0, 1024), max_lag_fraction=0.075)
        for bad in ({"max_lag_fraction": True}, {"max_lag_fraction": 0.6}, {"max_lag_fraction": 0.075, "contribution_bins": 0},
                    {"max_lag_fraction": 0.075, "include_envelopes": 1}):
            with self.subTest(bad=bad), self.assertRaises(InvalidInputError):
                client.explain_match(q, IntervalRef("a.c", "m", 0, 2048), **bad)
        with self.assertRaises(InvalidInputError):      # not a stored window size
            client.explain_match(IntervalRef("a.c", "q", 0, 1000), IntervalRef("a.c", "m", 0, 1000), max_lag_fraction=0.075)
        with self.assertRaises(TypeError):      # the search's value is required: another one explains another score
            client.explain_match(q, IntervalRef("a.c", "m", 0, 2048))


@unittest.skipUnless(DSN, "set MORLET_SDK_TEST_DSN to run the explanation integration tests")
class ExplainMatch(unittest.TestCase):
    def setUp(self):
        self.schema = "explain_" + uuid.uuid4().hex[:10]
        self.conn = psycopg.connect(DSN)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        if self.conn.execute("SELECT to_regproc('public.morlet_explain_match') IS NULL").fetchone()[0]:
            self.conn.rollback()
            self.conn.close()
            self.skipTest("server without morlet_explain_match (SQL 0.3.16)")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.db = morletdb.Client(self.conn)
        self.coll = self.schema + ".signals"
        self.db.create_collection(self.coll)
        for rid, samples in (("q", wave(0)), ("m", wave(40, gain=5.0))):
            self.db.ingest(self.coll, rid, samples, sample_rate_hz=1000, channel="x", unit="g", search_window=2048)
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()

    def test_explains_the_distance_search_returned(self):
        start, distance = self.conn.execute(
            "SELECT start_sample, distance FROM morlet_search_envelope_interval(%s::regclass,'q',0,2048,top_k=>20) "
            "WHERE recording_id='m' ORDER BY distance LIMIT 1", (self.coll,)).fetchone()
        e = self.db.explain_match(IntervalRef(self.coll, "q", 0, 2048), IntervalRef(self.coll, "m", start, start + 2048),
                                  max_lag_fraction=0.075, contribution_bins=8, include_envelopes=True)
        self.assertEqual(e.distance.hex(), distance.hex())
        self.assertEqual((e.step_samples, e.lag_samples), (10, e.lag_steps * 10))
        self.assertLessEqual(abs(e.lag_steps), e.max_lag_steps)
        self.assertEqual(len(e.contributions), 8)
        self.assertAlmostEqual(sum(c.contribution for c in e.contributions) + e.contribution_residual,
                               e.best_correlation, places=12)
        for c in e.contributions:
            self.assertEqual(c.query_start - c.stored_start, e.lag_steps)
        # independent of the server: the winning lag's sum over the returned envelopes, with the full denominator
        n, lag = e.envelope_steps, e.lag_steps
        q, g = e.query_envelope, e.stored_envelope
        total = q[lag:] @ g[:n - lag] if lag >= 0 else q[:n + lag] @ g[-lag:]
        self.assertAlmostEqual(total / n, e.best_correlation, places=12)
        # normalized shapes match; the amplitude difference stays visible in the raw statistics
        self.assertGreater(e.match_stats.rms, 4 * e.query_stats.rms)

    def test_envelopes_only_on_request_and_removed_raw_is_explicit(self):
        q, m = IntervalRef(self.coll, "q", 0, 2048), IntervalRef(self.coll, "m", 0, 2048)
        self.assertIsNone(self.db.explain_match(q, m, max_lag_fraction=0.075).stored_envelope)
        self.conn.execute(f"DELETE FROM {self.coll}_chunks WHERE recording_id='m' AND start_sample=0")
        e = self.db.explain_match(q, m, max_lag_fraction=0.075)
        self.assertEqual((e.match_raw_status, e.match_stats), ("unavailable", None))
        self.assertIsNotNone(e.query_stats)
        self.conn.rollback()

    def test_errors_are_typed_and_collection_spelling_does_not_matter(self):
        q = IntervalRef(self.coll, "q", 0, 2048)
        with self.assertRaises(morletdb.NotFoundError):
            self.db.explain_match(q, IntervalRef(self.coll, "m", 7, 2055), max_lag_fraction=0.075)
        self.conn.rollback()
        with self.assertRaises(morletdb.NotFoundError):
            self.db.explain_match(q, IntervalRef(self.coll, "none", 0, 2048), max_lag_fraction=0.075)
        self.conn.rollback()
        self.db.ingest(self.coll, "other", wave(0), sample_rate_hz=500, channel="x", unit="g", search_window=2048)
        self.conn.commit()
        with self.assertRaises(InvalidInputError):
            self.db.explain_match(q, IntervalRef(self.coll, "other", 0, 2048), max_lag_fraction=0.075)
        self.conn.rollback()
        with self.assertRaises(morletdb.NotFoundError):      # the id's own words do not change the error's meaning
            self.db.explain_match(q, IntervalRef(self.coll, "pump must be in service", 0, 2048), max_lag_fraction=0.075)
        self.conn.rollback()
        spelled = '"' + self.schema + '"."signals"'
        e = self.db.explain_match(q, IntervalRef(spelled, "m", 0, 2048), max_lag_fraction=0.075)
        self.assertEqual(e.match_raw_status, "available")
        self.conn.rollback()

    def test_signed_zero_in_raw_statistics_survives(self):
        self.db.ingest(self.coll, "z", np.full(8192, -0.0), sample_rate_hz=1000, channel="x", unit="g", search_window=2048)
        self.conn.commit()
        e = self.db.explain_match(IntervalRef(self.coll, "z", 0, 2048), IntervalRef(self.coll, "z", 2048, 4096),
                                  max_lag_fraction=0.075)
        self.assertEqual(e.query_stats.min.hex(), self.db.stats(self.coll, "z", 0, 2048).min.hex())
        self.assertTrue(np.signbit(e.query_stats.min))
        self.conn.rollback()


if __name__ == "__main__":
    unittest.main()
