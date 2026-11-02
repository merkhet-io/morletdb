#!/usr/bin/env python3
"""B3 packed baseline 2 (PROTOCOL.md "Packed-recording baseline"): one Parquet file per group with the QuestDB
table's columns (ts as int64 nanoseconds, acquisition as dictionary string, channels as float64), pyarrow,
zstd level 9, one row group per acquisition. Storage only; read back and checked by SHA-256."""
import json
import tempfile
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

import common
import workload as wl


def main():
    m = common.manifest()
    tmp = Path(tempfile.mkdtemp(prefix="b3-packed-"))
    out = {"codec": "zstd", "level": 9, "pyarrow": pa.__version__, "groups": {}}
    writers = {}
    t0 = time.perf_counter()
    for a in m["acquisitions"]:
        data = wl.load(wl.ROOT / a["path"])
        for g, spec in wl.GROUPS.items():
            n = len(data[spec["channels"][0]])
            cols = {"ts": pa.array(a["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"],
                                   pa.timestamp("ns", tz="UTC")),
                    "acquisition": pa.DictionaryArray.from_arrays(pa.array(np.zeros(n, np.int32)),
                                                                  pa.array([a["acquisition"]]))}
            for c in spec["channels"]:
                cols[c] = pa.array(data[c], pa.float64())
            t = pa.table(cols)
            if g not in writers:
                writers[g] = pq.ParquetWriter(tmp / f"{g}.parquet", t.schema, compression="zstd", compression_level=9)
            writers[g].write_table(t, row_group_size=n)
    for w in writers.values():
        w.close()
    out["write_s"] = round(time.perf_counter() - t0, 1)
    bad = 0
    for g, spec in wl.GROUPS.items():
        f = tmp / f"{g}.parquet"
        out["groups"][g] = {"bytes": f.stat().st_size, "samples": m["samples"][g],
                            "bytes_per_sample": round(f.stat().st_size / m["samples"][g], 4),
                            "ratio_to_float64": round(f.stat().st_size / (8 * m["samples"][g]), 4)}
        pf = pq.ParquetFile(f)
        for i, a in enumerate(m["acquisitions"]):
            rg = pf.read_row_group(i)
            for c in spec["channels"]:
                bad += common.sha(rg.column(c).to_numpy()) != a["channels"][c]["sha256"]
        f.unlink()
    tmp.rmdir()
    out["mismatches"] = int(bad)
    (common.RESULTS / "packed_parquet.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
