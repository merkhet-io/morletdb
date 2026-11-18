"""Unit tests of the steward's decisions (no database): python -m unittest discover -s tools/steward"""
import unittest

import policy as P

GB = 1 << 30
CFG = P.Config()


def disk(free_gb, growth_gb_per_day, total_gb=100):
    return P.disk_state(CFG, P.Disk(int(total_gb * GB), int(free_gb * GB),
                                    None if growth_gb_per_day is None else growth_gb_per_day * GB))


class DiskLadder(unittest.TestCase):
    def test_levels_follow_time_remaining(self):
        # reserve: max(5% of 100 GB, 2 GB) = 5 GB
        self.assertEqual(disk(50, 1).level, P.NORMAL)                  # 45 days
        d = disk(15, 1)                                                # 10 days
        self.assertEqual((d.level, d.pause), (P.ATTENTION, False))
        self.assertIn("10 days", d.cause)
        d = disk(7, 1)                                                 # 2 days: pause
        self.assertEqual((d.level, d.pause, d.drop_derived, d.hold_ingest), (P.ACTION, True, False, False))
        d = disk(5.5, 1)                                               # 12 h: may drop derived data
        self.assertEqual((d.pause, d.drop_derived, d.hold_ingest), (True, True, False))
        self.assertIn("12 h", d.cause)
        d = disk(4, 1)                                                 # reserve reached: hold ingest
        self.assertEqual((d.level, d.hold_ingest), (P.ACTION, True))

    def test_not_growing_or_unknown(self):
        self.assertEqual(disk(10, None).level, P.NORMAL)
        self.assertEqual(disk(10, 0).level, P.NORMAL)
        u = P.disk_state(CFG, P.Disk(None, None, 1.0))
        self.assertEqual((u.level, u.unknown, u.pause), (P.ACTION, True, True))
        self.assertTrue(disk(1, None).hold_ingest)                     # below the reserve even without growth

    def test_reserve_is_the_larger_of_share_and_bytes(self):
        self.assertEqual(P.reserve(CFG, 10 * GB), 2 * GB)
        self.assertEqual(P.reserve(CFG, 1000 * GB), 50 * GB)

    def test_growth_from_samples(self):
        self.assertIsNone(P.growth_per_day([(0, 100)]))
        self.assertIsNone(P.growth_per_day([(0, 100), (60, 200)]))    # less than an hour of history
        self.assertAlmostEqual(P.growth_per_day([(0, 0), (43200, 5 * GB)]), 10 * GB)
        self.assertEqual(P.growth_per_day([(0, 5 * GB), (86400, GB)]), 0.0)   # shrinking counts as no growth


