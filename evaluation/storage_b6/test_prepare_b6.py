#!/usr/bin/env python3
"""Unit tests of prepare_b6.py's exclusion and chunking logic, on synthetic data only (no benchmark input).

  .venv-b3/bin/python -I evaluation/storage_b6/test_prepare_b6.py
"""
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_b6 as p  # noqa: E402


def blk(unit, ident, n, start):
    return {"unit": unit, "id": ident, "x": np.arange(start, start + n, dtype=np.int64)}


class FakeLoader:
    """B1's datasets.paderborn and paderborn_mat.load_file, synthetic: blocks in loader order, physical = counts / 8."""

    def __init__(self, blocks):
        self.blocks = blocks
        self.ds = types.SimpleNamespace(paderborn=self.paderborn)
        self.pm = types.SimpleNamespace(load_file=self.load_file)
        self.loaded = []

    def paderborn(self, role, root=None):
        assert role == "heldout"
        yield from self.blocks

    def load_file(self, path):
        unit, ident = Path(path).parent.name, Path(path).stem
        self.loaded.append(f"{unit}/{ident}")
        b = next(b for b in self.blocks if b["unit"] == unit and b["id"] == ident)
        vib = np.concatenate([b["x"] / 8.0, [1e6] * 10])   # longer than x: truncated to len(x), as p2_dump does
        return None, None, None, vib, {"rate_ok": True}


class Exclusion(unittest.TestCase):
    def test_development_bearings(self):
        self.assertEqual(p.DEV_BEARINGS, ("K001", "KA01", "KA09", "KB24", "KI08"))
        for u in p.DEV_BEARINGS:
            self.assertFalse(p.keep_unit(u))
        for u in ("K002", "KA22", "KI21", "KB23", "KA09x", "ka09", ""):
            self.assertTrue(p.keep_unit(u))

    def test_b3_files_by_bearing_directory(self):
        paths = [Path("/d/K001/N09_M07_F10_K001_1.mat"), Path("/d/K002/N09_M07_F10_K002_1.mat"),
                 Path("/d/KA09/N15_M07_F04_KA09_19.mat"), Path("/d/KI21/x.mat"), Path("/d/KB24/y.mat"),
                 Path("/d/KA01/z.mat"), Path("/d/KI08/w.mat"), Path("/d/KA22/v.mat")]
        self.assertEqual(p.b3_keep(paths), [paths[1], paths[3], paths[7]])
        # order is kept
        self.assertEqual(p.b3_keep(list(reversed(paths))), [paths[7], paths[3], paths[1]])

    def test_paderborn_b6_skips_development_bearings_before_chunking(self):
        # 3,000-sample blocks; development bearings interleaved; 1,024 chunks need many blocks
        blocks, start = [], 0
        units = ["K002", "KA09", "KI21", "KB24", "KA04", "KI08", "K001", "KA01", "KA22"]
        for i in range(2400):
            blocks.append(blk(units[i % len(units)], f"f{i}", 3000, start))
            start += 3000
        fake = FakeLoader(blocks)
        (cx, cl), (px, pl), src = p.paderborn_b6(root="/r", modules=(fake.ds, fake.pm))
        self.assertEqual(len(cl), p.P2_CHUNKS)
        self.assertTrue(all(u not in p.DEV_BEARINGS for u in src["units_used"]))
        self.assertEqual(set(src["units_used"]), {"K002", "KI21", "KA04", "KA22"})
        skipped_units = {s.split("/")[0] for s in src["files_skipped_development_bearings"]}
        self.assertEqual(skipped_units, {"KA09", "KB24", "KI08", "K001", "KA01"})
        # the counts are exactly the kept blocks in loader order, cut into 4,096-sample chunks across... per block
        kept = [b for b in blocks if b["unit"] not in p.DEV_BEARINGS]
        want, lens = [], []
        for b in kept:
            for i in range(0, 3000, 4096):
                c = b["x"][i:i + 4096].astype(float)
                want.append(c)
                lens.append(len(c))
                if len(want) == p.P2_CHUNKS:
                    break
            if len(want) == p.P2_CHUNKS:
                break
        np.testing.assert_array_equal(cx, np.concatenate(want))
        np.testing.assert_array_equal(cl, lens)
        # physical: the same samples' physical values, same chunk lengths, from the same files only
        np.testing.assert_array_equal(pl, cl)
        np.testing.assert_array_equal(px, cx / 8.0)
        self.assertTrue(all(f.split("/")[0] not in p.DEV_BEARINGS for f in fake.loaded))
        self.assertEqual(len(src["files_used"]), len(want))


class Chunking(unittest.TestCase):
    def test_first_chunks_matches_p2_dump(self):
        items = [(np.arange(10000.0), "a"), (np.arange(5.0), "b"), (np.arange(9000.0), "c"), (np.arange(7.0), "d")]
        chunks, used = p.first_chunks(items, n_chunks=5, ch=4096)
        self.assertEqual([len(c) for c in chunks], [4096, 4096, 1808, 5, 4096])
        self.assertEqual(used, ["a", "b", "c"])
        chunks, used = p.first_chunks(items, n_chunks=100, ch=4096)
        self.assertEqual(used, ["a", "b", "c", "d"])
        self.assertEqual(sum(len(c) for c in chunks), 10000 + 5 + 9000 + 7)

    def test_every_acquisition_starts_a_new_chunk(self):
        acqs = [{"force": np.arange(10000.0), "speed": np.zeros(10000)},
                {"force": np.arange(100.0) + 0.5, "speed": np.ones(100)},
                {"force": np.arange(4096.0) - 7, "speed": np.ones(4096)}]
        x, lengths = p.acquisition_chunks(acqs, "force")
        self.assertEqual(lengths.tolist(), [4096, 4096, 1808, 100, 4096])
        np.testing.assert_array_equal(x, np.concatenate([a["force"] for a in acqs]))
        x, lengths = p.acquisition_chunks([], "force")
        self.assertEqual((x.size, lengths.size), (0, 0))

    def test_chunk_file_layout(self):
        x = np.array([1.5, -0.0, 3.25, 1e9, 5e-324])
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "c.bin"
            sha = p.write_chunks(x, np.array([2, 3]), f)
            b = f.read_bytes()
            import hashlib
            self.assertEqual(sha, hashlib.sha256(b).hexdigest())
            self.assertEqual(len(b), 2 * 12 + 8 * 5)
            self.assertEqual(int.from_bytes(b[0:4], "little"), 2)
            self.assertEqual(np.frombuffer(b[4:12], "<f8")[0], 1.0)
            np.testing.assert_array_equal(np.frombuffer(b[12:28], "<f8").view("<u8"), x[:2].view("<u8"))
            self.assertEqual(int.from_bytes(b[28:32], "little"), 3)
            np.testing.assert_array_equal(np.frombuffer(b[40:], "<f8").view("<u8"), x[2:].view("<u8"))
            with self.assertRaises(AssertionError):
                p.write_chunks(x, np.array([2, 2]), f)
            with self.assertRaises(AssertionError):
                p.write_chunks(np.zeros(40000), np.array([40000]), f)


if __name__ == "__main__":
    unittest.main()
