#!/usr/bin/env python3
"""B4 measurement (PROTOCOL.md): MorletDB's chunk codec against the 2026 float codecs, every arm lossless.

  .venv-b4/bin/python -I evaluation/storage_b4/measure_b4.py SET OUT.json
      SET: dev (4 Elf series chosen by seed) or confirm (B1's six waveform inputs and all Elf series)

Arms per input (bits per sample; every arm decoded and compared bit for bit, a mismatch is recorded and voids it):
- M-auto: pg_morlet chunk codec, 4,096-sample chunks, quantum 1 (the codec detects a grid itself);
- M-decl: the same with B1's declared quanta (waveform inputs only);
- PCO: pcodec 1.0.4, default level, whole series;
- PQ-ALP, PQ-ALP-zstd9, PQ-BSS-zstd9: Rust parquet 60.0.0 (pqalp), one Float64 column, dictionary off;
- CH-ALP, CH-ALP-zstd9, CH-PCO, CH-zstd9: ClickHouse 26.10 (Docker), MergeTree wide part, one Float64 column.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pcodec
from pcodec import standalone

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = ROOT / "data"
BENCH = ROOT / "target/release/examples/rawcodec_bench"
PQALP = HERE / "pqalp/target/release/pqalp"
CH_IMAGE = "clickhouse/clickhouse-server:26.10"
WAVE = {"cx": "p2/cx", "ims": "p2/ims", "pad-c": "p2/paderborn-counts", "pad-p": "p2/paderborn-physical",
        "sleep": "inputs/sleep", "ecg": "inputs/ecg"}
CHUNK = 4096
PQ = {"PQ-ALP": ("ALP", "none"), "PQ-ALP-zstd9": ("ALP", "zstd:9"), "PQ-BSS-zstd9": ("BYTE_STREAM_SPLIT", "zstd:9")}
CH = {"CH-ALP": "ALP", "CH-ALP-zstd9": "ALP, ZSTD(9)", "CH-PCO": "PCO", "CH-zstd9": "ZSTD(9)"}


def elf_series():
    """The Elf test files, byte-identical duplicates removed (init.csv is a copy of Wind-Speed.csv)."""
    seen, out = set(), []
    for p in sorted((DATA / "b4-elf").glob("*.csv")):
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h not in seen:
            seen.add(h)
            out.append(p)
    return out


def dev_names():
    rng = np.random.default_rng(20261104)
    names = [p.stem for p in elf_series()]
    return sorted(rng.choice(names, 4, replace=False).tolist())


def load_wave(name):
    z = np.load(DATA / "b1-runs" / f"{WAVE[name]}.npz")
    q = np.full(len(z["lengths"]), float(z["quantum"])) if "quantum" in z.files else z["quanta"]
    return z["floats"].astype("<f8"), z["lengths"], q


def load_elf(name):
    # Every line that parses as a number, in file order; other lines (empty, "") are skipped. (Elf's own reader
    # turns an unparseable line into 0.0 and drops the last partial 1,000-value block; B4 does neither.)
    vals = []
    for line in (DATA / "b4-elf" / f"{name}.csv").read_text().splitlines():
        try:
            vals.append(float(line.strip()))
        except ValueError:
            pass
    x = np.array(vals, dtype=np.float64)
    lengths = np.diff(np.r_[np.arange(0, x.size, CHUNK), x.size])
    return x.astype("<f8"), lengths, None


def same(a, b):
    return a.shape == b.shape and np.array_equal(a.view(np.uint64), b.view(np.uint64))


def arm_morlet(x, lengths, quanta, tmp):
    path = Path(tmp) / "m.bin"
    with open(path, "wb") as f:
        for ch, q in zip(np.split(x, np.cumsum(lengths)[:-1]), quanta):
            f.write(np.uint32(len(ch)).tobytes() + np.float64(q).tobytes() + ch.tobytes())
    r = json.loads(subprocess.run([str(BENCH), str(path)], check=True, capture_output=True, text=True).stdout)
    # the bench asserts a bitwise round trip of every chunk
    return {"bytes": r["bytes"], "exact": True, "grid_chunks": r["grid_chunks"], "float_chunks": r["float_chunks"],
            "raw_chunks": r["raw_chunks"], "encode_msamples_s": r["encode_msamples_s"],
            "decode_msamples_s": r["decode_msamples_s"]}


def arm_pco(x):
    t = time.perf_counter()
    b = standalone.simple_compress(x, pcodec.ChunkConfig())
    enc = time.perf_counter() - t
    t = time.perf_counter()
    y = standalone.simple_decompress(b)
    dec = time.perf_counter() - t
    return {"bytes": len(b), "exact": bool(same(x, np.asarray(y, dtype=np.float64))),
            "encode_msamples_s": x.size / enc / 1e6, "decode_msamples_s": x.size / dec / 1e6}


def arm_parquet(x, enc, comp, tmp):
    src, out = Path(tmp) / "pq.f64", Path(tmp) / "pq.parquet"
    x.tofile(src)
    r = json.loads(subprocess.run([str(PQALP), str(src), str(out), enc, comp], check=True, capture_output=True,
                                  text=True).stdout)
    return {"bytes": r["bytes"], "exact": r["exact"], "encodings": r["encodings"],
            "encode_msamples_s": x.size / r["write_s"] / 1e6, "decode_msamples_s": x.size / r["read_s"] / 1e6}


def arm_clickhouse(x, codec, tmp):
    w = Path(tmp) / "ch"
    w.mkdir(exist_ok=True)
    # The row index travels with each value: numbering rows at insert (rowNumberInAllBlocks) depended on
    # ClickHouse's parallel parsing and reordered rows on large inputs (deviation 1).
    rec = np.empty(x.size, dtype=[("i", "<u4"), ("v", "<f8")])
    rec["i"], rec["v"] = np.arange(x.size, dtype=np.uint32), x
    rec.tofile(w / "in.bin")
    sql = (f"CREATE TABLE t (i UInt32, v Float64 CODEC({codec})) ENGINE=MergeTree ORDER BY i "
           "SETTINGS min_bytes_for_wide_part=0;"
           "INSERT INTO t SELECT i, v FROM file('/w/in.bin', RowBinary, 'i UInt32, v Float64');"
           "OPTIMIZE TABLE t FINAL;"
           "SELECT sum(column_data_compressed_bytes) FROM system.parts_columns "
           "WHERE table='t' AND column='v' AND active FORMAT TSV;"
           "SELECT v FROM t ORDER BY i INTO OUTFILE '/w/out.f64' TRUNCATE FORMAT RowBinary;")
    out = subprocess.run(["docker", "run", "--rm", "-v", f"{w}:/w", "--entrypoint", "clickhouse", CH_IMAGE, "local",
                          "--path", "/tmp/chd", "--enable_alp_codec", "1", "--enable_pco_codec", "1",
                          "--allow_experimental_codecs", "1", "-mn", "-q", sql],
                         check=True, capture_output=True, text=True).stdout.split()
    y = np.fromfile(w / "out.f64", dtype="<f8")
    return {"bytes": int(out[-1]), "exact": bool(same(x, y))}


def measure(name, x, lengths, quanta, tmp):
    n = x.size
    arms = {"M-auto": arm_morlet(x, lengths, np.ones(len(lengths)), tmp)}
    if quanta is not None:
        arms["M-decl"] = arm_morlet(x, lengths, quanta, tmp)
    arms["PCO"] = arm_pco(x)
    for k, (e, c) in PQ.items():
        arms[k] = arm_parquet(x, e, c, tmp)
    for k, c in CH.items():
        arms[k] = arm_clickhouse(x, c, tmp)
    for a in arms.values():
        a["bits"] = round(8 * a["bytes"] / n, 4)
    print(name, {k: (v["bits"], v["exact"]) for k, v in arms.items()}, flush=True)
    return {"samples": n, "sha256": hashlib.sha256(x.tobytes()).hexdigest(), "arms": arms}


def main_ch_only(which, out, base):
    """Deviation 1: the ClickHouse arms again, with the row index in the input; other arms copied from base."""
    res = json.loads(Path(base).read_text())
    res["ch_rerun"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with tempfile.TemporaryDirectory() as tmp:
        for name, v in res["inputs"].items():
            x = (load_wave(name) if v["kind"] == "waveform" else load_elf(name))[0]
            for k, c in CH.items():
                a = arm_clickhouse(x, c, tmp)
                a["bits"] = round(8 * a["bytes"] / x.size, 4)
                v["arms"][k] = a
            print(name, {k: (v["arms"][k]["bits"], v["arms"][k]["exact"]) for k in CH}, flush=True)
    Path(out).write_text(json.dumps(res, indent=1) + "\n")


def main(which, out):
    names = dev_names() if which == "dev" else None
    res = {"set": which, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "pcodec": pcodec.__version__ if hasattr(
        pcodec, "__version__") else "1.0.4", "clickhouse_image": CH_IMAGE,
        "clickhouse_image_id": subprocess.run(["docker", "image", "inspect", CH_IMAGE, "--format", "{{.Id}}"],
                                              capture_output=True, text=True).stdout.strip(), "inputs": {}}
    with tempfile.TemporaryDirectory() as tmp:
        if which == "confirm":
            for name in WAVE:
                res["inputs"][name] = {"kind": "waveform", **measure(name, *load_wave(name), tmp)}
        for p in elf_series():
            if which == "dev" and p.stem not in names:
                continue
            res["inputs"][p.stem] = {"kind": "elf", **measure(p.stem, *load_elf(p.stem), tmp)}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(res, indent=1) + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "ch-rerun":
        main_ch_only("confirm", sys.argv[2], sys.argv[3])
    else:
        main(sys.argv[1], sys.argv[2])
