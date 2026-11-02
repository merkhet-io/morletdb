"""morlet_s3.py helpers that need no object store (the S3 round trip is tested manually; see docs/object-storage.md)."""
import unittest

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


if __name__ == "__main__":
    unittest.main()
