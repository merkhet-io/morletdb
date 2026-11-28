"""B9 block runner: a deferred retry is its own attempt, and a hash difference stops the study. Stub results only."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import b9  # noqa: E402
import b9blocks  # noqa: E402


def result(digest="h"):
    return {"samples": 1000, **{c: {"sha256": digest, **{key: 1.0 for _, key in b9.OPS}} for c in b9blocks.CONFIGS}}


def good(digest="h"):
    return {"valid": True, "invalid_reason": None, "stop": False, "result": result(digest)}


class Blocks(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = os.path.join(self.dir.name, "passes.jsonl")

    def rows(self):
        with open(self.out) as f:
            return [json.loads(line) for line in f]

    def test_a_deferred_retry_after_a_partial_attempt_has_its_own_identity(self):
        calls = []
        def do_pass(name, path, arm, tag, run, index, attempt):
            calls.append(tag)
            return None if len(calls) == 2 else good()
        self.assertEqual(b9blocks.run_blocks(self.out, "mac", "t", [("x", "x")], "AB", 1, do_pass, lambda _: None), "done")
        rows = self.rows()
        self.assertEqual([r["attempt"] for r in rows if r.get("block") == "set aside"], [1])
        self.assertEqual([r["attempt"] for r in rows if r.get("block") == "complete"], [2])
        self.assertEqual(len(set(calls)), 2)      # artifacts of the two attempts do not overwrite each other

    def test_a_hash_difference_stops_at_the_first_mismatch(self):
        calls = []
        def do_pass(name, path, arm, tag, run, index, attempt):
            calls.append(arm)
            return good("a" if arm == "A" else "b")
        with self.assertRaises(b9blocks.Stop):
            b9blocks.run_blocks(self.out, "mac", "t", [("x", "x")], "ABBA", 3, do_pass, lambda _: None)
        rows = self.rows()
        self.assertEqual(calls, ["A", "A", "A", "B"])
        self.assertTrue(any(r.get("block") == "STOP" for r in rows))
        self.assertFalse(any(r.get("block") == "complete" for r in rows))


if __name__ == "__main__":
    unittest.main()
