# Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);
# not under the Apache License 2.0. Production use beyond the free tier requires a subscription.
"""Observations and derived context against a real MorletDB, through the companion's HTTP interface: timing,
quality and value preservation, idempotency, conflicts, out-of-range times, gaps, the clock bound, late channels
and restarts."""
import dataclasses
import json
import time
import unittest
import uuid

import morletdb

import support
from machine import CHANNELS, current
from morlet_winccoa import config as cfgmod
from morlet_winccoa.observations import mark_finished
from support import LOAD, SPEED, expected_samples
from writer import origin_utc, recording_id, write_acquisition

GOOD = {"good": True}
TEMP = "System1:Rig1.temperature"
RAW = "System1:Rig1.raw"
OBSERVED = "System1:Rig1.mode"
EXTRA = (cfgmod.Datapoint(TEMP, "rig-1", "temperature"),
         cfgmod.Datapoint(RAW, "rig-1", "raw_state", accept_missing_quality=True))


class Base(unittest.TestCase):
    datapoints = EXTRA
    clock = None

    def setUp(self):
        self.env = support.Env(support.database(), datapoints=self.datapoints, clock=self.clock)
        self.addCleanup(self.env.drop)
        self.adapter = self.env.start()

    def acquisition(self, *, ago_s: float = 16, seconds: int | None = None, batches: int | None = None):
        """A written acquisition whose origin lies `ago_s` seconds before now (samples may run ahead of now)."""
        plan = dataclasses.replace(current(), acquisition_id=f"acq-{uuid.uuid4().hex[:8]}")
        if seconds is not None:
            plan = dataclasses.replace(plan, seconds=seconds, events=())
        origin = (time.time_ns() // 1000) * 1000 - int(ago_s * 1e9)
        origin -= origin % 1000
        with self.env.writer() as w:
            write_acquisition(w, self.env.collection, plan, origin, stop_after_batches=batches)
        return plan, origin

    def finish(self, plan):
        with self.env.writer() as w:
            mark_finished(w, self.env.cfg, self.env.collection, plan.acquisition_id, plan.end_ns)
            w.commit()

    def send(self, expect=200):
        rs = self.adapter.flush()
        self.assertTrue(rs, "nothing sent")
        self.assertEqual(rs[-1].status, expect, rs[-1].body)
        return rs[-1]

    def ctx(self, plan):
        with self.env.writer() as w:
            return w.execute(f"SELECT recording_id, key, value, start_sample, end_sample FROM {self.env.collection}_ctx "
                             "WHERE recording_id LIKE %s AND NOT retracted ORDER BY recording_id, key, start_sample",
                             (plan.acquisition_id + "-%",)).fetchall()

    def derivations(self, plan):
        with self.env.writer() as w:
            return w.execute(f"SELECT d.key, d.batch_id, d.reason, d.t_start_ns, d.t_end_ns, d.time_basis, o.seq "
                             f"FROM {self.env.cfg.schema}.derivation d JOIN {self.env.cfg.schema}.observation o "
                             "USING (obs_id) WHERE d.acquisition_id = %s ORDER BY o.seq", (plan.acquisition_id,)).fetchall()

    def expected_rows(self, plan, key, intervals):
        out = []
        for role, (rate, *_rest) in CHANNELS.items():
            offset = CHANNELS[role][3]
            for t0, t1, value in intervals:
                a, b = expected_samples(rate, offset, t0, t1)
                if b > a:
                    out.append((recording_id(plan, role), key, value, a, b))
        return out


class Timing(Base):
    def test_context_rows_at_exact_sample_boundaries(self):
        plan, origin = self.acquisition()
        t1, t2 = 6_500_000_123, 11_250_000_017       # not on any sample of either channel
        a, A = plan.acquisition_id, "rig-1"
        self.adapter.heartbeat(a, A)
        self.adapter.change(a, SPEED, "1800", "int", origin, quality=GOOD)
        self.adapter.change(a, SPEED, "1200", "int", origin + t1, quality=GOOD)
        self.adapter.change(a, SPEED, "1800", "int", origin + t2, quality=GOOD)
        self.send()
        self.env.derive(a)
        # The last state is open: reported, not written.
        self.assertEqual(sorted(r[3:5] for r in self.ctx(plan) if r[0].endswith("-vib")),
                         [(0, expected_samples(8192, 0, 0, t1)[1]), expected_samples(8192, 0, t1, t2)])
        health = self.env.client.request("GET", "/v1/health").body
        asset = health["assets"][0]
        self.assertEqual(asset["current_acquisition"], a)
        self.assertEqual(asset["open_states"][0]["key"], "speed")
        self.assertEqual(asset["open_states"][0]["open_since_ns"], str(t2))
        self.assertEqual(asset["clock"]["state"], "ok")
        self.finish(plan)
        self.env.derive(a)
        want = self.expected_rows(plan, "speed", [(0, t1, 1800), (t1, t2, 1200), (t2, plan.end_ns, 1800)])
        self.assertEqual(sorted(self.ctx(plan)), sorted(want))
        d = self.derivations(plan)
        self.assertEqual([r[2] for r in d], [None, None, None])
        self.assertTrue(all(r[1].startswith(f"winccoa:{a}:speed:") for r in d))
        self.assertEqual([(r[3], r[4]) for r in d], [(0, t1), (t1, t2), (t2, plan.end_ns)])

    def test_state_before_the_first_sample_holds_at_sample_zero(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, LOAD, '"high"', "string", origin - 10_000_000_000, quality=GOOD)
        self.adapter.change(a, LOAD, '"low"', "string", origin - 4_000_000_000, quality=GOOD)
        self.adapter.change(a, LOAD, '"high"', "string", origin + 3_000_000_000, quality=GOOD)
        self.send()
        self.finish(plan)
        self.env.derive(a)
        d = self.derivations(plan)
        self.assertEqual(d[0][2], "outside_recording")          # wholly before sample 0 on every channel
        self.assertEqual(sorted(self.ctx(plan)), sorted(self.expected_rows(
            plan, "load", [(-4_000_000_000, 3_000_000_000, "low"), (3_000_000_000, plan.end_ns, "high")])))
        self.assertIn((recording_id(plan, "vib"), "load", "low", 0, 24_576), self.ctx(plan))

    def test_after_the_end_and_past_committed_samples(self):
        plan, origin = self.acquisition(batches=1)          # 8 s of 16 committed
        a = plan.acquisition_id
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, SPEED, "1800", "int", origin, quality=GOOD)
        self.adapter.change(a, SPEED, "1200", "int", origin + 12_000_000_000, quality=GOOD)
        self.send()
        self.env.derive(a)
        # An interval past the committed samples is stored; segments use it once the samples arrive.
        vib = [r for r in self.ctx(plan) if r[0].endswith("-vib")]
        self.assertEqual(vib, [(recording_id(plan, "vib"), "speed", 1800, 0, 98_304)])
        with self.env.writer() as w:
            self.assertEqual(morletdb.Client(w).recording(self.env.collection, recording_id(plan, "vib")).samples,
                             65_536)
            write_acquisition(w, self.env.collection, plan, origin)
        self.finish(plan)
        self.env.derive(a)
        # The open state is closed at the end of the finished acquisition.
        self.assertIn((recording_id(plan, "vib"), "speed", 1200, 98_304, 131_072), self.ctx(plan))
        # A state that begins after the end maps to no sample, and the one before it stays clipped.
        self.adapter.change(a, SPEED, "1800", "int", origin + 20_000_000_000, quality=GOOD)
        self.send()
        s = self.env.derive(a)
        self.assertEqual(s.mismatches, 0)
        d = self.derivations(plan)
        self.assertEqual([(r[2], r[3], r[4]) for r in d][1:],
                         [(None, 12_000_000_000, 16_000_000_000), ("outside_recording", 20_000_000_000, 16_000_000_000)])

    def test_out_of_range_and_malformed_times_are_refused(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        for bad in ("9223372036854775808", "-9223372036854775809", "1.5e18", "", "0x10"):
            self.adapter.unacked.clear()
            self.adapter.change(a, SPEED, "1", "int", None, quality=GOOD)["source_ns"] = bad
            self.assertEqual(self.send(400).body["error"], "malformed")
        self.adapter.unacked.clear()
        o = self.adapter.change(a, SPEED, "1", "int", None, quality=GOOD)
        o["source_ns"] = int(origin)                                   # a JSON number, not a string
        self.assertEqual(self.send(400).body["error"], "malformed")
        with self.env.writer() as w:
            self.assertEqual(w.execute(f"SELECT count(*) FROM {self.env.cfg.schema}.observation").fetchone()[0], 0)


class Values(Base):
    def test_quality_and_value_text_preserved(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        quality = {"good": True, "bits": "0x8000000000000041", "user": [1, 2.5], "_online.._invalid": False}
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, TEMP, "-0.0", "float", origin, quality=quality)
        self.adapter.change(a, TEMP, "NaN", "float", origin + 1_000_000_000, quality=GOOD)
        self.adapter.change(a, TEMP, "21.5", "float", origin + 2_000_000_000, quality=GOOD)
        self.adapter.change(a, TEMP, "22.25", "float", origin + 3_000_000_000, quality={"good": False, "bits": "0x1"})
        self.adapter.change(a, TEMP, "23", "float", origin + 4_000_000_000, quality={"bits": "0x2"})   # no verdict
        self.adapter.change(a, TEMP, "24", "float", origin + 5_000_000_000, quality=GOOD)
        self.adapter.change(a, TEMP, "25", "float", origin + 6_000_000_000)                           # no quality
        self.adapter.change(a, RAW, "7", "int", origin + 1_000_000_000)          # accepted without quality
        self.send()
        self.finish(plan)
        self.env.derive(a)
        with self.env.writer() as w:
            rows = w.execute(f"SELECT seq, value_text, value_json, quality, quality_status FROM {self.env.cfg.schema}"
                             ".observation WHERE kind = 'change' ORDER BY seq").fetchall()
        self.assertEqual(rows[0][1], "-0.0")
        self.assertEqual(rows[0][3], quality)                     # raw attributes as received
        self.assertEqual((rows[1][1], rows[1][2]), ("NaN", None))
        self.assertEqual([r[4] for r in rows], ["good", "good", "good", "bad", "uncertain", "good", "missing",
                                                "missing"])
        reasons = {r[6]: (r[1] is not None, r[2], r[3], r[4]) for r in self.derivations(plan)}
        s = 1                                                                    # seq 0 is the heartbeat
        self.assertEqual(reasons[s][:2], (True, None))                         # -0.0 until the NaN at 1 s
        self.assertEqual(reasons[s][3], 1_000_000_000)
        self.assertEqual(reasons[s + 1][1], "unrepresentable")
        self.assertEqual(reasons[s + 2][3], 3_000_000_000)                      # 21.5 ended by bad quality
        self.assertEqual(reasons[s + 3][1], "bad_quality")
        self.assertEqual(reasons[s + 4][1], "bad_quality")                      # uncertain is not good
        self.assertEqual(reasons[s + 5][3], 6_000_000_000)                      # 24 ended by the missing quality
        self.assertEqual(reasons[s + 6][1], "bad_quality")                      # missing quality: no context
        self.assertEqual(reasons[s + 7][:2], (True, None))                      # ... unless the datapoint accepts it
        vib = {(r[1], r[3]): r[2] for r in self.ctx(plan) if r[0].endswith("-vib")}
        self.assertEqual(vib[("temperature", 0)], 0)                           # jsonb has no -0; the text keeps it
        self.assertEqual(vib[("temperature", expected_samples(8192, 0, 2_000_000_000, 0)[0])], 21.5)
        self.assertEqual(vib[("raw_state", 8192)], 7)

    def test_untimed_values_end_nothing_they_cannot_date(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, SPEED, "1800", "int", origin, quality=GOOD)
        self.adapter.change(a, SPEED, "1200", "int", None, quality=GOOD)                 # no source time
        self.adapter.change(a, SPEED, "1500", "int", None, quality=GOOD, source_status="unreadable")
        self.adapter.change(a, SPEED, "1800", "int", origin + 9_000_000_000, quality=GOOD)
        self.send()
        self.finish(plan)
        self.env.derive(a)
        self.assertEqual([r[2] for r in self.derivations(plan)], ["untimed", "untimed", "untimed", None])
        self.assertEqual([r[3:] for r in self.ctx(plan) if r[0].endswith("-vib")], [(73_728, 131_072)])
        with self.env.writer() as w:
            st = w.execute(f"SELECT source_status FROM {self.env.cfg.schema}.observation WHERE kind = 'change' "
                           "ORDER BY seq").fetchall()
        self.assertEqual([r[0] for r in st], ["ok", "missing", "unreadable", "ok"])


class Boundaries(Base):
    def test_a_value_dated_before_a_closed_boundary_is_out_of_order(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, SPEED, "1800", "int", origin, quality=GOOD)
        self.adapter.change(a, SPEED, "1200", "int", origin + 5_000_000_000, quality={"good": False})
        self.adapter.change(a, SPEED, "1500", "int", origin + 3_000_000_000, quality=GOOD)      # earlier than 5 s
        self.adapter.change(a, SPEED, "1500", "int", origin + 7_000_000_000, quality=GOOD)
        self.send()
        self.finish(plan)
        s = self.env.derive(a)
        self.assertEqual([r[2] for r in self.derivations(plan)], [None, "bad_quality", "out_of_order", None])
        self.assertEqual(s.mismatches, 0)
        self.assertEqual([r[3:] for r in self.ctx(plan) if r[0].endswith("-vib")], [(0, 40_960), (57_344, 131_072)])

    def test_a_batch_the_server_refuses_is_recorded_and_others_still_added(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        with self.env.writer() as w:        # another writer already stored speed for [4 s, 6 s) on vib
            w.execute("SELECT morlet_add_context(%s::regclass, 'other-writer', %s)",
                      (self.env.collection, json.dumps([{"recording_id": recording_id(plan, "vib"), "key": "speed",
                                                         "value": 1000, "start_sample": 32_768,
                                                         "end_sample": 49_152}])))
            w.commit()
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, SPEED, "1800", "int", origin, quality=GOOD)
        self.adapter.change(a, SPEED, "1200", "int", origin + 3_000_000_000, quality=GOOD)
        self.adapter.change(a, LOAD, '"low"', "string", origin, quality=GOOD)
        self.send()
        self.finish(plan)
        self.env.derive(a)
        d = self.derivations(plan)
        self.assertEqual([(r[0], r[2]) for r in d], [("speed", None), ("speed", "rejected"), ("load", None)])
        self.assertIn("overlap", self._detail(d[1][6]))
        s = self.env.derive(a)              # stays recorded; no retry loop, no error, no mismatch
        self.assertEqual(s.mismatches, 0)
        self.assertEqual([r[2] for r in self.derivations(plan)], [None, "rejected", None])

    def _detail(self, seq):
        with self.env.writer() as w:
            return w.execute(f"SELECT d.detail FROM {self.env.cfg.schema}.derivation d JOIN "
                             f"{self.env.cfg.schema}.observation o USING (obs_id) WHERE o.seq = %s",
                             (seq,)).fetchone()[0]


class ObservedBasis(Base):
    datapoints = EXTRA + (cfgmod.Datapoint(OBSERVED, "rig-1", "mode", time_basis="observed"),)
    clock = cfgmod.Clock(allow_observed_time_basis=True)

    def test_observed_time_basis_is_named(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, OBSERVED, '"auto"', "string", None, observed_ns=origin + 2_000_000_000, quality=GOOD)
        self.send()
        self.finish(plan)
        self.env.derive(a)
        d = self.derivations(plan)
        self.assertEqual((d[0][2], d[0][3], d[0][5]), (None, 2_000_000_000, "observed"))


class Idempotency(Base):
    def test_retries_restarts_and_rederivation_add_nothing(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        self.adapter.heartbeat(a, "rig-1")
        for i, v in enumerate((1800, 1200, 1800)):
            self.adapter.change(a, SPEED, str(v), "int", origin + i * 4_000_000_000, quality=GOOD)
        sent = list(self.adapter.unacked)
        r = self.send()
        self.assertEqual((r.body["stored"], r.body["duplicates"], r.body["highest_seq"]), (4, 0, 3))
        # Acknowledged means committed: another session sees every observation.
        with self.env.connect() as c:
            self.assertEqual(c.execute(f"SELECT count(*) FROM {self.env.cfg.schema}.observation").fetchone()[0], 4)
        # The acknowledgement was lost: the adapter resends.
        self.adapter.unacked = list(sent)
        r = self.send()
        self.assertEqual((r.body["stored"], r.body["duplicates"], r.body["highest_seq"]), (0, 4, 3))
        self.finish(plan)
        self.env.derive(a)
        first = self.ctx(plan)
        self.assertEqual(len(first), 6)
        # Derive again, from another process, and after a companion restart: nothing new.
        self.env.derive(a)
        self.env.stop()
        adapter = self.env.start()
        adapter.session = self.adapter.session
        adapter.unacked = list(sent)
        r = adapter.flush()[-1]
        self.assertEqual((r.status, r.body["stored"], r.body["duplicates"]), (200, 0, 4))
        self.env.derive(a)
        self.assertEqual(self.ctx(plan), first)
        # A restarted adapter (new session) sends the current value again as a snapshot: no new state.
        fresh = adapter
        fresh.session = f"sim-{uuid.uuid4()}"
        fresh.seq = 0
        fresh.heartbeat(a, "rig-1")
        fresh.change(a, SPEED, "1800", "int", origin + 8_000_000_000, quality=GOOD, kind="snapshot")
        fresh.change(a, SPEED, "1800", "int", origin + 8_000_000_000, quality=GOOD, kind="snapshot")
        self.assertEqual(fresh.flush()[-1].status, 200)
        self.env.derive(a)
        self.assertEqual(self.ctx(plan), first)
        with self.env.writer() as w:
            n = w.execute(f"SELECT count(*) FROM {self.env.collection}_ctx WHERE batch_id LIKE %s",
                          (f"winccoa:{a}:%",)).fetchone()[0]
        self.assertEqual(n, 6)

    def test_conflicting_retry_is_refused_and_stores_nothing(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        o = self.adapter.change(a, SPEED, "1800", "int", origin, quality=GOOD)
        self.send()
        # Same (session, seq), different body; with a new observation in the same message.
        self.adapter.unacked = [dict(o, value_text="1200"), {**o, "seq": 1, "value_text": "1500"}]
        r = self.send(409)
        self.assertEqual(r.body["seqs"], [0])
        with self.env.writer() as w:
            rows = w.execute(f"SELECT seq, value_text FROM {self.env.cfg.schema}.observation ORDER BY seq").fetchall()
        self.assertEqual(rows, [(0, "1800")])

    def test_unknown_acquisition_and_names(self):
        plan, origin = self.acquisition()
        self.adapter.change("no-such-acquisition", SPEED, "1", "int", origin, quality=GOOD)
        self.assertEqual(self.send(422).body["error"], "unknown_acquisition")
        self.adapter.unacked.clear()
        self.adapter.change(plan.acquisition_id, "System1:Other.x", "1", "int", origin, quality=GOOD)
        self.assertEqual(self.send(403).body["error"], "not_configured")
        self.adapter.unacked.clear()
        with self.env.writer() as w:      # an acquisition of another machine in the same collection
            w.execute("SELECT morlet_create_acquisition(%s::regclass, 'other-machine-acq', 'rig-2', %s, '{}')",
                      (self.env.collection, origin_utc(origin)))
            w.commit()
        self.adapter.change("other-machine-acq", SPEED, "1", "int", origin, quality=GOOD)
        self.assertEqual(self.send(422).body["error"], "unknown_acquisition")
        with self.env.writer() as w:
            self.assertEqual(w.execute(f"SELECT count(*) FROM {self.env.cfg.schema}.observation").fetchone()[0], 0)


class Gaps(Base):
    def test_gap_ends_state_at_last_contact_and_reads_as_missing(self):
        plan, origin = self.acquisition(ago_s=6)            # in progress: now is about 6 s into it
        a = plan.acquisition_id
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, SPEED, "1800", "int", origin + 1_000_000_000, quality=GOOD)
        last = self.adapter.heartbeat(a, "rig-1")
        self.send()
        time.sleep(0.3)
        # The adapter lost the connection and reconnects: a gap, then the initial values as snapshots.
        self.adapter.gap(a, "rig-1")
        snap = self.adapter.change(a, SPEED, "1800", "int", origin + 1_000_000_000, quality=GOOD, kind="snapshot")
        self.adapter.change(a, SPEED, "1800", "int", origin + 1_000_000_000, quality=GOOD, kind="snapshot")   # no-op
        self.adapter.heartbeat(a, "rig-1")
        self.send()
        self.finish(plan)
        self.env.derive(a)
        d = self.derivations(plan)
        gap_end = int(last["observed_ns"]) - origin
        snap_t = int(snap["observed_ns"]) - origin              # max(source time, reconnect time)
        self.assertEqual((d[0][2], d[0][3], d[0][4]), (None, 1_000_000_000, gap_end))
        self.assertEqual((d[1][2], d[1][3], d[1][4]), (None, snap_t, plan.end_ns))
        self.assertEqual(d[2][2], "unchanged")
        vib = [r[3:] for r in self.ctx(plan) if r[0].endswith("-vib")]
        hole = (expected_samples(8192, 0, 0, gap_end)[1], expected_samples(8192, 0, snap_t, 0)[0])
        self.assertEqual(vib, [(8192, hole[0]), (hole[1], 131_072)])
        self.assertLess(hole[0], hole[1])                      # the outage has no context: missing evidence


class ClockBound(Base):
    def test_exceeded_clock_stops_derivation_and_degrades_health(self):
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        self.adapter.change(a, SPEED, "1800", "int", origin, quality=GOOD)                  # before any heartbeat
        self.adapter.heartbeat(a, "rig-1", observed_ns=time.time_ns() - 1_000_000_000)     # 1 s off
        self.adapter.change(a, SPEED, "1200", "int", origin + 2_000_000_000, quality=GOOD)
        self.adapter.change(a, SPEED, "1800", "int", origin + 4_000_000_000, quality=GOOD)
        self.send()
        health = self.env.client.request("GET", "/v1/health").body
        self.assertEqual((health["status"], health["assets"][0]["clock"]["state"]), ("degraded", "exceeded"))
        self.adapter.heartbeat(a, "rig-1", rtt_ns=2_000_000)                                 # back within 50 ms
        self.adapter.change(a, SPEED, "1200", "int", origin + 6_000_000_000, quality=GOOD)
        self.adapter.change(a, SPEED, "1800", "int", origin + 8_000_000_000, quality=GOOD)
        self.send()
        self.assertEqual(self.env.client.request("GET", "/v1/health").body["assets"][0]["clock"]["state"], "ok")
        self.finish(plan)
        self.env.derive(a)
        reasons = [r[2] for r in self.derivations(plan)]
        self.assertEqual(reasons, ["clock_exceeded", "clock_exceeded", "clock_exceeded", None, None])
        # Stored all the same.
        with self.env.writer() as w:
            self.assertEqual(w.execute(f"SELECT count(*) FROM {self.env.cfg.schema}.observation").fetchone()[0], 7)


class StartAndChannels(Base):
    def _acq(self, start_skew_ns=0, roles=("vib",)):
        plan = dataclasses.replace(current(), acquisition_id=f"acq-{uuid.uuid4().hex[:8]}")
        origin = (time.time_ns() // 1000) * 1000 - plan.end_ns
        from machine import synthesize
        data = synthesize(plan)
        with self.env.writer() as w:
            db = morletdb.Client(w)
            w.execute("SELECT morlet_create_acquisition(%s::regclass, %s, 'rig-1', %s, '{}')",
                      (self.env.collection, plan.acquisition_id, origin_utc(origin)))
            for role in roles:
                self._channel(w, db, plan, origin, data, role, start_skew_ns)
            w.commit()
        return plan, origin, data

    def _channel(self, w, db, plan, origin, data, role, skew=0):
        rate, channel, unit, offset, clock, unc = CHANNELS[role]
        db.ingest(self.env.collection, recording_id(plan, role), data[role], sample_rate_hz=rate, channel=channel,
                  unit=unit, start_time=origin + offset + skew)
        w.execute("SELECT morlet_add_acquisition_channel(%s::regclass, %s, %s, %s, %s, %s, %s)",
                  (self.env.collection, plan.acquisition_id, recording_id(plan, role), role, offset, clock, unc))

    def _context(self, plan, origin):
        a = plan.acquisition_id
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, SPEED, "1800", "int", origin, quality=GOOD)
        self.adapter.change(a, SPEED, "1200", "int", origin + 5_000_000_000, quality=GOOD)
        self.send()
        self.finish(plan)

    def test_inconsistent_start_derives_nothing(self):
        plan, origin, _ = self._acq(start_skew_ns=1000)
        self._context(plan, origin)
        s = self.env.derive(plan.acquisition_id)
        self.assertIn("start_ns differs", s.inconsistent_start)
        self.assertEqual([r[2] for r in self.derivations(plan)], ["inconsistent_start"] * 2)
        self.assertEqual(self.ctx(plan), [])

    def test_late_channel_gets_existing_batches_once(self):
        plan, origin, data = self._acq(roles=("vib",))
        self._context(plan, origin)
        self.env.derive(plan.acquisition_id)
        self.assertEqual({r[0] for r in self.ctx(plan)}, {recording_id(plan, "vib")})
        vib_before = self.ctx(plan)
        with self.env.writer() as w:
            self._channel(w, morletdb.Client(w), plan, origin, data, "current")
            w.commit()
        from morlet_winccoa import derive
        with self.env.connect() as c:
            self.assertIn((self.env.collection, plan.acquisition_id), derive.pending_acquisitions(c, self.env.cfg))
            derive.derive_all(c, self.env.cfg)
            self.assertEqual(derive.pending_acquisitions(c, self.env.cfg), [])
        rows = self.ctx(plan)
        self.assertEqual([r for r in rows if r[0].endswith("-vib")], vib_before)
        self.assertEqual(sorted(r for r in rows if r[0].endswith("-current")), sorted(
            (recording_id(plan, "current"), "speed", v, *expected_samples(1000, 2_000_000, t0, t1))
            for t0, t1, v in ((0, 5_000_000_000, 1800), (5_000_000_000, plan.end_ns, 1200))))
        self.env.derive(plan.acquisition_id)
        self.assertEqual(self.ctx(plan), rows)


class Background(Base):
    def test_the_service_derives_after_acknowledging(self):
        self.env.companion.start_deriver()
        plan, origin = self.acquisition()
        a = plan.acquisition_id
        self.finish(plan)
        self.adapter.heartbeat(a, "rig-1")
        self.adapter.change(a, LOAD, '"high"', "string", origin, quality=GOOD)
        self.send()
        deadline = time.monotonic() + 10
        while not self.ctx(plan) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(len(self.ctx(plan)), 2)


if __name__ == "__main__":
    unittest.main()
