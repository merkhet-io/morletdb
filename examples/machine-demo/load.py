#!/usr/bin/env python3
"""Load the machine demo: NASA/IMS bearing tests 2 and 3 (runs to failure) into a MorletDB collection.

  python examples/machine-demo/load.py DATA_DIR      (DATABASE_URL: a role that may create tables and roles)
  python examples/machine-demo/load.py DATA_DIR --from-dir PATH       (already extracted: PATH/2nd_test and
                                                                       PATH/4th_test/txt)

1. Downloads the IMS bearing archive from NASA's prognostics data repository (about 1 GB) unless DATA_DIR
   already has it, checks its SHA-256, and extracts tests 2 and 3 with bsdtar.
2. Imports test 2's 984 snapshots and every sixth of test 3's 6,324 (one an hour) as four recordings each, one
   per bearing (1 s at 20 kHz, values as distributed on their 0.001 grid, stored exactly), with the test, the
   bearing, the test conditions and the start time as metadata, and builds the search envelopes.
3. Creates the read-only role machine_reader that the MCP server connects as.

Running it again skips what is already done.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools/ingest"))
import morlet_import as mi  # noqa: E402

URL = "https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip"
ZIP_SHA256 = "21001ac266c465f5d345ec42d7b508c6a6328487fd9d4d7774422dd5ea10ad83"
RAR_SHA256 = {"2nd_test.rar": "b154d5ba1ae5f7f01cdd4f1bde5b08cfdc2f3134d51ad2f6614b1270a86ab632",
              "3rd_test.rar": "01e9ec83c6c55adc0300a20003a261f9a2ac2b714aae4984050325e589252bc8"}
TESTS = {"2": {"folder": "2nd_test", "files": 984, "every": 1},
         "3": {"folder": "4th_test/txt", "files": 6324, "every": 6}}   # test 3 ships as 4th_test/txt
COLLECTION = "public.bearings"
READER, READER_PASSWORD = "machine_reader", "machine-demo-only"
RATE = 20000.0
CONTEXT = {"rig": "IMS bearing test rig (University of Cincinnati, Rexnord)", "shaft_rpm": 2000,
           "radial_load_lbs": 6000, "bearing_type": "Rexnord ZA-2115 double row",
           "sensor": "PCB 353B33 accelerometer on the bearing housing"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(data):
    """Folder holding 2nd_test and 4th_test/txt, downloading and extracting the NASA archive if needed."""
    if all((data / t["folder"]).is_dir() and len(list((data / t["folder"]).iterdir())) == t["files"]
           for t in TESTS.values()):
        return data
    archive = data / "bearings.zip"
    if not archive.exists():
        print(f"downloading {URL} (about 1 GB)", flush=True)
        tmp = archive.with_suffix(".part")
        urllib.request.urlretrieve(URL, tmp)
        tmp.rename(archive)
    if sha256(archive) != ZIP_SHA256:
        raise SystemExit(f"{archive} does not match the expected SHA-256; delete it and run again")
    print("extracting tests 2 and 3 (a few minutes)", flush=True)
    inner = data / "IMS.7z"
    with open(inner, "wb") as out:
        subprocess.run(["bsdtar", "-xOf", str(archive), "4. Bearings/IMS.7z"], stdout=out, check=True)
    subprocess.run(["bsdtar", "-xf", str(inner), "-C", str(data), *RAR_SHA256], check=True)
    inner.unlink()
    for rar, digest in RAR_SHA256.items():
        if sha256(data / rar) != digest:
            raise SystemExit(f"{rar} does not match the expected SHA-256")
        subprocess.run(["bsdtar", "-xf", str(data / rar), "-C", str(data)], check=True)
    return data


def start_of(name):
    """File names are the snapshot's start on the rig clock (time zone not stated), e.g. 2004.02.12.10.32.39."""
    return datetime.strptime(name, "%Y.%m.%d.%H.%M.%S").replace(tzinfo=timezone.utc)


def read_ims(path, _opts):
    """One IMS test-2 or test-3 snapshot file: four columns (bearings 1-4) of 20,480 values on a 0.001 grid.
    Recording ids are bearing and start time; the tests do not overlap in time."""
    values = np.loadtxt(path, dtype=np.float64)
    if values.shape != (20480, 4):
        raise ValueError(f"{path.name}: expected 20480 x 4 values, got {values.shape}")
    t0 = start_of(path.name)
    test = "2" if t0.year == 2004 and t0.month == 2 else "3"
    stamp = t0.strftime("%Y%m%dT%H%M%S")
    out = []
    for b in range(4):
        meta = {**CONTEXT, "test": test, "bearing": str(b + 1), "source": path.name,
                "start": t0.isoformat().replace("+00:00", "Z")}
        out.append(mi.Recording(f"B{b + 1}.{stamp}", RATE, "housing-accel", "as distributed (unit not stated)",
                                0.001, values[:, b], meta))
    return out


def loaded_tests(conn):
    """Tests already in the collection, by number of recordings."""
    if conn.execute("SELECT to_regclass(%s)", (COLLECTION + "_envelopes",)).fetchone()[0] is None:
        return {}
    return dict(conn.execute(f"SELECT metadata->>'test', count(*) FROM {COLLECTION} GROUP BY 1").fetchall())


def grant_reader(conn):
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,)).fetchone():
        conn.execute(f"CREATE ROLE {READER} LOGIN PASSWORD '{READER_PASSWORD}'")
    conn.execute(f"GRANT USAGE ON SCHEMA public TO {READER}")
    conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}")
    conn.commit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("data", nargs="?", default="data", type=Path)
    p.add_argument("--from-dir", type=Path, help="use already extracted test-2 files instead of downloading")
    opts = p.parse_args()
    opts.data.mkdir(parents=True, exist_ok=True)
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        conn.commit()
        have = loaded_tests(conn)
    todo = [t for t, spec in TESTS.items() if have.get(t, 0) < 4 * len(range(0, spec["files"], spec["every"]))]
    if not todo:
        print(f"{COLLECTION} is already loaded", flush=True)
    else:
        root = opts.from_dir or fetch(opts.data)
        files = []
        for t in todo:
            spec = TESTS[t]
            names = sorted((root / spec["folder"]).iterdir())
            if len(names) != spec["files"]:
                raise SystemExit(f"test {t}: expected {spec['files']} snapshot files, found {len(names)}")
            files += names[::spec["every"]]
        mi.READERS["ims"] = read_ims
        print(f"importing {len(files)} snapshots (tests {', '.join(todo)}) x 4 bearings and building search "
              f"envelopes", flush=True)
        code = mi.main([*map(str, files), "--collection", COLLECTION, "--create", "--format", "ims"])
        if code:
            raise SystemExit(code)
    with psycopg.connect(dsn) as conn:
        grant_reader(conn)
        n, windows = conn.execute(
            f"SELECT (SELECT count(*) FROM {COLLECTION}), (SELECT count(*) FROM {COLLECTION}_envelopes)").fetchone()
    print(f"\nready: {n} recordings, {windows} search windows in {COLLECTION}; "
          f"the MCP server connects as {READER} (read only)", flush=True)
    print("next: open Claude Code in examples/machine-demo and ask the question in README.md", flush=True)


if __name__ == "__main__":
    main()
