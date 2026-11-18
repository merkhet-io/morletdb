#!/usr/bin/env python3
"""measure_b6.py's stops, on synthetic inputs only: G1 different from B4's and B5's Grizzbit 1 (one sample changed in
an input with a B4/B5 name) stops storage with exit 3; a chunk file that differs from its manifest stops it too.

  .venv-b3/bin/python -I evaluation/storage_b6/test_measure_b6.py      (needs bin/b6 from setup_b6.sh)
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prepare_b6 as p  # noqa: E402


def work_with(tmp, name, x):
    w = Path(tmp)
    (w / "inputs").mkdir(parents=True)
    lengths = p.chunked(x)
    sha = p.write_chunks(x, lengths, w / "inputs" / f"{name}.bin")
    m = {"set": "test", "chunk": 4096, "quantum": 1.0,
         "inputs": [{"input": name, "set": "elf", "samples": int(x.size), "chunks": int(lengths.size),
                     "chunk_lengths": {}, "sha256": hashlib.sha256(x.tobytes()).hexdigest(),
                     "chunk_file": f"inputs/{name}.bin", "chunk_file_sha256": sha, "source": {}}]}
    (w / "inputs.json").write_text(json.dumps(m))
    return w


def run(work, out):
    return subprocess.run([sys.executable, "-I", str(HERE / "measure_b6.py"), "storage", str(work), str(out)],
                          capture_output=True, text=True)


class Stops(unittest.TestCase):
    def test_g1_mismatch_with_b4_and_b5_stops(self):
        # A synthetic series under a name B4 and B5 measured: its sha256 and bytes cannot match theirs.
        x = np.round(np.cumsum(np.ones(5000)) * 0.01, 2)
        with tempfile.TemporaryDirectory() as t:
            w = work_with(Path(t) / "w", "Stocks-USA", x)
            r = run(w, Path(t) / "out.json")
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            res = json.loads((Path(t) / "out.json").read_text())
            self.assertIn("G1 differs from B4 input, B4 M-auto bytes, B5 G1 bytes", res["stopped"])

    def test_unknown_input_has_no_reference_and_passes(self):
        x = np.round(np.cumsum(np.ones(5000)) * 0.01, 2)
        with tempfile.TemporaryDirectory() as t:
            w = work_with(Path(t) / "w", "synthetic", x)
            r = run(w, Path(t) / "out.json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            row = json.loads((Path(t) / "out.json").read_text())["inputs"][0]
            self.assertTrue(row["exact"] and row["bench_example_agrees"])
            self.assertNotIn("b4", row)

    def test_changed_chunk_file_stops(self):
        x = np.arange(5000.0)
        with tempfile.TemporaryDirectory() as t:
            w = work_with(Path(t) / "w", "synthetic", x)
            f = w / "inputs/synthetic.bin"
            b = bytearray(f.read_bytes())
            b[20] ^= 1
            f.write_bytes(bytes(b))
            r = run(w, Path(t) / "out.json")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("chunk file differs from the manifest", r.stderr)


if __name__ == "__main__":
    unittest.main()
