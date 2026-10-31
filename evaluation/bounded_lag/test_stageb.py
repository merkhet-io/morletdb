#!/usr/bin/env python3
"""Stage B bookkeeping checks (no database)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stageb as S  # noqa: E402


class Windows(unittest.TestCase):
    def test_completed_windows_on_the_stride_grid(self):
        self.assertEqual([S.completed_windows(e) for e in (0, 4095, 4096, 6143, 6144, 8192)], [0, 0, 1, 1, 2, 3])


class Delays(unittest.TestCase):
    def test_paced_completion_and_worker_processing(self):
        # one recording, chunks of 4096 committed 0.1 s after the arrival of their last sample
        commits = [{"rid": "r", "end": e, "t": e / S.PACE + 0.1, "arrival": e / S.PACE} for e in (4096, 8192)]
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "w.jsonl"
            log.write_text("\n".join(json.dumps({"rid": "r", "max_start": m, "t": t}) for m, t in ((0, 1.0), (4096, 2.0))))
            out = S.delays(commits, log, paced=True)
        # windows 0 (end 4096), 1 (end 6144, completed by the 8192 chunk), 2 (end 8192)
        self.assertEqual(out["windows_without_visibility_log"], 0)
        self.assertAlmostEqual(out["completion_delay_s"]["max"], 0.1 + 2048 / S.PACE)
        self.assertAlmostEqual(out["processing_delay_s"]["max"], 2.0 - (8192 / S.PACE + 0.1))

    def test_synchronous_mode_has_zero_processing_delay(self):
        commits = [{"rid": "r", "end": 4096, "t": 1.0, "arrival": None}]
        self.assertEqual(S.delays(commits, None, paced=False)["processing_delay_s"]["max"], 0.0)


if __name__ == "__main__":
    unittest.main()
