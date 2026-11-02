#!/usr/bin/env python3
"""B2 measurement (PROTOCOL.md): ClickHouse (chDB) and TileDB arms on B1's inputs, each verified lossless,
plus a re-measurement of pg_morlet against B1's stored bytes.

  .venv-b2/bin/python -I evaluation/storage_b2/measure_b2.py data/b1-runs/inputs data/b1-runs/p2 \
      target/release/examples/rawcodec_bench evaluation/storage_b2/results
"""
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import chdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import tiledb
from chdb import session as chs

HERE = Path(__file__).resolve().parent
B1 = HERE.parent / "storage_baselines" / "results" / "b1.json"
P2 = {"cx": "cx", "ims": "ims", "pad-c": "paderborn-counts", "pad-p": "paderborn-physical"}
ORDER = ("cx", "ims", "pad-c", "pad-p", "sleep", "ecg")
CALIBRATED, DECIMAL = ("pad-p", "sleep"), ("ims", "ecg")
CH_F = ["ZSTD(19)", "Gorilla, ZSTD(19)", "FPC, ZSTD(19)", "Delta(8), ZSTD(19)"]
CH_I = ["ZSTD(19)", "Delta, ZSTD(19)", "DoubleDelta, ZSTD(19)", "T64, ZSTD(19)", "Gorilla, ZSTD(19)"]
TDB_F = {"Zstd(19)": [], "ByteShuffle, Zstd(19)": ["ByteShuffleFilter"], "BitShuffle, Zstd(19)": ["BitShuffleFilter"],
         "XOR, Zstd(19)": ["XORFilter"]}
TDB_I = {"Zstd(19)": [], "Delta, Zstd(19)": ["DeltaFilter"], "DoubleDelta, Zstd(19)": ["DoubleDeltaFilter"],
         "BitWidthReduction, Zstd(19)": ["BitWidthReductionFilter"], "ByteShuffle, Zstd(19)": ["ByteShuffleFilter"]}
TILE = 4096


def load(inputs, p2, name):
    """B1's loader (evaluation/storage_baselines/measure.py)."""
    if name in P2:
        z = np.load(p2 / f"{P2[name]}.npz")
        q = float(z["quantum"])
        return z["floats"], z["ints"], z["lengths"], np.full(len(z["lengths"]), q)
    z = np.load(inputs / f"{name}.npz")
    return z["floats"], z["ints"], z["lengths"], z["quanta"]


def bits(nbytes, n):
    return 8.0 * nbytes / n


