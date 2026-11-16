"""Regression tests for the submission's analysis corrections; no database."""
import gzip
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation"))
from analysis_io import read_json, spearman


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class AnalysisTests(unittest.TestCase):
    def test_average_ties_and_constants(self):
        self.assertTrue(math.isnan(spearman([1, 2, 3], [7, 7, 7])))
        self.assertAlmostEqual(spearman([1, 2, 3], [1, 1, 2]), math.sqrt(3) / 2)
        self.assertAlmostEqual(spearman([1, 2, 3], [3, 2, 1]), -1)
        self.assertAlmostEqual(spearman([3, 1, 2], [2, 1, 1]), math.sqrt(3) / 2)

    def test_gzip_and_missing_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sample.json"
            with gzip.open(str(p) + ".gz", "wt") as f:
                json.dump({"value": 3}, f)
            self.assertEqual(read_json(p), {"value": 3})
            with self.assertRaises(FileNotFoundError):
                read_json(Path(d) / "missing.json")

    def test_missing_input_preserves_output(self):
        with tempfile.TemporaryDirectory() as d:
            result = Path(d) / "summary.json"
            result.write_text('{"published": true}')
            env = dict(os.environ, MORLET_ANALYSIS_INPUT=d, MORLET_ANALYSIS_OUTPUT=d)
            run = subprocess.run([sys.executable, "-I", str(ROOT / "evaluation/industrial_iu1/analyze.py")],
                                 env=env, capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(json.loads(result.read_text()), {"published": True})

    def test_archived_t6_is_not_claimed_as_corrected_search(self):
        m = module("iu1", "evaluation/industrial_iu1/analyze.py")
        result = m.t6()
        self.assertAlmostEqual(result["BL"], .470)
        self.assertEqual(result["short_results"], {"BL": 47, "ZL": 5})
        self.assertEqual(result["empty_results"], {"BL": 13, "ZL": 1})
        self.assertIn("rerun required", result["status"])

    def test_failure_cycle_is_not_advance_warning(self):
        m = module("iu2", "evaluation/industrial_iu2/iu2_analyze.py")
        m.S = m.load_scores(cycle_scores=True)
        w = m.w3()
        self.assertEqual(w["BL"]["tools_flagged"], 1)
        self.assertEqual(w["BL"]["tools_first_flagged_in_failure_cycle"], 1)
        self.assertEqual(w["VIB"]["tools_flagged"], 6)
        self.assertEqual(w["VIB"]["tools_first_flagged_in_failure_cycle"], 1)
        self.assertEqual(w["RMSW"]["tools_flagged"], 7)
        for a in w.values():
            self.assertEqual(a["tools_flagged"] + a["tools_first_flagged_in_failure_cycle"] + a["tools_never_flagged"], 7)


if __name__ == "__main__":
    unittest.main()
