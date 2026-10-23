import math
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq
from parquet_import import Config, chunks, import_files, ImportFailure


class ParquetContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"input.parquet"
        self.values = [math.sin(i/8) for i in range(2048)]

    def write(self, rows):
        pq.write_table(pa.Table.from_pylist(rows), self.path, row_group_size=2)

    def base(self):
        return dict(recording_id="A", sample_rate_hz=4096., channel="vibration", unit="g")

    def test_layout_and_batch_boundaries_do_not_change_chunks(self):
        self.write([{**self.base(), "start_sample": start, "samples":self.values[start:end]}
                    for start, end in [(0,137),(137,1000),(1000,2048)]])
        expected=list(chunks(self.path, Config(chunk_size=512,batch_rows=1)))
        self.assertEqual(expected,list(chunks(self.path,Config(chunk_size=512,batch_rows=64))))
        self.write([{**self.base(), "sample_index":i, "value":value} for i,value in enumerate(self.values)])
        self.assertEqual(expected,list(chunks(self.path,Config(layout="samples",chunk_size=512,batch_rows=17))))
        self.assertEqual([c[2] for c in expected],[0,512,1024,1536])

    def test_mapping_and_validation_without_database(self):
        self.write([{**self.base(), "offset":0,"wave":self.values}])
        report=import_files([self.path],"public.unused",Config(chunk_size=512),
                            {"start_sample":"offset","samples":"wave"},validate_only=True)
        self.assertEqual(report["status"],"validated")
        self.assertEqual(report["validated_sample_submissions"],2048)
        self.assertEqual(report["committed_chunk_submissions"],0)
        self.assertEqual(len(report["files"][0]["sha256"]),64)

    def test_bounds_rpc_payload_before_database_work(self):
        Config(chunk_size=4096,rpc_chunks=64).validate()
        for config in [Config(rpc_chunks=65),Config(chunk_size=8192,rpc_chunks=64)]:
            with self.assertRaises(ValueError):
                config.validate()

    def test_rejects_gaps_invalid_values_and_changed_metadata(self):
        for rows in [
            [{**self.base(),"start_sample":0,"samples":[0.]*137},
             {**self.base(),"start_sample":138,"samples":[0.]}],
            [{**self.base(),"start_sample":0,"samples":[float("nan")]}],
            [{**self.base(),"start_sample":0,"samples":[0.],"metadata":"{}"},
             {**self.base(),"start_sample":1,"samples":[0.],"metadata":'{"asset":"B"}'}],
        ]:
            self.write(rows)
            with self.assertRaises(ImportFailure):
                import_files([self.path],"public.unused",validate_only=True)


if __name__ == "__main__":
    unittest.main()
