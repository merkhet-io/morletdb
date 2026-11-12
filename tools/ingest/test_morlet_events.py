"""morlet_events.py without a database: reading a message log and placing messages on recordings (#421)."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import morlet_events as me


class Events(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def recs(self):
        t0 = datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)
        return [{"recording_id": "m1.spindle", "rate": 1000.0, "samples": 10000, "t0": t0,
                 "metadata": {"machine": "M1", "time_axis": {"max_offset_s": 0.168}}},
                {"recording_id": "m2.spindle", "rate": 1000.0, "samples": 10000, "t0": t0,
                 "metadata": {"machine": "M2"}}]

    def test_messages_land_on_the_right_sample_of_the_right_machine(self):
        p = self.tmp / "alarms.csv"
        p.write_text("time,machine,code,text\n"
                     "2026-03-01T08:00:02.3456Z,M1,2041,spindle overload\n"
                     "2026-03-01T08:00:05,M2,17,door open\n"
                     "2026-03-01T09:00:00Z,M1,1,after the recording\n")
        msgs = me.read_messages(p, "time", ["code", "text"], "machine")
        per_rec, outside = me.place(msgs, self.recs(), "alarm", "machine")
        self.assertEqual(outside, 1)
        (ev,) = per_rec["m1.spindle"]
        self.assertEqual((ev["key"], ev["start_sample"]), ("alarm", 2345))     # floor(2.3456 s x 1000 Hz)
        self.assertEqual(ev["value"]["code"], "2041")
        self.assertEqual(ev["value"]["time_uncertainty_s"], 0.168)            # the recording's measured drift
        self.assertNotIn("end_sample", ev)                                    # an event, not an interval
        (ev2,) = per_rec["m2.spindle"]
        self.assertEqual(ev2["start_sample"], 5000)                           # no offset given: UTC
        self.assertNotIn("time_uncertainty_s", ev2["value"])

    def test_without_match_a_message_goes_on_every_recording_it_falls_into(self):
        p = self.tmp / "plc.csv"
        p.write_text("t,msg\n2026-03-01T08:00:01Z,program start\n")
        per_rec, outside = me.place(me.read_messages(p, "t", ["msg"], None), self.recs(), "plc", None)
        self.assertEqual((sorted(per_rec), outside), (["m1.spindle", "m2.spindle"], 0))

    def test_bad_logs_are_refused(self):
        p = self.tmp / "bad.csv"
        p.write_text("t,msg\nyesterday,x\n")
        with self.assertRaisesRegex(ValueError, "ISO 8601"):
            me.read_messages(p, "t", ["msg"], None)
        with self.assertRaisesRegex(ValueError, "no column"):
            me.read_messages(p, "t", ["code"], None)


if __name__ == "__main__":
    unittest.main()
