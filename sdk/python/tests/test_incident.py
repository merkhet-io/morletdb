"""Client.resolve_incident (#580): asset + incident time -> recording intervals, with nothing guessed."""
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import patch
import uuid

import numpy as np
import psycopg
from psycopg.pq import TransactionStatus

import morletdb
from morletdb import InvalidInputError, InvestigationLimitError

DSN = os.environ.get("MORLET_SDK_TEST_DSN", "")
if not DSN and os.environ.get("MORLET_SDK_REQUIRE_DB") == "1":
    raise RuntimeError("MORLET_SDK_REQUIRE_DB=1 but MORLET_SDK_TEST_DSN is not set")

BASE = 1_772_366_400_000_000_000      # 2026-03-01T12:00:00Z
MS = 1_000_000
SECOND = 1_000_000_000


class Validation(unittest.TestCase):
    def test_invalid_requests_need_no_connection(self):
        client = object.__new__(morletdb.Client)
        naive = datetime(2026, 3, 1, 12)
        for args, kwargs in ((("", BASE), {}), (("a", naive), {}), (("a", None), {}), (("a", True), {}),
                             (("a", BASE), {"before_s": 0, "after_s": 0}), (("a", BASE), {"before_s": -1}),
                             (("a", BASE), {"after_s": float("nan")}), (("a", BASE), {"before_s": 10 ** 1000}),
                             (("a", BASE), {"references": []}), (("a", BASE), {"reference_keys": []}), (("a", BASE), {"before_s": 86400 * 32}),
                             (("a", BASE), {"max_recordings": 0}), (("a", BASE), {"timeout_ms": True}),
                             (("a", BASE), {"identity_keys": "plant"}), (("a", BASE), {"metadata_filter": []}),
                             (("a", BASE), {"references": {"work_order": "WO-1"}}),     # no metadata key for it
                             (("a", BASE), {"metadata_filter": {"asset": "other"}})):
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(InvalidInputError):
                client.resolve_incident("public.signals", *args, **kwargs)


