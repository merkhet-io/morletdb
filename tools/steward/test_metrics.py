"""The metrics text format, without a database (the database side is in test_steward_db.py)."""
import unittest

import metrics as M


class TextFormat(unittest.TestCase):
    def test_help_type_and_samples_in_first_seen_order(self):
        out = M.render([("morlet_up", {}, 1), ("morlet_steward_breaker_open", {"job": "envelopes"}, 0),
                        ("morlet_steward_breaker_open", {"job": "verify"}, 1)])
        self.assertEqual(out.splitlines(), [
            "# HELP morlet_up 1 when the last scrape reached the database", "# TYPE morlet_up gauge", "morlet_up 1",
            "# HELP morlet_steward_breaker_open 1 when a job's circuit breaker is open",
            "# TYPE morlet_steward_breaker_open gauge",
            'morlet_steward_breaker_open{job="envelopes"} 0', 'morlet_steward_breaker_open{job="verify"} 1'])

    def test_label_values_are_escaped(self):
        out = M.render([("morlet_table_dead_tuples", {"table": 'public."we\\ird\n"'}, 3)])
        self.assertIn('morlet_table_dead_tuples{table="public.\\"we\\\\ird\\n\\""} 3', out)

    def test_values(self):
        self.assertEqual([M.format_value(v) for v in (0, 3, 2.5, 1e20, float("nan"), float("inf"))],
                         ["0", "3", "2.5", "1e+20", "NaN", "+Inf"])

    def test_an_unreachable_database_reports_only_morlet_up_0(self):
        c = M.Collector("host=127.0.0.1 port=1 dbname=none connect_timeout=1")
        self.assertEqual(c.scrape(), "# HELP morlet_up 1 when the last scrape reached the database\n"
                                     "# TYPE morlet_up gauge\nmorlet_up 0\n")


if __name__ == "__main__":
    unittest.main()
