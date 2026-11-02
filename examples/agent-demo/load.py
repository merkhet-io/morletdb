#!/usr/bin/env python3
"""Load the agent demo: four days of public seismic data into a MorletDB collection.

  python examples/agent-demo/load.py DATA_DIR        (DATABASE_URL: a role that may create tables and roles)

1. Fetches CX.PB01, PB02, PB06 and PSGCX HHZ (100 Hz) for 2025-03-11..14 UTC from the GEOFON FDSN service
   (IPOC network, open access), one miniSEED file per station, unless DATA_DIR already has it.
2. Checks the samples against the hashes in manifest.json beside this script and says so if they differ.
3. Imports them with tools/ingest/morlet_import.py into public.seismic and builds the search envelopes.
4. Creates the read-only role agent_reader that the MCP server connects as.

Running it again skips what is already done.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
GEOFON = "https://geofon.gfz.de"
NETWORK, CHANNEL = "CX", "HHZ"
STATIONS = ("PB01", "PB02", "PB06", "PSGCX")
START, END = "2025-03-11T00:00:00", "2025-03-15T00:00:00"
COLLECTION = "public.seismic"
READER, READER_PASSWORD = "agent_reader", "agent-demo-only"


def path_for(data, station):
    return data / f"{NETWORK}.{station}.{CHANNEL}.20250311-20250314.mseed"


def sample_digest(path):
    """Samples within [START, END) as contiguous segments: (start, count) and one SHA-256 over all of them.
    Independent of how the server packs records, so a re-download can be compared with the manifest."""
    from obspy import UTCDateTime, read
    st = read(str(path))
    st.merge(method=-1)
    t0, t1 = UTCDateTime(START), UTCDateTime(END)
    h, segments = hashlib.sha256(), []
    for tr in sorted(st, key=lambda t: t.stats.starttime):
        tr = tr.slice(t0, t1 - tr.stats.delta)
        if tr.stats.npts == 0:
            continue
        h.update(str(tr.stats.starttime).encode())
        h.update(tr.data.astype("<i4").tobytes())
        segments.append([str(tr.stats.starttime), int(tr.stats.npts)])
    return {"segments": segments, "samples": sum(n for _, n in segments), "sha256": h.hexdigest()}


def fetch(data):
    from obspy import UTCDateTime
    from obspy.clients.fdsn import Client
    client = None
    for st in STATIONS:
        path = path_for(data, st)
        if path.exists():
            continue
        client = client or Client(GEOFON)
        print(f"fetching {NETWORK}.{st}.{CHANNEL}, 11-14 March 2025, from GEOFON", flush=True)
        tmp = path.with_suffix(".part")
        client.get_waveforms(NETWORK, st, "", CHANNEL, UTCDateTime(START), UTCDateTime(END), filename=str(tmp))
        tmp.rename(path)


def check(data):
    manifest = json.loads((HERE / "manifest.json").read_text())
    ok = True
    for st in STATIONS:
        got = sample_digest(path_for(data, st))
        want = manifest["stations"][st]
        if got["sha256"] != want["sha256"]:
            ok = False
            print(f"note: {st} samples differ from manifest.json ({got['samples']} samples, expected "
                  f"{want['samples']}); results will differ from the README", flush=True)
    if ok:
        print("samples match manifest.json", flush=True)


def imported(conn):
    if conn.execute("SELECT to_regclass(%s)", (COLLECTION + "_envelopes",)).fetchone()[0] is None:
        return False
    return conn.execute(f"SELECT count(*) FROM {COLLECTION}_envelopes").fetchone()[0] > 0


def grant_reader(conn):
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,)).fetchone():
        conn.execute(f"CREATE ROLE {READER} LOGIN PASSWORD '{READER_PASSWORD}'")
    conn.execute(f"GRANT USAGE ON SCHEMA public TO {READER}")
    conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}")
    conn.commit()


def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    data.mkdir(parents=True, exist_ok=True)
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        conn.commit()
        done = imported(conn)
    if done:
        print(f"{COLLECTION} is already loaded", flush=True)
    else:
        fetch(data)
        check(data)
        files = [str(path_for(data, st)) for st in STATIONS]
        subprocess.run([sys.executable, str(ROOT / "tools/ingest/morlet_import.py"), *files,
                        "--collection", COLLECTION, "--create"], check=True)
    with psycopg.connect(dsn) as conn:
        grant_reader(conn)
        n, windows = conn.execute(
            f"SELECT (SELECT count(*) FROM {COLLECTION}), (SELECT count(*) FROM {COLLECTION}_envelopes)").fetchone()
    print(f"\nready: {n} recordings, {windows} search windows in {COLLECTION}; "
          f"the MCP server connects as {READER} (read only)", flush=True)
    print("next: open Claude Code in examples/agent-demo and ask the question in README.md", flush=True)


if __name__ == "__main__":
    main()
