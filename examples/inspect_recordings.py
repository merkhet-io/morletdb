#!/usr/bin/env python3
"""Import, search and inspect synthetic recordings through the PostgreSQL path.

Run again after a database restart to verify identical import replay and matches.
No model accuracy or physical fault-detection claim is made by this example.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "ingest"))
sys.path.insert(0, str(ROOT / "tools" / "client"))
from parquet_import import Config, import_files
from morlet_client import RecordingClient


def archive(path):
    rate = 4096
    event = [math.sin(2 * math.pi * (0.035*i + 0.0001*i*i)) *
             (0.2 + math.sin(math.pi*i/512)**2) for i in range(512)]
    rows = []
    for name, offset in [("query", 768), ("related", 1536), ("unrelated", None)]:
        values = [0.1*math.sin(2*math.pi*0.27*i) for i in range(3072)]
        if offset is not None:
            values[offset:offset+512] = event
        rows.append({"recording_id": name, "start_sample": 0,
                     "sample_rate_hz": rate, "channel": "acoustic", "unit": "a.u.",
                     "metadata": json.dumps({"dataset": "synthetic-demo-v1", "synthetic": True}),
                     "samples": values})
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="optional query/match PNG; requires matplotlib")
    args = parser.parse_args()
    dsn = os.environ.get("DATABASE_URL", "")
    with psycopg.connect(dsn) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        connection.execute("CREATE SCHEMA IF NOT EXISTS morlet_demo")
        if connection.execute("SELECT to_regclass('morlet_demo.signals')").fetchone()[0] is None:
            connection.execute("SELECT morlet_create_stream_collection('morlet_demo','signals')")
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "signals.parquet"
        archive(path)
        config = Config(chunk_size=300, rpc_chunks=4, commit_chunks=8)
        first = import_files([path], "morlet_demo.signals", config, dsn=dsn)
        import_files([path], "morlet_demo.signals", config, dsn=dsn)
    with psycopg.connect(dsn) as connection:
        client = RecordingClient(connection, "morlet_demo.signals")
        query = client.read_interval("query", 768, 1280)
        matches = client.search_interval("query", 768, 1280, top_k=3)
        assert matches and matches[0]["recording_id"] == "related", matches
        assert matches[0]["start_sample"] == 1536 and matches[0]["distance"] < 1e-10, matches
        inspected = [client.read_interval(m["recording_id"], m["start_sample"], m["end_sample"])
                     for m in matches]
        assert inspected[0]["samples"] == query["samples"]
        assert connection.execute("SELECT count(*) FROM morlet_demo.signals_chunks").fetchone()[0] == 33
        report = {"synthetic": True, "import_status": first["status"], "query": {
            key: value for key, value in query.items() if key != "samples"}, "matches": matches}
        print(json.dumps(report, indent=2))
        if args.output:
            import matplotlib.pyplot as plt
            fig, axes = plt.subplots(1 + len(inspected), 1, figsize=(10, 2*(1+len(inspected))))
            for axis, row in zip(axes, [query] + inspected):
                axis.plot([(row["start_sample"]+i)/row["sample_rate_hz"] for i in range(len(row["samples"]))], row["samples"])
                axis.set_title(f'{row["recording_id"]}: [{row["start_sample"]}, {row["end_sample"]})')
                axis.set_xlabel("Recording time (seconds)")
            fig.tight_layout()
            fig.savefig(args.output)
            plt.close(fig)


if __name__ == "__main__":
    main()
