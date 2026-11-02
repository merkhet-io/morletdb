"""morlet-mqtt/1 payload checks and the deterministic test signal (no broker or database)."""
import unittest

import contract as c


def batch(**kw):
    p = {"schema": c.SCHEMA, "acquisition": "A1", "channel": "vib", "unit": "g", "sample_rate_hz": 5120.0, "seq": 0,
         "start_sample": 0, "samples": [0.1, 0.2]}
    p.update(kw)
    return p


class Contract(unittest.TestCase):
    def test_valid_and_invalid_batches(self):
        self.assertEqual(c.check_samples(batch())["seq"], 0)
        for bad in (dict(schema="other/1"), dict(start_sample=-1), dict(samples=[]), dict(samples=["x"]),
                    dict(sample_rate_hz=0), dict(acquisition="")):
            with self.assertRaises(ValueError):
                c.check_samples(batch(**bad))
        p = batch(); del p["unit"]
        with self.assertRaises(ValueError):
            c.check_samples(p)

    def test_context(self):
        ok = {"schema": c.SCHEMA, "acquisition": "A1", "key": "speed", "value": 1500, "source_time": "2026-01-01T00:00:01Z"}
        self.assertEqual(c.check_context(ok)["key"], "speed")
        with self.assertRaises(ValueError):
            c.check_context({**ok, "source_time": "yesterday"})

    def test_signal_is_deterministic_per_batch(self):
        self.assertEqual(c.signal(1024, 512).tolist(), c.signal(1024, 512).tolist())
        self.assertNotEqual(c.signal(0, 512).tolist(), c.signal(512, 512).tolist())


if __name__ == "__main__":
    unittest.main()
