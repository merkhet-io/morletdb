"""morlet_s3.py: helpers without an object store, and resume after an interrupted import against a database with a
fake object store (MORLET_MCP_TEST_DATABASE_URL). The real S3 round trip is tested manually (docs/object-storage.md)."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import morlet_s3 as ms


class Helpers(unittest.TestCase):
    def test_split_urls(self):
        self.assertEqual(ms.split("s3://bucket/a/b.parquet"), ("bucket", "a/b.parquet"))
        self.assertEqual(ms.split("s3://bucket/prefix/"), ("bucket", "prefix/"))
        self.assertEqual(ms.split("s3://bucket"), ("bucket", ""))
        for bad in ("https://bucket/x", "s3:///x", "bucket/x"):
            with self.assertRaises(ValueError):
                ms.split(bad)

    def test_supported_suffixes_follow_the_importer(self):
        for suffix in (".wav", ".edf", ".mseed", ".tdms", ".csv", ".parquet"):
            self.assertIn(suffix, ms.SUPPORTED)
        self.assertNotIn(".txt", ms.SUPPORTED)
        self.assertNotIn(".bdf", ms.SUPPORTED)


URL = os.environ.get("MORLET_MCP_TEST_DATABASE_URL")


class FakeS3:
    """list_objects_v2 / head_object / download_file over a local directory."""
    def __init__(self, root):
        self.root = Path(root)

    def get_paginator(self, _name):
        root = self.root

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": f.name, "ETag": f'"etag-{f.name}"'} for f in sorted(root.iterdir())]}
        return P()

    def head_object(self, Bucket, Key):
        return {"ETag": f'"etag-{Key}"', "ContentLength": (self.root / Key).stat().st_size}

    def download_file(self, Bucket, Key, dest):
        shutil.copy(self.root / Key, dest)


@unittest.skipUnless(URL, "needs MORLET_MCP_TEST_DATABASE_URL")
class ResumeAfterAnInterruptedObject(unittest.TestCase):                       # #442
    def setUp(self):
        import psycopg
        with psycopg.connect(URL, autocommit=True) as c:
            for t in ("s3_imports", "chunks", "segments", "envelopes"):
                c.execute(f"DROP TABLE IF EXISTS public.s3r_{t} CASCADE")
            c.execute("DROP TABLE IF EXISTS public.s3r CASCADE")
        self.dir = tempfile.TemporaryDirectory()
        rows = ["t,a,b"] + [f"{i / 1000:.3f},{i % 7},{(i * 3) % 11}" for i in range(20000)]
        for name in ("one.csv", "two.csv"):
            Path(self.dir.name, name).write_text("\n".join(rows) + "\n")
        self.fake = FakeS3(self.dir.name)
        self.client, ms.client = ms.client, lambda: self.fake
        self.reader = ms.mi.chunk_reader

    def tearDown(self):
        ms.client, ms.mi.chunk_reader = self.client, self.reader
        self.dir.cleanup()

    def fail_two_after_first_commit(self):
        original = self.reader

        def reader(opts):
            base = original(opts)

            def read(path, config, mapping):
                for n, x in enumerate(base(path, config, mapping)):
                    if path.name == "two.csv" and n == 2:
                        raise OSError("connection lost")
                    yield x
            return read
        ms.mi.chunk_reader = reader

    def ends(self):
        import psycopg
        with psycopg.connect(URL) as c:
            return dict(c.execute("SELECT recording_id, (checkpoint->>'end')::bigint FROM s3r").fetchall())

    def run_import(self):
        return ms.import_prefix("s3://bucket/", "public.s3r", URL, ["--create", "--time-column", "t", "--no-build"])

    def test_an_object_cut_off_after_a_commit_is_imported_again(self):
        self.fail_two_after_first_commit()
        report, code = self.run_import()
        self.assertEqual(code, 1)
        self.assertEqual([o["key"] for o in report["imported"]], ["one.csv"])
        self.assertEqual([o["key"] for o in report["failed"]], ["two.csv"])
        self.assertLess(self.ends().get("two.a", 0), 20000)                      # part of two.csv is stored
        self.assertEqual(ms.already_imported(URL, "public.s3r", "bucket"), {("one.csv", "etag-one.csv")})
        ms.mi.chunk_reader = self.reader                                          # the link is back
        report, code = self.run_import()
        self.assertEqual((code, report["skipped_already_imported"]), (0, 1))
        self.assertEqual(report["imported"][0]["recordings"], {"two.a": 20000, "two.b": 20000})
        self.assertEqual(self.ends(), {"one.a": 20000, "one.b": 20000, "two.a": 20000, "two.b": 20000})
        report, code = self.run_import()
        self.assertEqual((code, report["skipped_already_imported"], report["imported"]), (0, 2, []))


if __name__ == "__main__":
    unittest.main()
