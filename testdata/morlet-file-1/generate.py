#!/usr/bin/env python3
"""Generates the MorletDB file format 1 test corpus (docs/file-format.md): corpus.morlet with one recording per
case, and expected.json with each case's sample count, the SHA-256 of its samples as little-endian float64, and
its first values. SHA256SUMS covers both files. Written with the
reference writer (morletdb.open, the morlet-sqlite library); deterministic. Run from this directory:

    MORLETDB_SQLITE_LIB=.../libmorlet_sqlite.dylib PYTHONPATH=../../sdk/python/src python generate.py
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np

import morletdb

HERE = Path(__file__).resolve().parent
rng = np.random.default_rng(20261109)
i = np.arange(20_000)
CASES = {
    # name: (samples, quantum, chunk lengths or chunk size, description)
    "adc-counts-div": (np.round(3000 * np.sin(i / 37.0) + rng.normal(0, 4, i.size)) / 512, 1 / 512, 4096,
                       "ADC counts on a 1/512 grid (grid, mode 1 = k / scale)"),
    "decimal-mul": (np.round(rng.normal(0, 50, 9000)) * 0.3, 0.3, 4096,
                    "multiples of 0.3, not exact in binary (grid, mode 0 = k * scale, last-place corrections)"),
    "calibrated-offset": (273.15 + np.round(rng.normal(0, 200, 6000)) * 0.0625, 1.0, 3000,
                          "an offset grid detected from the data: 273.15 + k / 16 (version 3)"),
    "float-noise": (rng.normal(0, 1, 5000), 1.0, 2500, "values on no grid (float bits)"),
    "raw-bits": (np.frombuffer(rng.integers(0, 2**62, 3000, dtype=np.int64).astype("<i8").tobytes(), "<f8")
                 .copy() % 1e9 * np.where(rng.random(3000) < 0.5, -1, 1), 1.0, 3000,
                 "incompressible values (float bits or raw, whichever is smaller)"),
    "edge-values": (np.array([0.0, -0.0, 1e9, -1e9, 5e-324, -5e-324, 1.0, 1.0, 1.0] * 400), 1.0, [1, 32, 3567],
                    "zero and negative zero, the amplitude limits, subnormals, repeats; chunks of 1, 32, 3567"),
    "constant-long": (np.full(32768 + 5, 42.5), 0.5, 32768, "one full 32,768-sample chunk and a short one"),
    "no-summaries": (np.round(np.cos(i[:8192] / 5.0) * 100) / 4, 0.25, 1024,
                     "chunks whose summary is NULL (as a writer without summaries leaves them)"),
}


def main():
    f = HERE / "corpus.morlet"
    for p in (f, Path(f"{f}-wal"), Path(f"{f}-shm")):
        p.unlink(missing_ok=True)
    expected = {}
    with morletdb.open(f) as db:
        for name, (x, q, layout, desc) in CASES.items():
            x = np.asarray(x, dtype="<f8")
            db.ingest_empty(name, sample_rate_hz=1000.0, channel=name, unit="", sample_quantum=q,
                            metadata={"description": desc})
            lengths = layout if isinstance(layout, list) else [min(layout, x.size - s) for s in range(0, x.size, layout)]
            db.append_chunks(name, 0, x, lengths)
            expected[name] = {"description": desc, "samples": int(x.size),
                              "sha256_float64le": hashlib.sha256(x.tobytes()).hexdigest(),
                              "first": [repr(float(v)) for v in x[:4]]}
    with closing(sqlite3.connect(f)) as c:
        c.execute("UPDATE chunks SET summary=NULL WHERE recording_id='no-summaries'")
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("PRAGMA journal_mode=DELETE")    # one self-contained file in the repository
        c.execute("VACUUM")
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1) + "\n")
    files = ["corpus.morlet", "expected.json"]
    (HERE / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((HERE / n).read_bytes()).hexdigest()}  {n}\n"
                                             for n in files))
    print("wrote", ", ".join(files))


if __name__ == "__main__":
    main()
