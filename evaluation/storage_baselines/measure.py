#!/usr/bin/env python3
"""B1 measurement (PROTOCOL.md): bits/sample of every arm on every input, each verified lossless.

  .venv-paper/bin/python -I evaluation/storage_baselines/measure.py data/b1-runs/inputs data/b1-runs/p2 \
      target/release/examples/rawcodec_bench evaluation/storage_baselines/results
"""
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyflac
import zstandard

BLOCK = 65536
P2 = {"cx": "cx", "ims": "ims", "pad-c": "paderborn-counts", "pad-p": "paderborn-physical"}
ORDER = ("cx", "ims", "pad-c", "pad-p", "sleep", "ecg")
CALIBRATED, DECIMAL = ("pad-p", "sleep"), ("ims", "ecg")


def load(inputs, p2, name):
    if name in P2:
        z = np.load(p2 / f"{P2[name]}.npz")
        q = float(z["quantum"])
        return z["floats"], z["ints"], z["lengths"], np.full(len(z["lengths"]), q)
    z = np.load(inputs / f"{name}.npz")
    return z["floats"], z["ints"], z["lengths"], z["quanta"]


def chunks(x, lengths):
    return np.split(x, np.cumsum(lengths)[:-1])


def bits(nbytes, n):
    return 8.0 * nbytes / n


def arm_zstd(x, lengths):
    c, d = zstandard.ZstdCompressor(level=19), zstandard.ZstdDecompressor()
    total = 0
    for ch in chunks(x, lengths):
        e = c.compress(ch.tobytes())
        assert d.decompress(e) == ch.tobytes()
        total += len(e)
    return total


def arm_parquet(col, encoding):
    buf = io.BytesIO()
    pq.write_table(pa.table({"v": col}), buf, compression="zstd", compression_level=19, use_dictionary=False,
                   column_encoding={"v": encoding}, row_group_size=len(col))
    back = pq.read_table(io.BytesIO(buf.getvalue()))["v"].to_numpy()
    assert back.dtype == col.dtype and back.tobytes() == col.tobytes()
    return len(buf.getvalue())


def duck_used(con):
    return con.execute("SELECT used_blocks * block_size FROM pragma_database_size() WHERE database_name = 'd'").fetchall()[0][0]


def arm_duckdb(col, sqltype, tmp):
    """Used bytes of a one-column table after CHECKPOINT, minus the empty table; read back single-threaded
    (insertion order) and compared."""
    path = Path(tmp) / f"b1-{sqltype}.duckdb"
    con = duckdb.connect()
    con.execute("SET threads = 1")
    con.execute(f"ATTACH '{path}' AS d (BLOCK_SIZE {BLOCK})")
    con.execute(f"CREATE TABLE d.t (v {sqltype})")
    con.execute("CHECKPOINT d")
    empty = duck_used(con)
    con.register("src", pa.table({"v": col}))
    con.execute("INSERT INTO d.t SELECT v FROM src")
    con.execute("CHECKPOINT d")
    used = duck_used(con)
    comp = dict(con.execute("SELECT compression, sum(count) FROM pragma_storage_info('d.t') GROUP BY 1").fetchall())
    back = con.execute("SELECT v FROM d.t").fetchnumpy()["v"]
    assert np.asarray(back, dtype=col.dtype).tobytes() == col.tobytes()
    con.close()
    path.unlink()
    return used - empty, {k: int(v) for k, v in comp.items()}


def arm_flac(ints):
    if ints.min() < -(1 << 23) or ints.max() >= (1 << 23):
        return None
    out = []
    enc = pyflac.StreamEncoder(sample_rate=48000, write_callback=lambda b, n, s, f: out.append(len(b)),
                               compression_level=8, blocksize=4096, verify=True)
    enc.process(ints.astype(np.int32).reshape(-1, 1) if ints.max() >= (1 << 15) or ints.min() < -(1 << 15)
                else ints.astype(np.int16).reshape(-1, 1))
    enc.finish()                       # libFLAC verify mode decodes every frame and compares
    return sum(out)


def arm_morlet(x, lengths, quanta, bench, tmp):
    path = Path(tmp) / "b1-morlet.bin"
    with open(path, "wb") as f:
        for ch, q in zip(chunks(x, lengths), quanta):
            f.write(np.uint32(len(ch)).tobytes() + np.float64(q).tobytes() + ch.astype("<f8").tobytes())
    r = json.loads(subprocess.run([str(bench), str(path)], check=True, capture_output=True, text=True).stdout)
    path.unlink()
    return r                           # the bench asserts bitwise round trips


def main():
    inputs, p2, bench, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in ORDER:
            x, ints, lengths, quanta = load(inputs, p2, name)
            n = len(x)
            assert lengths.sum() == n == len(ints)
            r = {"samples": n, "chunks": int(len(lengths))}
            t = time.perf_counter()
            r["Z"] = bits(arm_zstd(x, lengths), n)
            r["PQ-f"] = bits(arm_parquet(x, "BYTE_STREAM_SPLIT"), n)
            alp, comp = arm_duckdb(x, "DOUBLE", tmp)
            r["ALP"], r["ALP_compression"] = bits(alp, n), comp
            m = arm_morlet(x, lengths, quanta, bench, tmp)
            r["M"], r["M_detail"] = m["bits_per_sample"], m
            fl = arm_flac(ints)
            r["FLAC"] = None if fl is None else bits(fl, n)
            r["PQ-i"] = bits(arm_parquet(ints.astype(np.int64), "DELTA_BINARY_PACKED"), n)
            di, dcomp = arm_duckdb(ints.astype(np.int64), "BIGINT", tmp)
            r["DDB-i"], r["DDB-i_compression"] = bits(di, n), dcomp
            mi = arm_morlet(ints.astype(np.float64), lengths, np.ones(len(lengths)), bench, tmp)
            r["M-i"], r["M-i_detail"] = mi["bits_per_sample"], mi
            r["seconds"] = time.perf_counter() - t
            res[name] = r
            print(name, {k: round(v, 3) for k, v in r.items() if isinstance(v, float)}, flush=True)

    def best(r, arms):
        vals = {a: r[a] for a in arms if r.get(a) is not None}
        a = min(vals, key=vals.get)
        return a, vals[a]

    comps = {}
    for name in ORDER:
        r = res[name]
        fa, fv = best(r, ("Z", "PQ-f", "ALP"))
        ia, iv = best(r, ("FLAC", "PQ-i", "DDB-i"))
        comps[name] = {"best_float": fa, "M_over_best_float": r["M"] / fv, "best_int": ia, "M_over_best_int": r["M"] / iv,
                       "M_over_ALP": r["M"] / r["ALP"]}
    claims = {
        "calibrated_2x_vs_metadata_free": all(comps[k]["M_over_best_float"] <= 0.5 for k in CALIBRATED),
        "within_15pct_of_best_integer": all(comps[k]["M_over_best_int"] <= 1.15 for k in ORDER),
        "decimal_beats_ALP_by_20pct": all(comps[k]["M_over_ALP"] <= 0.8 for k in DECIMAL),
    }
    json.dump({"inputs": res, "comparisons": comps, "claims": claims, "duckdb": duckdb.__version__,
               "pyarrow": pa.__version__, "zstandard": zstandard.__version__}, open(out / "b1.json", "w"), indent=1)
    print(json.dumps({"comparisons": comps, "claims": claims}, indent=1))


if __name__ == "__main__":
    main()
