# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
"""The replay machine against a real MorletDB: bit-exact readback, bounded batches, restarts, search readiness and
the expected synthetic matches through the companion."""
import dataclasses
import time
import unittest

import numpy as np

import support
from machine import WINDOW, current, history, synthesize
from replay import replay_current, write_history
from writer import recording_id, write_acquisition

import morletdb

ENV = None


def setUpModule():
    global ENV
    ENV = support.Env(support.database())
    with ENV.writer() as w:
        write_history(w, ENV.collection)


def tearDownModule():
    ENV.drop()


def bits(a):
    return np.ascontiguousarray(a, dtype="<f8").view("<u8")


class Machine(unittest.TestCase):
    def test_history_reads_back_bit_exact(self):
        with ENV.writer() as w:
            db = morletdb.Client(w)
            for plan in history():
                data = synthesize(plan)
                for role, x in data.items():
                    got = db.read(ENV.collection, recording_id(plan, role), 0, x.size).samples
                    self.assertTrue(np.array_equal(bits(got), bits(x)), f"{plan.acquisition_id}/{role}")

    def test_synthesis_is_deterministic(self):
        a, b = synthesize(current()), synthesize(current())
        for role in a:
            self.assertTrue(np.array_equal(bits(a[role]), bits(b[role])))

    def test_signed_zero_and_grid_values_survive(self):
        x = np.array([0.0, -0.0, 2.0 ** -12, -(2.0 ** -12), 5e-324, -1e9, 1e9] * 600)
        with ENV.writer() as w:
            db = morletdb.Client(w)
            db.ingest(ENV.collection, "zeros", x, sample_rate_hz=1000, channel="zero_test", unit="u")
            w.commit()
            got = db.read(ENV.collection, "zeros", 0, x.size).samples
        self.assertTrue(np.array_equal(bits(got), bits(x)))

    def test_bounded_batches_resume_and_conflict(self):
        plan = dataclasses.replace(current(), acquisition_id=f"resume-{ENV.hex}")
        origin = 1_791_000_000_000_000_000
        seen = []
        with ENV.writer() as w:
            # Interrupted after the first transaction, then restarted.
            write_acquisition(w, ENV.collection, plan, origin, batch_samples=16_384, stop_after_batches=1)
            db = morletdb.Client(w)
            self.assertEqual(db.recording(ENV.collection, recording_id(plan, "vib")).samples, 16_384)
            write_acquisition(w, ENV.collection, plan, origin, batch_samples=16_384,
                              on_batch=lambda i, ends: seen.append(ends["vib"]))
            data = synthesize(plan)
            for role, x in data.items():
                got = db.read(ENV.collection, recording_id(plan, role), 0, x.size).samples
                self.assertTrue(np.array_equal(bits(got), bits(x)))
            # Batches were bounded, and every committed batch was searchable at its commit.
            self.assertEqual(seen[0], 16_384)
            self.assertTrue(all(b - a <= 16_384 for a, b in zip(seen, seen[1:])))
            # A full identical rerun is a no-op; a conflicting replay fails and changes nothing.
            write_acquisition(w, ENV.collection, plan, origin, batch_samples=16_384)
            bad = data["vib"][:4096].copy()
            bad[100] += 2.0 ** -12
            with self.assertRaises(morletdb.ConflictError):
                db.append(ENV.collection, recording_id(plan, "vib"), 0, bad)
            w.rollback()
            got = db.read(ENV.collection, recording_id(plan, "vib"), 0, 4096).samples
            self.assertTrue(np.array_equal(bits(got), bits(data["vib"][:4096])))
            # A changed specification is refused, not continued.
            with self.assertRaises(Exception):
                write_acquisition(w, ENV.collection, plan, origin + 1000, batch_samples=16_384)
            w.rollback()

    def test_searchable_at_the_commit_that_stores_the_batch(self):
        plan = dataclasses.replace(current(), acquisition_id=f"ready-{ENV.hex}")
        rid = recording_id(plan, "vib")
        states = []

        def check(i, ends):
            with ENV.connect() as c:   # another session: sees only what was committed
                rows = c.execute("SELECT start_sample, end_sample, state FROM morlet_coverage(%s::regclass, %s, %s)",
                                 (ENV.collection, [rid], WINDOW)).fetchall()
            states.append((ends["vib"], rows))

        with ENV.writer() as w:
            write_acquisition(w, ENV.collection, plan, 1_791_100_000_000_000_000, batch_samples=32_768,
                              on_batch=check)
        for end, rows in states:
            searchable = [(a, b) for a, b, s in rows if s == "searchable"]
            # Complete windows on the stride grid are searchable; only a tail shorter than a window is not.
            self.assertEqual(searchable, [(0, end - end % 2048)] if end >= WINDOW else [])
            self.assertTrue(all(s in ("searchable", "not_indexed") for _, _, s in rows))

    def test_expected_synthetic_matches(self):
        # A collection of its own: other tests here add more copies of the current acquisition.
        env = support.Env(support.database())
        self.addCleanup(env.drop)
        with env.writer() as w:
            write_history(w, env.collection)
        self._expected_matches(env)

    def _expected_matches(self, ENV):
        adapter = ENV.start()
        try:
            plan = dataclasses.replace(current(), acquisition_id=f"match-{ENV.hex}")
            origin = (time.time_ns() // 1000) * 1000 - plan.end_ns
            with ENV.writer() as w:
                replay_current(w, ENV.collection, adapter, origin, schema=ENV.cfg.schema, plan=plan)
            impact = next(e for e in plan.events if e.label == "impact")
            body = adapter.start_investigation("panel", ENV.collection, recording_id(plan, "vib"), impact.vib_start,
                                               impact.vib_start + WINDOW, top_k=10)
            r = adapter.send_investigation(body)
            self.assertEqual(r.status, 200, r.body)
            self.assertEqual(r.body["status"], "ok")
            self.assertEqual(r.body["search_mode"], "exact")
            self.assertTrue(r.body["readiness"]["source_searchable"])
            matches = r.body["matches"]
            # Every historical impact, and nothing else, in the first eight; then a clear gap.
            hist_impacts = {(recording_id(p, "vib"), e.vib_start) for p in history() for e in p.events
                            if e.label == "impact"}
            top = matches[:8]
            for m in top:
                self.assertEqual([lb["label"] for lb in m["labels"]], ["impact"], m)
                lb = m["labels"][0]
                self.assertTrue(m["start_sample"] < lb["end_sample"] and lb["start_sample"] < m["end_sample"])
            found = {(m["recording_id"], next(lb["start_sample"] for lb in m["labels"])) for m in top}
            self.assertEqual(found, hist_impacts)
            self.assertLess(max(m["distance"] for m in top), 0.15)
            self.assertGreater(matches[8]["distance"], 0.25)
            # Results are references with context, never samples.
            self.assertNotIn("samples", str(matches[0].keys()))
            self.assertTrue(all(len(m["overview"]) == ENV.cfg.limits.overview_buckets for m in matches))
            self.assertTrue({c["key"] for c in top[0]["context"]} >= {"speed", "load"})
            self.assertTrue(top[0]["start_utc"].startswith("2026-10-"))
            # Each match reads back bit for bit from its reference.
            with ENV.writer() as w:
                db = morletdb.Client(w)
                for m in top[:3]:
                    p = next(p for p in history() if recording_id(p, "vib") == m["recording_id"])
                    got = db.read(ENV.collection, m["recording_id"], m["start_sample"], m["end_sample"]).samples
                    want = synthesize(p)["vib"][m["start_sample"]:m["end_sample"]]
                    self.assertTrue(np.array_equal(bits(got), bits(want)))
        finally:
            ENV.stop()


if __name__ == "__main__":
    unittest.main()
