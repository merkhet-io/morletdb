import unittest
from datetime import datetime, timedelta, timezone

from context_rows import context_rows, first_sample

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Placement(unittest.TestCase):
    def test_a_change_on_a_sample_starts_there(self):
        self.assertEqual(first_sample(T0 + timedelta(seconds=1), T0, 25600.0), 25600)
        self.assertEqual(first_sample(T0, T0, 25600.0), 0)

    def test_a_change_between_samples_starts_at_the_next_one(self):
        # 1 kHz: 10.4 ms and 10.6 ms are both before sample 11; rounding would put the first at sample 10
        self.assertEqual(first_sample(T0 + timedelta(microseconds=10_400), T0, 1000.0), 11)
        self.assertEqual(first_sample(T0 + timedelta(microseconds=10_600), T0, 1000.0), 11)

    def test_exact_for_rates_that_are_not_whole_numbers(self):
        # 3 samples of 1/12.5 s are exactly 0.24 s; floating point would give 3.0000000000000004 and ceil 4
        self.assertEqual(first_sample(T0 + timedelta(microseconds=240_000), T0, 12.5), 3)

    def test_before_the_start_is_negative(self):
        self.assertLess(first_sample(T0 - timedelta(milliseconds=5), T0, 1000.0), 0)


class Rows(unittest.TestCase):
    def test_the_value_is_the_state_alone(self):
        a = context_rows("r", "state", "running", "good", 10, good="good")
        b = context_rows("r", "state", "running", "good", 99_000, good="good")
        self.assertEqual([r["value"] for r in a], ["running"])
        self.assertEqual(a[0]["value"], b[0]["value"])   # same state, same segment context (#480)

    def test_a_status_that_is_not_good_is_its_own_event(self):
        rows = context_rows("r", "speed", 1500.0, "uncertain", 7, good="good")
        self.assertEqual([(r["key"], r["value"], r["start_sample"]) for r in rows],
                         [("speed", 1500.0, 7), ("speed.status", "uncertain", 7)])

    def test_no_status_means_no_status_event(self):
        self.assertEqual(len(context_rows("r", "speed", 1.0, None, 0)), 1)


if __name__ == "__main__":
    unittest.main()