def same(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if a.dtype == np.float64:
        return a.shape == b.shape and np.array_equal(a.view(np.uint64), b.astype(np.float64).view(np.uint64))
    return a.shape == b.shape and np.array_equal(a, b)


def arm_clickhouse(values, sqltype, codec, tmp):
    path = Path(tempfile.mkdtemp(dir=tmp))
    s = chs.Session(str(path / "db"))
    try:
        src = path / "in.parquet"
        pq.write_table(pa.table({"i": pa.array(np.arange(len(values), dtype=np.uint32)), "v": pa.array(values)}), src)
        s.query("CREATE DATABASE IF NOT EXISTS b2")
        s.query(f"CREATE TABLE b2.t (i UInt32, v {sqltype} CODEC({codec})) ENGINE = MergeTree ORDER BY i SETTINGS min_bytes_for_wide_part = 0")
        t0 = time.perf_counter()
        s.query(f"INSERT INTO b2.t SELECT i, v FROM file('{src}', Parquet)")
        s.query("OPTIMIZE TABLE b2.t FINAL")
        write_s = time.perf_counter() - t0
        rows = json.loads(s.query("SELECT count() AS parts, sum(rows) AS rows, sum(bytes_on_disk) AS disk FROM system.parts "
                                  "WHERE database = 'b2' AND table = 't' AND active", "JSON").bytes())["data"][0]
        col = json.loads(s.query("SELECT column_data_compressed_bytes AS c, column_data_uncompressed_bytes AS u "
                                 "FROM system.parts_columns WHERE database = 'b2' AND table = 't' AND active AND column = 'v'",
                                 "JSON").bytes())["data"]
        t0 = time.perf_counter()
        back = pq.read_table(io.BytesIO(s.query("SELECT v FROM b2.t ORDER BY i", "Parquet").bytes())).column("v").to_numpy()
        read_s = time.perf_counter() - t0
        assert int(rows["parts"]) == 1 and len(col) == 1, (rows, col)
        assert same(back, values), f"ClickHouse {sqltype} {codec}: not lossless"
        return {"bytes": int(col[0]["c"]), "part_bytes_on_disk": int(rows["disk"]), "write_s": write_s, "read_s": read_s}
    finally:
        s.close()


def arm_tiledb(values, filters, tmp):
    uri = str(Path(tempfile.mkdtemp(dir=tmp)) / "a")
    n = len(values)
    fl = [getattr(tiledb, f)() for f in filters] + [tiledb.ZstdFilter(level=19)]
    dom = tiledb.Domain(tiledb.Dim(name="i", domain=(0, n - 1), tile=TILE, dtype=np.uint64))
    attr = tiledb.Attr(name="v", dtype=values.dtype, filters=tiledb.FilterList(fl))
    tiledb.Array.create(uri, tiledb.ArraySchema(domain=dom, sparse=False, attrs=[attr]))
    t0 = time.perf_counter()
    with tiledb.open(uri, "w") as a:
        a[:] = values
    tiledb.consolidate(uri)
    tiledb.vacuum(uri)
    write_s = time.perf_counter() - t0
    frags = [p for p in (Path(uri) / "__fragments").iterdir() if p.is_dir()]
    assert len(frags) == 1, frags
    data = sum(f.stat().st_size for f in frags[0].iterdir() if f.name.startswith("a") and f.suffix == ".tdb")
    total = sum(f.stat().st_size for f in Path(uri).rglob("*") if f.is_file())
    t0 = time.perf_counter()
    with tiledb.open(uri) as a:
        back = a[:]["v"]
    read_s = time.perf_counter() - t0
    assert same(back, values), f"TileDB {filters}: not lossless"
    return {"bytes": int(data), "array_bytes_on_disk": int(total), "write_s": write_s, "read_s": read_s}


def arm_morlet(x, lengths, quanta, bench, tmp):
    """B1's pg_morlet arm (the bench asserts bitwise round trips)."""
    path = Path(tmp) / "b2-morlet.bin"
    with open(path, "wb") as f:
        for ch, q in zip(np.split(x, np.cumsum(lengths)[:-1]), quanta):
            f.write(np.uint32(len(ch)).tobytes() + np.float64(q).tobytes() + ch.astype("<f8").tobytes())
    r = json.loads(subprocess.run([str(bench), str(path)], check=True, capture_output=True, text=True).stdout)
    path.unlink()
    return r


def best(chains):
    ok = {k: v for k, v in chains.items() if isinstance(v, dict)}
    k = min(ok, key=lambda c: ok[c]["bits"])
    return k, ok[k]["bits"]


def run_set(fn, values, chains, n, tmp, label):
    out = {}
    for name, spec in chains.items():
        try:
            r = fn(values, spec, tmp)
            r["bits"] = bits(r["bytes"], n)
            out[name] = r
        except Exception as e:                       # reported, not fixed (PROTOCOL.md)
            out[name] = f"unavailable or failed: {type(e).__name__}: {e}"
        print(" ", label, name, round(out[name]["bits"], 3) if isinstance(out[name], dict) else out[name], flush=True)
    return out


def main():
    inputs, p2, bench, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    b1 = json.loads(B1.read_text())["inputs"]
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in ORDER:
            x, ints, lengths, quanta = load(inputs, p2, name)
            n = len(x)
            ints = ints.astype(np.int64)
            print(name, n, flush=True)
            r = {"samples": n}
            r["CH-f_chains"] = run_set(lambda v, c, t: arm_clickhouse(v, "Float64", c, t), x, {c: c for c in CH_F}, n, tmp, "CH-f")
            r["CH-i_chains"] = run_set(lambda v, c, t: arm_clickhouse(v, "Int64", c, t), ints, {c: c for c in CH_I}, n, tmp, "CH-i")
            r["TDB-f_chains"] = run_set(arm_tiledb, x, TDB_F, n, tmp, "TDB-f")
            r["TDB-i_chains"] = run_set(arm_tiledb, ints, TDB_I, n, tmp, "TDB-i")
            for arm in ("CH-f", "CH-i", "TDB-f", "TDB-i"):
                r[arm + "_best"], r[arm] = best(r[arm + "_chains"])
            m = arm_morlet(x, lengths, quanta, bench, tmp)
            r["M"], r["M_detail"] = m["bits_per_sample"], m
            r["M_equals_B1"] = abs(m["bits_per_sample"] - b1[name]["M"]) < 1e-12
            for a in ("Z", "PQ-f", "ALP", "FLAC", "PQ-i", "DDB-i", "M-i"):
                r[a] = b1[name][a]
            res[name] = r
    comps = {}
    for name in ORDER:
        r = res[name]
        fl = {a: r[a] for a in ("Z", "PQ-f", "ALP", "CH-f", "TDB-f") if r.get(a) is not None}
        it = {a: r[a] for a in ("FLAC", "PQ-i", "DDB-i", "CH-i", "TDB-i") if r.get(a) is not None}
        fa, ia = min(fl, key=fl.get), min(it, key=it.get)
        comps[name] = {"best_float": fa, "M_over_best_float": r["M"] / fl[fa], "best_int": ia,
                       "M_over_best_int": r["M"] / it[ia], "M_over_CH-f": r["M"] / r["CH-f"],
                       "M_over_TDB-f": r["M"] / r["TDB-f"], "M_over_CH-i": r["M"] / r["CH-i"],
                       "M_over_TDB-i": r["M"] / r["TDB-i"]}
    claims = {
        "calibrated_smaller_than_every_metadata_free_float": all(comps[k]["M_over_best_float"] < 1 for k in CALIBRATED),
        "within_15pct_of_best_integer_all_six": all(comps[k]["M_over_best_int"] <= 1.15 for k in ORDER),
    }
    versions = {"chdb": chdb.__version__, "chdb_engine": chs.Session().query("SELECT version()", "CSV").bytes().decode().strip(),
                "tiledb_py": tiledb.__version__, "tiledb_core": ".".join(map(str, tiledb.libtiledb.version())),
                "pyarrow": pa.__version__, "numpy": np.__version__}
    (out / "b2.json").write_text(json.dumps({"inputs": res, "comparisons": comps, "claims": claims, "versions": versions},
                                            indent=1) + "\n")
    print(json.dumps({"comparisons": comps, "claims": claims, "versions": versions}, indent=1))


if __name__ == "__main__":
    main()
