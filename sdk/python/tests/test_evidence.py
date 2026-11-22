"""Evidence bundles (#575): one snapshot, exact digests, offline verification, no active content."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

import numpy as np
import psycopg

import morletdb
from morletdb import InvalidInputError, InvestigationLimitError, verify_evidence
from morletdb._evidence import EvidenceBundle, canonical, manifest_digest, render

DSN = os.environ.get("MORLET_SDK_TEST_DSN", "")
if not DSN and os.environ.get("MORLET_SDK_REQUIRE_DB") == "1":
    raise RuntimeError("MORLET_SDK_REQUIRE_DB=1 but MORLET_SDK_TEST_DSN is not set")


def wave(shift=0, n=8192):
    i = np.arange(n) + shift
    return np.sin(i / 9.0) + 0.2 * np.cos(i / 2.7)


def manifest():
    hostile = "<script>alert(1)</script>"
    spec = {"sample_rate_hz": 1000.0, "channel": hostile, "unit": "g", "metadata": {"note": hostile}}
    ref = {"collection": "public.c", "recording_id": hostile, "start_sample": 0, "end_sample": 2}
    data = np.array([-0.0, 1.5]).astype("<f8").tobytes()
    m = {"format": "morletdb-evidence", "version": 1, "bundle": "exact-samples", "limitations": ["limit"],
         "investigation": {"evidence_states": ["no_temporal_cutoff"], "notes": [hostile],
                           "query_stats": {"sample_count": 2, "min": -0.0, "max": 1.5, "mean": 0.75, "rms": 1.0},
                           "search": {"mode": "exact", "matches": []}, "coverage_status": "available",
                           "coverage_summary": {"samples_by_state": {"searchable": 2}}, "context": None,
                           "provenance": {"role": hostile}},
         "intervals": [{**ref, "id": "query", "role": "query", "rank": 0, "specification": spec, "raw_status": "available",
                        "sample_sha256": hashlib.sha256(data).hexdigest(), "file": "samples/query.f64le"}]}
    m["manifest_sha256"] = manifest_digest(m)
    return m, data


class Offline(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "case"
        self.manifest, self.data = manifest()
        EvidenceBundle(self.manifest, {"query": self.data}).write(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def rewrite(self, change):
        m = json.loads((self.path / "manifest.json").read_text())
        change(m)
        (self.path / "manifest.json").write_bytes(canonical(m))

    def test_clean_bundle_verifies_and_is_deterministic(self):
        self.assertTrue(verify_evidence(self.path).ok)
        self.assertEqual(render(self.manifest), render(json.loads((self.path / "manifest.json").read_text())))
        self.assertEqual(np.signbit(np.frombuffer((self.path / "samples/query.f64le").read_bytes(), "<f8"))[0], True)

    def test_report_has_no_active_content(self):
        html = (self.path / "report.html").read_text()
        self.assertNotIn("<script", html)
        self.assertIn("&lt;script&gt;", html)
        for token in ("http://", "https://", " src=", " href="):
            self.assertNotIn(token, html)

    def test_detects_a_changed_sample_even_one_sign_bit(self):
        file = self.path / "samples/query.f64le"
        file.write_bytes(np.array([0.0, 1.5]).astype("<f8").tobytes())
        self.assertIn("query: samples differ from their digest", verify_evidence(self.path).problems)

    def test_detects_a_changed_specification(self):
        self.rewrite(lambda m: m["intervals"][0]["specification"].update(unit="mm"))
        self.assertIn("manifest content does not match manifest_sha256", verify_evidence(self.path).problems)

    def test_detects_a_changed_report(self):
        (self.path / "report.html").write_text("changed")
        self.assertIn("report.html differs from the report of this manifest", verify_evidence(self.path).problems)

    def test_recomputed_digest_is_caught_by_a_digest_kept_elsewhere(self):
        kept = self.manifest["manifest_sha256"]
        def forge(m):
            m["intervals"][0]["specification"]["unit"] = "mm"
            m["manifest_sha256"] = manifest_digest(m)
        self.rewrite(forge)
        self.assertIn("manifest digest differs from the expected digest",
                      verify_evidence(self.path, expected_manifest_sha256=kept).problems)

    def test_manifest_cannot_point_outside_the_bundle(self):
        outside = Path(self.dir.name) / "outside.f64le"
        outside.write_bytes(self.data)
        self.rewrite(lambda m: m["intervals"][0].update(id="../../outside"))
        self.assertFalse(verify_evidence(self.path).ok)

    def test_writing_a_loaded_bundle_cannot_escape_its_directory(self):
        victim = Path(self.dir.name) / "victim"
        self.rewrite(lambda m: m["intervals"][0].update(file=str(victim)))      # samples are found by id, never by this
        morletdb.load_evidence(self.path).write(Path(self.dir.name) / "copy")
        self.assertFalse(victim.exists())
        self.assertTrue((Path(self.dir.name) / "copy/samples/query.f64le").is_file())

    def test_sample_symlink_and_missing_file_are_refused_on_load(self):
        file = self.path / "samples/query.f64le"
        private = Path(self.dir.name) / "private"
        private.write_bytes(self.data)
        file.unlink()
        file.symlink_to(private)
        with self.assertRaises(InvalidInputError):
            morletdb.load_evidence(self.path)
        self.assertFalse(verify_evidence(self.path).ok)
        file.unlink()
        with self.assertRaises(InvalidInputError):
            morletdb.load_evidence(self.path)

    def test_report_is_compared_as_bytes(self):
        report = self.path / "report.html"
        report.write_bytes(report.read_bytes().replace(b"\n", b"\r\n"))
        self.assertIn("report.html differs from the report of this manifest", verify_evidence(self.path).problems)

    def test_malformed_intervals_are_a_failed_check_not_a_crash(self):
        def forge(m):
            m["intervals"] = ["x"]
            m["manifest_sha256"] = manifest_digest(m)
        self.rewrite(forge)
        self.assertFalse(verify_evidence(self.path).ok)

    def test_bundle_directory_is_private(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)

    def test_existing_target_is_refused(self):
        with self.assertRaises(InvalidInputError):
            EvidenceBundle(self.manifest, {"query": self.data}).write(self.path)


@unittest.skipUnless(DSN, "set MORLET_SDK_TEST_DSN to run the evidence integration tests")
class Export(unittest.TestCase):
    def setUp(self):
        self.schema = "evidence_" + uuid.uuid4().hex[:10]
        self.conn = psycopg.connect(DSN)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        self.conn.execute(f"CREATE SCHEMA {self.schema}")
        self.db = morletdb.Client(self.conn)
        self.coll = self.schema + ".signals"
        self.db.create_collection(self.coll)
        for rid, samples in (("q", wave()), ("m", wave(40))):
            self.db.ingest(self.coll, rid, samples, sample_rate_hz=1000, channel="x", unit="g", search_window=2048,
                           metadata={"asset": "P7"})
        self.conn.commit()
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()
        self.conn.rollback()
        self.conn.execute("RESET ROLE")
        self.conn.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.conn.commit()
        self.conn.close()

    def test_bundle_round_trip_and_samples_are_exact(self):
        path = self.db.export_evidence(self.coll, "q", 0, 2048, top_k=3, include_samples=True).write(
            Path(self.dir.name) / "case")
        self.assertTrue(verify_evidence(path).ok)
        m = json.loads((path / "manifest.json").read_text())
        q = next(i for i in m["intervals"] if i["id"] == "query")
        expected = self.db.read(self.coll, "q", 0, 2048).samples.astype("<f8").tobytes()
        self.conn.rollback()
        self.assertEqual((path / "samples/query.f64le").read_bytes(), expected)
        self.assertEqual(q["sample_sha256"], hashlib.sha256(expected).hexdigest())
        self.assertGreater(len(m["intervals"]), 1)
        self.assertEqual({r["status"] for r in self.db.resolve_evidence(path)}, {"unchanged"})

    def test_report_survives_later_ingest_and_resolution_reports_changes(self):
        bundle = self.db.export_evidence(self.coll, "q", 0, 2048, top_k=3)
        self.assertEqual(bundle.manifest["bundle"], "reference-only")
        before = bundle.html()
        self.db.ingest(self.coll, "later", wave(7), sample_rate_hz=1000, channel="x", unit="g", search_window=2048)
        self.conn.execute(f"DELETE FROM {self.coll}_chunks WHERE recording_id='m'")
        self.conn.commit()
        self.assertEqual(bundle.html(), before)
        status = {r["recording_id"]: r["status"] for r in self.db.resolve_evidence(bundle)}
        self.assertEqual((status["q"], status["m"]), ("unchanged", "samples_unavailable"))
        self.conn.execute(f"DELETE FROM {self.coll} WHERE recording_id='m'")
        self.conn.commit()
        self.assertEqual({r["recording_id"]: r["status"] for r in self.db.resolve_evidence(bundle)}["m"], "missing")

    def test_context_comparison_renders_from_real_rows(self):
        self.conn.execute("SELECT morlet_enable_context(%s::regclass)", (self.coll,))
        rows = [{"recording_id": "q", "key": k, "value": v, "start_sample": 0, "end_sample": 4096}
                for k, v in (("tool", "T1"), ("part", "p1"))]
        self.conn.execute("SELECT morlet_add_context(%s::regclass,%s,%s::jsonb)", (self.coll, "q", json.dumps(rows)))
        self.conn.execute("SELECT morlet_build_context_segments(%s::regclass,'q',ARRAY['tool'],ARRAY['part'])", (self.coll,))
        self.conn.execute("SELECT morlet_build_context_values(%s::regclass,'q')", (self.coll,))
        segment = self.conn.execute(f"SELECT segment_id FROM {self.coll}_ctx_segments WHERE recording_id='q' LIMIT 1").fetchone()
        self.conn.commit()
        html = self.db.export_evidence(self.coll, "q", 0, 2048, segment_id=segment[0]).html()
        self.assertIn("Operating context", html)
        self.assertIn("History median", html)

    def test_partial_samples_are_labelled_and_tampered_paths_refused(self):
        self.conn.execute(f"DELETE FROM {self.coll}_chunks WHERE recording_id='m'")
        self.conn.commit()
        bundle = self.db.export_evidence(self.coll, "q", 0, 2048, top_k=8, include_samples=True)
        self.assertEqual(bundle.manifest["bundle"], "partial-samples")
        self.assertTrue(bundle.manifest["intervals_without_samples"])
        self.assertIn("Raw samples were unavailable", bundle.html())
        path = bundle.write(Path(self.dir.name) / "partial")
        m = json.loads((path / "manifest.json").read_text())
        m["intervals"][0]["sample_sha256"] = "0" * 64
        (path / "manifest.json").write_bytes(canonical(m))
        with self.assertRaises(InvalidInputError):
            self.db.resolve_evidence(path)

    def test_revised_time_axis_is_reported(self):
        self.conn.execute("SELECT morlet_enable_time_anchors(%s::regclass)", (self.coll,))
        self.conn.commit()
        bundle = self.db.export_evidence(self.coll, "q", 0, 2048, top_k=3)
        self.conn.execute("SELECT morlet_add_time_anchors(%s::regclass,'b1',%s::jsonb)", (self.coll, json.dumps(
            [{"recording_id": "q", "sample_offset": 0, "utc_ns": "1772366400000000000", "uncertainty_ns": 1000,
              "source": "daq", "break_before": False}])))
        self.conn.commit()
        status = {r["id"]: r["status"] for r in self.db.resolve_evidence(bundle)}
        self.assertEqual(status["query"], "time_axis_changed")

    def test_manifest_is_held_to_the_output_budget(self):
        size = len(self.db.export_evidence(self.coll, "q", 0, 2048, top_k=3).html())
        self.assertGreater(size, 0)
        with self.assertRaises(InvestigationLimitError):
            self.db.export_evidence(self.coll, "q", 0, 2048, top_k=3, max_output_bytes=3000)
        self.assertEqual(self.conn.info.transaction_status, psycopg.pq.TransactionStatus.IDLE)

    def test_limit_produces_no_partial_bundle(self):
        with self.assertRaises(InvestigationLimitError):
            self.db.export_evidence(self.coll, "q", 0, 2048, max_sample_bytes=8)
        self.assertEqual(self.conn.info.transaction_status, psycopg.pq.TransactionStatus.IDLE)

    def test_access_is_the_current_callers(self):
        bundle = self.db.export_evidence(self.coll, "q", 0, 2048, top_k=8)
        self.assertIn("m", {i["recording_id"] for i in bundle.manifest["intervals"]})
        role = "ev_" + uuid.uuid4().hex[:8]
        self.conn.execute(f"CREATE ROLE {role}")
        self.conn.commit()
        try:
            self.conn.execute(f"SET ROLE {role}")
            self.conn.commit()
            for call in (lambda: self.db.export_evidence(self.coll, "q", 0, 2048), lambda: self.db.resolve_evidence(bundle)):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    call()
                self.conn.rollback()
            self.conn.execute("RESET ROLE")
            self.conn.execute(f"GRANT USAGE ON SCHEMA {self.schema} TO {role}")
            self.conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {self.schema} TO {role}")
            self.conn.execute("SELECT morlet_enable_row_security(%s::regclass)", (self.coll,))
            self.conn.execute(f"ALTER TABLE {self.coll} ENABLE ROW LEVEL SECURITY")
            self.conn.execute(f"CREATE POLICY only_q ON {self.coll} FOR SELECT TO {role} USING (recording_id='q')")
            self.conn.commit()
            self.conn.execute(f"SET ROLE {role}")
            self.conn.commit()
            status = {r["recording_id"]: r["status"] for r in self.db.resolve_evidence(bundle)}
            self.assertEqual((status["q"], status["m"]), ("unchanged", "missing"))    # hidden is not told apart from gone
            hidden = self.db.export_evidence(self.coll, "q", 0, 2048, top_k=3)
            self.assertEqual({i["recording_id"] for i in hidden.manifest["intervals"]}, {"q"})
        finally:
            self.conn.rollback()
            self.conn.execute("RESET ROLE")
            self.conn.execute(f"DROP OWNED BY {role}")
            self.conn.execute(f"DROP ROLE {role}")
            self.conn.commit()


if __name__ == "__main__":
    unittest.main()