@unittest.skipUnless(DSN, "set MORLET_SDK_TEST_DSN to run the incident integration tests")
class Incident(unittest.TestCase):
    def setUp(self):
        self.schema = "incident_" + uuid.uuid4().hex[:10]
        self.conn = psycopg.connect(DSN)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        if self.conn.execute("SELECT to_regproc('public.morlet_utc_to_samples') IS NULL").fetchone()[0]:
            self.conn.rollback()
            self.conn.close()
            self.skipTest("server without time anchors (SQL 0.3.13)")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.db = morletdb.Client(self.conn)
        self.coll = self.schema + ".signals"
        self.db.create_collection(self.coll)
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute("RESET ROLE")
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()

    def add(self, rid, n=2000, start=BASE, channel="x", **metadata):
        rec = self.db.ingest(self.coll, rid, np.sin(np.arange(n) / 9.0).round(4), sample_rate_hz=1000, channel=channel,
                             unit="g", start_time=start, metadata={"asset": "P7", **metadata}, search_window=512)
        self.conn.commit()
        return rec

    def anchors(self, rid, rows, breaks=True):
        self.conn.execute("SELECT morlet_enable_time_anchors(%s::regclass)", (self.coll,))
        self.conn.execute("SELECT morlet_add_time_anchors(%s::regclass,%s,%s::jsonb)", (self.coll, "b-" + rid, json.dumps(
            [{"recording_id": rid, "sample_offset": n, "utc_ns": str(BASE + t), "uncertainty_ns": 1000,
              "source": "daq", "break_before": breaks and n > 0} for n, t in rows])))
        self.conn.commit()

    def resolve(self, at=BASE + SECOND, before_s=0.1, after_s=0.05, asset="P7", **kwargs):
        return self.db.resolve_incident(self.coll, asset, at, before_s=before_s, after_s=after_s, **kwargs)

    def test_exact_boundaries_are_half_open(self):
        self.add("r")
        result = self.resolve()
        c = result.candidates[0]
        self.assertEqual((c.ref.recording_id, c.ref.start_sample, c.ref.end_sample), ("r", 900, 1050))
        self.assertEqual((result.window_start_ns, result.window_end_ns), (BASE + 900 * MS, BASE + 1050 * MS))
        self.assertEqual((c.samples, c.expected_samples, c.covers_window, c.sample_rate_hz, c.unit),
                         (150, 150, True, 1000, "g"))
        self.assertEqual((c.basis, c.ambiguous, c.extrapolated), ("start+rate", False, False))
        self.assertIn("time_uncertainty_unknown", result.states)      # start + rate has no error bound
        self.assertEqual(self.db.read_ref(c.ref).samples.size, 150)
        self.conn.rollback()
        # between two samples: the first sample at or after the bound, as everywhere in the server
        c = self.resolve(before_s=0.1005).candidates[0]
        self.assertEqual((c.ref.start_sample, c.expected_samples, c.covers_window), (900, 151, True))
        aware = datetime(2026, 3, 1, 13, 0, 1, tzinfo=timezone(timedelta(hours=1)))   # 12:00:01 UTC
        self.assertEqual(self.resolve(at=aware).incident_ns, BASE + SECOND)

    def test_different_ways_of_finding_nothing_stay_different(self):
        self.assertEqual(self.resolve().states, ["asset_not_found"])
        self.db.ingest(self.coll, "untimed", np.zeros(1000), sample_rate_hz=1000, channel="x", unit="g",
                       metadata={"asset": "P7"}, search_window=512)
        self.conn.commit()
        result = self.resolve()
        self.assertEqual((result.candidates, result.recordings_without_time_axis), ([], ["untimed"]))
        self.assertIn("recordings_without_time_axis", result.states)
        self.assertIn("no_recording_at_time", result.states)
        self.add("timed", n=500)
        result = self.resolve(at=BASE + 10 * SECOND)
        self.assertEqual(result.recordings_outside_window, ["timed"])
        self.assertIn("no_recording_at_time", result.states)

    def test_a_window_past_the_end_is_partial_not_stretched(self):
        self.add("r", n=1000)
        result = self.resolve(at=BASE + SECOND, before_s=0.1, after_s=0.1)
        c = result.candidates[0]
        self.assertEqual((c.ref.start_sample, c.ref.end_sample, c.expected_samples, c.covers_window),
                         (900, 1000, 200, False))
        self.assertIn("partial_window", result.states)

    def test_first_sample_a_period_late_is_partial(self):
        # a first sample a whole period after the inclusive window start leaves one position uncovered
        self.add("late", n=1000, start=BASE + SECOND)
        c = self.resolve(at=BASE + SECOND, before_s=0.001, after_s=0.1).candidates[0]
        self.assertEqual((c.ref.start_sample, c.samples, c.expected_samples, c.covers_window), (0, 100, 101, False))
        self.conn.rollback()

    def test_start_time_is_read_as_the_server_reads_it(self):
        self.db.ingest(self.coll, "text-ns", np.zeros(2000), sample_rate_hz=1000, channel="x", unit="g",
                       metadata={"asset": "P7", "start_ns": str(BASE)}, search_window=512)
        self.conn.commit()
        result = self.resolve()
        self.assertEqual(result.recordings_without_time_axis, [])
        self.assertEqual(result.candidates[0].ref.start_sample, 900)

    def test_clipped_candidate_describes_only_stored_samples(self):
        self.add("r", n=1000)
        self.anchors("r", [(0, 0), (999, 999 * MS)], breaks=False)
        c = self.resolve(at=BASE + 950 * MS, before_s=0.05, after_s=0.15).candidates[0]
        self.assertEqual((c.ref.start_sample, c.ref.end_sample, c.extrapolated), (900, 1000, False))
        self.assertLessEqual(c.end_uncertainty_ns, 1000)

    def test_removed_chunks_under_a_retained_window_are_unreadable(self):
        self.add("r", n=20000)
        lo, hi = self.conn.execute(f"SELECT start_sample, start_sample+sample_count FROM {self.coll}_chunks "
                                   "WHERE recording_id='r' AND start_sample>0 ORDER BY 1 LIMIT 1").fetchone()
        self.conn.execute(f"DELETE FROM {self.coll}_chunks WHERE recording_id='r' AND start_sample=%s", (lo,))
        self.conn.commit()
        c = self.resolve(at=BASE + (lo + 10) * MS, before_s=0.01, after_s=0.01).candidates[0]
        self.assertGreater(c.unreadable_samples, 0)

    def test_equal_json_numbers_verify_and_missing_sensors_stay_apart(self):
        self.add("a", wo=42.0)
        self.add("b", wo=42)
        result = self.resolve(sensor_key="position", references={"work_order": 42}, reference_keys={"work_order": "wo"})
        self.assertEqual({c.references["work_order"]["status"] for c in result.candidates}, {"verified"})
        self.assertIn("sensor_not_recorded", result.states)
        self.assertIn("multiple_sensors", result.states)
        self.assertNotIn("multiple_recordings_per_sensor", result.states)

    def test_the_limit_follows_the_window_not_the_history(self):
        for day in (1, 2, 3):
            self.add(f"old-{day}", n=500, start=BASE - day * 86400 * SECOND, plant="north" if day == 1 else "south")
        self.add("now", plant="south")
        result = self.resolve(max_recordings=1, identity_keys=("plant",))
        self.assertEqual([c.ref.recording_id for c in result.candidates], ["now"])
        self.assertEqual((result.recordings_outside_window, result.recordings_outside_window_count),
                         (["old-1", "old-2", "old-3"], 3))
        self.assertIn("ambiguous_asset", result.states)      # the identity is checked over the whole history

    def test_listed_ids_are_bounded_and_counts_exact(self):
        for day in (1, 2, 3):
            self.add(f"old-{day}", n=500, start=BASE - day * 86400 * SECOND)
        with patch("morletdb._incident.MAX_LISTED", 2):
            result = self.resolve()
        self.assertEqual((result.recordings_outside_window, result.recordings_outside_window_count),
                         (["old-1", "old-2"], 3))

    def test_anchored_recording_is_mapped_despite_its_start_time(self):
        self.add("moved", n=2000, start=BASE - 86400 * SECOND)
        self.anchors("moved", [(0, 0), (1999, 1999 * MS)], breaks=False)
        result = self.resolve(max_recordings=1)
        self.assertEqual([c.ref.recording_id for c in result.candidates], ["moved"])

    def test_two_recordings_are_never_concatenated(self):
        self.add("a", n=1000)
        self.add("b", n=1000, start=BASE + SECOND)
        result = self.resolve()
        self.assertEqual([(c.ref.recording_id, c.ref.start_sample, c.ref.end_sample) for c in result.candidates],
                         [("a", 900, 1000), ("b", 0, 50)])
        self.assertIn("multiple_recordings_per_sensor", result.states)
        self.assertIn("partial_window", result.states)

    def test_multiple_channels_and_sensor_selection(self):
        self.add("x", channel="accel-x", position="DE")
        self.add("y", channel="accel-y", position="NDE")
        result = self.resolve()
        self.assertEqual({c.sensor for c in result.candidates}, {"accel-x", "accel-y"})
        self.assertIn("multiple_sensors", result.states)
        self.assertEqual([c.ref.recording_id for c in self.resolve(sensor="accel-y").candidates], ["y"])
        keyed = self.resolve(sensor="DE", sensor_key="position")
        self.assertEqual([(c.ref.recording_id, c.sensor) for c in keyed.candidates], [("x", "DE")])
        self.assertNotIn("multiple_sensors", keyed.states)

    def test_a_reused_asset_id_is_ambiguous_until_scoped(self):
        self.add("a", plant="north")
        self.add("b", plant="south")
        self.assertIn("asset_identity_not_scoped", self.resolve().states)
        result = self.resolve(identity_keys=("plant",))
        self.assertIn("ambiguous_asset", result.states)
        self.assertEqual(result.identities, [{"plant": "north"}, {"plant": "south"}])
        scoped = self.resolve(identity_keys=("plant",), metadata_filter={"plant": "south"})
        self.assertNotIn("ambiguous_asset", scoped.states)
        self.assertEqual([c.ref.recording_id for c in scoped.candidates], ["b"])

    def test_backward_clock_step_returns_every_occurrence(self):
        self.add("r", n=300)
        self.anchors("r", ((0, 0), (100, 113 * MS), (200, 208 * MS)))         # block 2 starts before block 1 ends
        result = self.resolve(at=BASE + 209 * MS, before_s=0.001, after_s=0.001)
        self.assertEqual([(c.ref.start_sample, c.ref.end_sample, c.ambiguous, c.basis) for c in result.candidates],
                         [(195, 197, True, "anchors"), (200, 202, True, "anchors")])
        self.assertIn("ambiguous_time", result.states)
        self.assertTrue(result.candidates[0].time_axis_version.startswith("anchors:"))
        self.assertEqual(result.candidates[1].start_uncertainty_ns, 1000)

    def test_forward_clock_jump_inside_the_window_is_partial(self):
        self.add("r", n=200)
        self.anchors("r", ((0, 0), (100, 150 * MS)))                          # 50 ms without samples
        result = self.resolve(at=BASE + 120 * MS, before_s=0.05, after_s=0.05)
        c = result.candidates[0]
        self.assertEqual((c.ref.start_sample, c.ref.end_sample, c.expected_samples, c.covers_window),
                         (70, 120, 100, False))
        self.assertIn("partial_window", result.states)

    def test_a_drifting_clock_is_not_a_partial_window(self):                  # #583 review
        self.add("r", n=2000)
        self.anchors("r", ((0, 0), (1999, 2019 * MS + 990_000)), breaks=False)  # 1% slow: fewer samples per second
        c = self.resolve(at=BASE + 1100 * MS, before_s=0.9, after_s=0.1).candidates[0]
        self.assertLess(c.samples, c.expected_samples - 1)
        self.assertTrue(c.covers_window)

    def test_identity_without_a_value_and_unusual_sensor_values(self):        # #583 review
        self.add("a", plant="north", position=["DE", 1])
        self.add("b", position=["DE", 1])
        result = self.resolve(identity_keys=("plant",), sensor_key="position")
        self.assertIn("asset_identity_not_scoped", result.states)
        self.assertIn("ambiguous_asset", result.states)
        self.assertIn("multiple_recordings_per_sensor", result.states)

    def test_extrapolated_time_is_named(self):
        self.add("r", n=1000)
        self.anchors("r", ((0, 0),))
        result = self.resolve(at=BASE + 500 * MS)
        self.assertTrue(result.candidates[0].extrapolated)
        self.assertIn("extrapolated_time", result.states)

    def test_compacted_samples_are_unreadable_not_absent(self):
        self.add("r")
        self.conn.execute("SELECT morlet_enable_memory(%s::regclass)", (self.coll,))
        eid = self.conn.execute("SELECT morlet_add_episodes(%s::regclass,%s::jsonb)", (self.coll, json.dumps(
            [{"recording_id": "r", "start_sample": 0, "end_sample": 2000}]))).fetchone()[0]
        self.conn.execute("SELECT * FROM morlet_set_level(%s::regclass,morlet_start_memory_run(%s::regclass,'test','1'),%s::jsonb)",
                          (self.coll, self.coll, json.dumps([{"episode_id": eid, "level": 4}])))
        self.conn.commit()
        result = self.resolve()
        c = result.candidates[0]
        self.assertEqual((c.unreadable_samples, c.coverage_states), (150, {"gone": 150}))
        self.assertIn("unreadable_samples", result.states)

    def test_references_are_verified_only_by_a_stored_value(self):
        self.add("r", wo="WO-17", lot=42)
        refs = self.resolve(references={"work_order": "WO-17", "lot": "41", "part": "P-1"},
                            reference_keys={"work_order": "wo", "lot": "lot", "part": "part"}).candidates[0].references
        self.assertEqual({k: v["status"] for k, v in refs.items()},
                         {"work_order": "verified", "lot": "different", "part": "not_recorded"})
        result = self.resolve(references={"lot": "41", "part": "P-1"}, reference_keys={"lot": "lot", "part": "part"})
        self.assertIn("reference_different", result.states)
        typed = self.resolve(references={"as_text": "42", "as_number": 42}, reference_keys={"as_text": "lot", "as_number": "lot"})
        self.assertEqual({k: v["status"] for k, v in typed.candidates[0].references.items()},
                         {"as_text": "different", "as_number": "verified"})
        self.assertIn("reference_not_recorded", result.states)

    def test_limits_snapshot_and_serialization(self):
        self.add("a")
        self.add("b")
        with self.assertRaises(InvestigationLimitError):
            self.resolve(max_recordings=1)
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.IDLE)
        result = self.resolve()
        self.assertEqual(self.conn.info.transaction_status, TransactionStatus.IDLE)
        payload = json.loads(json.dumps(result.to_dict(), allow_nan=False))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["incident_ns"], BASE + SECOND)              # exact, not a float
        self.assertEqual(payload["candidates"][0]["ref"]["end_sample"], 1050)
        self.conn.execute(f"UPDATE {self.coll} SET metadata = metadata || '{{\"pending\": true}}'")   # caller's work
        with self.assertRaisesRegex(InvalidInputError, "read-only"):
            self.resolve()
        self.assertTrue(self.db.recording(self.coll, "a").metadata["pending"])
        self.conn.rollback()

    def test_non_owner_cannot_learn_of_a_hidden_recording(self):
        self.add("open", site="public")
        self.add("hidden", site="secret")
        self.add("only-hidden", site="secret", asset="Q9")
        role = self.schema + "_reader"
        self.conn.execute(f"CREATE ROLE {role}")
        try:
            self.conn.execute(f"GRANT USAGE ON SCHEMA {self.schema} TO {role}")
            self.conn.execute(f"ALTER TABLE {self.coll} ENABLE ROW LEVEL SECURITY")
            self.conn.execute(f"CREATE POLICY scope ON {self.coll} USING (metadata->>'site'='public')")
            self.conn.execute("SELECT morlet_enable_row_security(%s::regclass)", (self.coll,))
            self.conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {self.schema} TO {role}")
            self.conn.commit()
            self.assertEqual(len(self.resolve().candidates), 2)
            self.conn.execute(f"SET ROLE {role}")
            self.conn.commit()
            result = self.resolve(identity_keys=("site",))
            self.assertEqual([c.ref.recording_id for c in result.candidates], ["open"])
            self.assertNotIn("ambiguous_asset", result.states)
            self.assertNotIn("multiple_recordings_per_sensor", result.states)
            # an asset that exists only in hidden rows answers exactly as one that does not exist
            hidden, absent = self.resolve(asset="Q9"), self.resolve(asset="NOPE")
            self.assertEqual((hidden.states, hidden.candidates, hidden.recordings_outside_window),
                             (absent.states, absent.candidates, absent.recordings_outside_window))
        finally:
            self.conn.rollback()
            self.conn.execute("RESET ROLE")
            self.conn.execute(f"DROP OWNED BY {role}")
            self.conn.execute(f"DROP ROLE {role}")
            self.conn.commit()


if __name__ == "__main__":
    unittest.main()