class Gating(unittest.TestCase):
    def test_due_verification_gets_its_minimum_share_under_load(self):
        """Soak S1: under continuous load and ingest, verification never ran. Now a due verification gets one batch
        every verify_share_every_s, before the builders and despite the load gate; disk pressure still stops it."""
        cfg = P.Config(verify_share_every_s=900)
        fine = P.disk_state(cfg, P.Disk(100 << 30, 50 << 30, None))
        self.assertFalse(P.may_work(cfg, 3.05, 2, fine)[0])               # the load gate is closed
        self.assertTrue(P.verify_turn(cfg, True, None, fine))             # first batch after start
        self.assertTrue(P.verify_turn(cfg, True, 900, fine))
        self.assertFalse(P.verify_turn(cfg, True, 899, fine))             # not more often than its share
        self.assertFalse(P.verify_turn(cfg, False, None, fine))           # nothing due
        pressed = P.disk_state(cfg, P.Disk(100 << 30, (2 << 30) + (1 << 20), 10 << 30))
        self.assertTrue(pressed.pause)
        self.assertFalse(P.verify_turn(cfg, True, None, pressed))         # disk pressure stops it
        unknown = P.disk_state(cfg, P.Disk(None, None, None))
        self.assertFalse(P.verify_turn(cfg, True, None, unknown))
        backlog = {"envelopes": True, "context_values": True}
        self.assertEqual(P.next_job(backlog, {}, True), "envelopes")      # without its turn, builders first
        self.assertEqual(P.next_job(backlog, {}, True, verify_first=True), "verify")
        self.assertEqual(P.next_job(backlog, {"verify": P.Breaker(open=True)}, True, verify_first=True), "envelopes")
        self.assertEqual(P.next_job({}, {}, False, verify_first=True), None)

    def test_missing_summaries_are_backlog_not_damage(self):
        damage, pending = P.integrity_problems([
            "chunk at 0: summary missing", "chunk at 32768: summary missing",
            "chunk at 65536: CRC-32C mismatch", "chunk at 98304: summary does not match the samples",
            "gap: samples 10..20 have no chunk", "chunks end at 5, the checkpoint at 7"])
        self.assertEqual(pending, 2)
        self.assertEqual(len(damage), 4)
        self.assertEqual(P.integrity_problems([]), ([], 0))

    def test_load_and_disk_gate_work(self):
        ok = disk(50, 1)
        self.assertTrue(P.may_work(CFG, 1.0, 4, ok)[0])
        self.assertFalse(P.may_work(CFG, 2.9, 4, ok)[0])               # 0.7 x 4 = 2.8
        self.assertEqual(P.may_work(CFG, 0.1, 4, disk(7, 1)), (False, "disk pressure"))
        self.assertEqual(P.may_work(CFG, 0.1, 4, P.disk_state(CFG, P.Disk(None, None, None)))[1], "disk state unknown")

    def test_breaker_opens_after_repeated_failures_and_resets_on_success(self):
        b = P.Breaker()
        for _ in range(2):
            b.record(CFG, False, "boom")
        self.assertFalse(b.open)
        b.record(CFG, True)
        self.assertEqual(b.failures, 0)
        for _ in range(3):
            b.record(CFG, False, "boom")
        self.assertTrue(b.open)

    def test_next_job_by_priority_skipping_open_breakers(self):
        backlog = {"envelopes": True, "rollups": True, "analyze": True}
        self.assertEqual(P.next_job(backlog, {}, False), "envelopes")
        self.assertEqual(P.next_job(backlog, {"envelopes": P.Breaker(3, True, "x")}, False), "rollups")
        self.assertEqual(P.next_job({"analyze": True}, {}, True), "verify")
        self.assertEqual(P.next_job({"analyze": True}, {}, False), "analyze")
        self.assertIsNone(P.next_job({}, {}, False))

    def test_batch_adapts_to_the_budget(self):
        self.assertEqual(P.next_batch(CFG, 64, 5.0), 32)
        self.assertEqual(P.next_batch(CFG, 64, 0.1), 96)
        self.assertEqual(P.next_batch(CFG, 64, 1.0), 64)
        self.assertEqual(P.next_batch(CFG, 1, 9.0), 1)


class Status(unittest.TestCase):
    def test_items_say_cause_consequence_action_and_who(self):
        o = P.Observed(disk(15, 1), {"rollups": P.Breaker(3, True, "permission denied for table x_rollups")},
                       {"public.line": 2}, {"public.line": 1.0, "public.old": None}, ["public.other"])
        items = P.status_items(CFG, o)
        keys = [i.key for i in items]
        self.assertEqual(keys, ["disk", "job:rollups", "verify:public.line", "verify-age:public.old",
                                "privileges:public.other"])
        self.assertTrue(all(i.cause and i.consequence and i.action and i.who for i in items))
        self.assertEqual(P.overall(items), P.ACTION)
        self.assertEqual(P.overall([]), P.NORMAL)
        hold = P.status_items(CFG, P.Observed(disk(4, 1)))[0]
        self.assertIn("refused", hold.cause)


if __name__ == "__main__":
    unittest.main()
