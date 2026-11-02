#!/usr/bin/env python3
"""Load the machine demo: NASA/IMS bearing test 2 (run to failure) into a MorletDB collection.

  python examples/machine-demo/load.py DATA_DIR      (DATABASE_URL: a role that may create tables and roles)
  python examples/machine-demo/load.py DATA_DIR --from-dir PATH/2nd_test     (already extracted files)

1. Downloads the IMS bearing archive from NASA's prognostics data repository (about 1 GB) unless DATA_DIR
   already has it, checks its SHA-256, and extracts test 2 (984 snapshot files) with bsdtar.
2. Imports every snapshot as four recordings, one per bearing (1 s at 20 kHz, values as distributed on
   their 0.001 grid, stored exactly), with the bearing, the test conditions and the start time as metadata,
   and builds the search envelopes.
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
RAR_SHA256 = "b154d5ba1ae5f7f01cdd4f1bde5b08cfdc2f3134d51ad2f6614b1270a86ab632"
COLLECTION = "public.bearings"
READER, READER_PASSWORD = "machine_reader", "machine-demo-only"
RATE = 20000.0
CONTEXT = {"rig": "IMS bearing test rig (University of Cincinnati, Rexnord)", "test": "2", "shaft_rpm": 2000,
           "radial_load_lbs": 6000, "bearing_type": "Rexnord ZA-2115 double row",
           "sensor": "PCB 353B33 accelerometer on the bearing housing"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(data):
    """Test 2's snapshot files, downloading and extracting the NASA archive if needed."""
    files = data / "2nd_test"
    if files.is_dir() and len(list(files.iterdir())) == 984:
        return files
    archive = data / "bearings.zip"
    if not archive.exists():
        print(f"downloading {URL} (about 1 GB)", flush=True)
        tmp = archive.with_suffix(".part")
        urllib.request.urlretrieve(URL, tmp)
        tmp.rename(archive)
    if sha256(archive) != ZIP_SHA256:
        raise SystemExit(f"{archive} does not match the expected SHA-256; delete it and run again")
    print("extracting test 2 (about a minute)", flush=True)
    inner = data / "IMS.7z"
    with open(inner, "wb") as out:
        subprocess.run(["bsdtar", "-xOf", str(archive), "4. Bearings/IMS.7z"], stdout=out, check=True)
    subprocess.run(["bsdtar", "-xf", str(inner), "-C", str(data), "2nd_test.rar"], check=True)
    inner.unlink()
    if sha256(data / "2nd_test.rar") != RAR_SHA256:
        raise SystemExit("2nd_test.rar does not match the expected SHA-256")
    subprocess.run(["bsdtar", "-xf", str(data / "2nd_test.rar"), "-C", str(data)], check=True)
    return files


def start_of(name):
    """File names are the snapshot's start on the rig clock (time zone not stated), e.g. 2004.02.12.10.32.39."""
    return datetime.strptime(name, "%Y.%m.%d.%H.%M.%S").replace(tzinfo=timezone.utc)


def read_ims(path, _opts):
    """One IMS test-2 snapshot file: four columns (bearings 1-4) of 20,480 values on a 0.001 grid."""
    values = np.loadtxt(path, dtype=np.float64)
    if values.shape != (20480, 4):
        raise ValueError(f"{path.name}: expected 20480 x 4 values, got {values.shape}")
    t0 = start_of(path.name)
    stamp = t0.strftime("%Y%m%dT%H%M%S")
    out = []
    for b in range(4):
        meta = {**CONTEXT, "bearing": str(b + 1), "source": path.name,
                "start": t0.isoformat().replace("+00:00", "Z")}
        out.append(mi.Recording(f"B{b + 1}.{stamp}", RATE, "housing-accel", "as distributed (unit not stated)",
                                0.001, values[:, b], meta))
    return out


def imported(conn):
    if conn.execute("SELECT to_regclass(%s)", (COLLECTION + "_envelopes",)).fetchone()[0] is None:
        return False
    return conn.execute(f"SELECT count(*) FROM {COLLECTION}").fetchone()[0] >= 984 * 4


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
        done = imported(conn)
    if done:
        print(f"{COLLECTION} is already loaded", flush=True)
    else:
        files = sorted((opts.from_dir or fetch(opts.data)).iterdir())
        if len(files) != 984:
            raise SystemExit(f"expected 984 snapshot files, found {len(files)}")
        mi.READERS["ims"] = read_ims
        print(f"importing {len(files)} snapshots x 4 bearings and building search envelopes", flush=True)
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
